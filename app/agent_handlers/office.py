"""
agent_handlers/office.py
The ashiorid_office handlers (OB-21, build plan E2/E3/E6): the new office
message types, the office idle-tick hooks, and the small office_role hooks
the reused dev-team handlers call (Engineer = coder, Tester = tester,
Tech Lead = manager).

Every message handler here follows the same five steps:
  1. rank/protocol check   office.protocol.validate_message (a violation is
                           logged as a retake and the message is dropped)
  2. persona prompt        office.brief_stub.build_persona_prompt (E6 stub:
                           cast system_prompt + believed backstory + today's
                           directive), falling back to agent.system_prompt
  3. one LLM call          common._complete_with_emotion (a failure degrades
                           to a fallback line; the chain never stalls on it)
  4. lane action           git_client / gitea_client, every written path
                           checked with office.roles.lane_allows first
  5. next protocol message office.protocol builders, reply_ids(msg) so the
                           whole day's directive stays one correlation chain

The chain (docs/agent_handlers.md "Office handlers"):
  CEO directive -> Analyst functional_plan -> Tech Lead technical_plan (+
  task_assignment to the Engineer) -> Engineer commit + test_request ->
  Tester test_passed -> Tech Lead status_report -> CEO closes the issue.
  23:45 wrap_up (day runner) -> one status_report per seat to its superior;
  Sunday character_refresh (the v4 weekly-reset job, from "character-updater")
  -> clear state, check out loop/<payload.week>.
  Every spoken line goes out through _publish_line: the live transcript
  office_line + the v4 character_say (office/character_say.py).
  Lane/Engineer branches start from, and PRs target, the week trunk
  loop/<W> (_base_branch; agent.office.base_branch overrides).

Git and Gitea are opt-in per worker (`agent.office.workspace` /
`agent.office.gitea`); with neither configured the handlers still speak and
send, they just skip the lane action. Tokens are only ever referenced by
env-var NAME (gitea_client / git_client), never read or logged here.
"""
import logging
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import live_pane
from agent_state import write_state
from git_client import GitClient, GitError
from gitea_client import DEFAULT_TOKEN_ENV, GiteaClient, GiteaError
from message_bus import BROADCAST, build_message, correlation_of, reply_ids
from office import brief_stub
from office.clock import DEFAULT_EPOCH as DEFAULT_LOOP_EPOCH  # noqa: F401  (public alias)
from office.clock import DEFAULT_TZ, configured_epoch_and_tz
from office.protocol import (
    PHASES,
    ProtocolError,
    build_directive,
    build_functional_plan,
    build_status_report,
    build_technical_plan,
    build_test_request,
    validate_message,
)
from office.roles import REPORTS_TO, SEAT, OfficeRole, as_role, can_direct, lane_allows
from office.weekly_reset import CAMPAIGN as OFFICE_CAMPAIGN
from office.weekly_reset import (
    CHARACTER_REFRESH,
    REFRESH_SENDER,
    current_loop_branch,
    loop_branch,
)

from . import tester as tester_handlers
from .common import _complete_with_emotion

log = logging.getLogger(__name__)
TRACE = 5
logging.addLevelName(TRACE, "TRACE")

# ── defaults (build plan U5) ─────────────────────────────────────────────────
DEFAULT_GITEA_BASE_URL = "http://192.168.1.120:3300"
DEFAULT_GITEA_OWNER = "gitea_admin"
DEFAULT_GITEA_REPO = "fraud-stop"
OBSERVER_TOKEN_ENV = "GITEA_TOKEN_OBSERVER"
#: Fallback base when the week trunk can't be derived (bad epoch/tz). The
#: normal office base is the current week's `loop/<W>` (weekly_reset).
DEFAULT_BASE_BRANCH = "main"
#: agent.office.base_branch value (or unset) meaning "the current loop/<W>".
AUTO_BASE_BRANCH = "auto"
#: Every branch an office persona creates starts with this; the Office
#: Manager's garbage collection never touches a branch without it.
BRANCH_PREFIX = "office/"
DIRECTIVE_LABEL = "directive"

#: CEO -> these four, in this order (office_campaign_plan.md §2 "Directs").
DIRECTIVE_RECIPIENTS = (OfficeRole.TECH_LEAD, OfficeRole.ANALYST,
                        OfficeRole.MARKETING, OfficeRole.OFFICE_MANAGER)

#: Send-only bus type the Party Member's idle hook emits (never text).
OBSERVER_POSE = "observer_pose"
OBSERVER_POSE_NAME = "idle_watch"
DEFAULT_OBSERVER_EVERY_TICKS = 3

DEFAULT_CHORE_INTERVAL_S = 1800
CHORES = ("coffee", "cleanup", "garbage_collection")

_MAX_REMEMBERED_DIRECTIVES = 64
_TASK_LINE_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+?)\s*$", re.MULTILINE)


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


def _event(worker_id, event, level=logging.INFO, **fields):
    """One structured key=value line, both to the console (the agent loop
    doesn't configure logging, so docker logs only see prints — same as the
    sibling handlers) and to the module logger."""
    kv = " ".join(f"{k}={v}" for k, v in fields.items())
    line = f"event={event} {kv}".rstrip()
    prefix = {logging.ERROR: "ERROR ", logging.WARNING: "WARN "}.get(level, "")
    print(f"[agent:{worker_id}] {prefix}{line}")
    log.log(level, "office worker=%s %s", worker_id, line)


class LaneViolation(ValueError):
    """A persona tried to write outside its Fraud-Stop lane (build plan E3)."""


# ── config ───────────────────────────────────────────────────────────────────
def office_role_of(agent_config):
    """The worker's OfficeRole from `agent.office_role`, or None (not an office worker)."""
    value = (agent_config or {}).get("office_role")
    if not value:
        return None
    try:
        return as_role(value)
    except ValueError:
        log.error("office config bad office_role value=%r", value)
        return None


def office_settings(agent_config):
    """The `agent.office` block ({} when absent)."""
    settings = (agent_config or {}).get("office")
    return settings if isinstance(settings, dict) else {}


def build_gitea_client(agent_config):
    """GiteaClient for this worker, or None when `agent.office.gitea` is not
    configured (or `enabled: false`). The Party Member always gets a
    read-only client on GITEA_TOKEN_OBSERVER. Tests monkeypatch this name."""
    cfg = office_settings(agent_config).get("gitea")
    if not cfg or (isinstance(cfg, dict) and cfg.get("enabled") is False):
        _trace("build_gitea_client disabled")
        return None
    cfg = cfg if isinstance(cfg, dict) else {}
    read_only = bool(cfg.get("read_only")) or office_role_of(agent_config) is OfficeRole.PARTY_MEMBER
    token_env = cfg.get("token_env") or (OBSERVER_TOKEN_ENV if read_only else DEFAULT_TOKEN_ENV)
    return GiteaClient(
        cfg.get("base_url") or DEFAULT_GITEA_BASE_URL,
        cfg.get("owner") or DEFAULT_GITEA_OWNER,
        cfg.get("repo") or DEFAULT_GITEA_REPO,
        token_env=token_env,
        read_only=read_only,
    )


def build_git_client(agent_config, repo_path=None):
    """GitClient on `repo_path` (default `agent.office.workspace`), or None
    when there is no workspace. The Party Member never gets one. Tests
    monkeypatch this name."""
    settings = office_settings(agent_config)
    role = office_role_of(agent_config)
    path = repo_path or settings.get("workspace")
    if not path or role is OfficeRole.PARTY_MEMBER:
        _trace("build_git_client disabled path=%r role=%s", path, role)
        return None
    author = settings.get("author_name") or agent_config.get("name") or (role.value if role else "office")
    return GitClient(path, author, author_email=settings.get("author_email"),
                     remote_url=settings.get("remote_url"))


def _clock():
    """Now (tz-aware UTC). Tests monkeypatch this name to move the office week."""
    return datetime.now(timezone.utc)


def loop_epoch_and_tz(agent_config):
    """(epoch, tz) for the office week: agent.office.day_runner.epoch/tz, then
    agent.office.epoch/tz, then env OFFICE_EPOCH/OFFICE_TZ (the weekly_reset
    CLI's), then the defaults — the same precedence build_day_runner uses.
    Delegates to office.clock.configured_epoch_and_tz (one source)."""
    return configured_epoch_and_tz(agent_config)


def _base_branch(agent_config, now=None):
    """The branch office PRs target, Engineer/lane branches start from and the
    TL merges into: an explicit agent.office.base_branch wins; unset or
    "auto" -> the current week's trunk loop/<W> (weekly_reset.
    current_loop_branch, same epoch/tz as the day runner). A week that can't
    be derived falls back to DEFAULT_BASE_BRANCH (WARN)."""
    explicit = office_settings(agent_config).get("base_branch")
    if explicit and str(explicit).strip().lower() != AUTO_BASE_BRANCH:
        log.debug("office base_branch explicit branch=%s", explicit)
        return str(explicit)
    epoch, tz = loop_epoch_and_tz(agent_config)
    try:
        branch = current_loop_branch(now or _clock(), epoch, tz)
    except (ValueError, TypeError) as exc:
        log.warning("office base_branch auto failed, using fallback fallback=%s error=%s",
                    DEFAULT_BASE_BRANCH, exc)
        print(f"[agent] WARN event=base_branch_fallback branch={DEFAULT_BASE_BRANCH} error='{exc}'")
        return DEFAULT_BASE_BRANCH
    log.debug("office base_branch auto branch=%s epoch=%s tz=%s", branch, epoch, tz)
    return branch


def _merge_prs(agent_config):
    return office_settings(agent_config).get("merge_prs", True) is not False


def _today_iso():
    return datetime.now(ZoneInfo(DEFAULT_TZ)).date().isoformat()


# ── per-process office state ─────────────────────────────────────────────────
_directives = {}      # correlation_id -> directive dict
_today = None         # the most recent directive this process saw
_phase = None         # last phase_change phase
_chores = None        # ChoreScheduler (office manager), lazily built
_observer = {"tick": 0, "rotation": 0}
_day_runner = None    # OB-30 fills this via set_day_runner
_day_runner_autoinstall_done = False  # OB-30: agent.office.day_runner built at most once


def _reset_office_state():
    """Forget directives, phase, chore schedule, observer rotation and the
    day runner (tests / config reload)."""
    global _day_runner, _day_runner_autoinstall_done
    _refresh_office_state()
    _day_runner = None
    _day_runner_autoinstall_done = False


def _refresh_office_state():
    """character_refresh: forget the week's in-process character state
    (directives, phase, chore schedule, observer rotation). The installed
    day runner stays: it is the CEO's clock, not character memory, and its
    own state file already carries the day across the reset."""
    global _today, _phase, _chores
    _directives.clear()
    _today = _phase = _chores = None
    _observer.update(tick=0, rotation=0)


def remember_directive(correlation_id, text, title=None, issue=None, day=None):
    """Record today's directive under its chain id; returns the dict."""
    global _today
    directive = {"text": text, "title": title, "issue": issue, "day": day,
                 "correlation_id": correlation_id}
    if correlation_id:
        _directives[correlation_id] = directive
        while len(_directives) > _MAX_REMEMBERED_DIRECTIVES:
            _directives.pop(next(iter(_directives)))
    _today = directive
    log.debug("office directive remembered correlation_id=%s issue=%s", correlation_id, issue)
    return directive


def directive_for(msg):
    """The directive `msg` belongs to: payload fields first (directive /
    text + title + issue), then this process's memory of the chain, then
    the latest directive seen; None when nothing is known."""
    payload = (msg or {}).get("payload") or {}
    remembered = _directives.get(correlation_of(msg)) or {}
    text = payload.get("directive") or remembered.get("text")
    if not text and (msg or {}).get("type") == "directive":
        text = payload.get("text")
    if not text:
        return dict(_today) if _today else None
    return {"text": text,
            "title": payload.get("title") or remembered.get("title"),
            "issue": payload.get("issue") if payload.get("issue") is not None else remembered.get("issue"),
            "day": payload.get("day") or remembered.get("day"),
            "correlation_id": correlation_of(msg)}


def current_phase():
    """The last phase announced by phase_change in this process (or None)."""
    return _phase


# ── shared steps ─────────────────────────────────────────────────────────────
def _accept(worker_id, agent_config, msg, allowed, handler):
    """Steps 1: office role filter + protocol/rank check. Returns the
    worker's OfficeRole, or None when the message must be ignored."""
    role = office_role_of(agent_config)
    if role is None or role not in allowed:
        print(f"[agent:{worker_id}] ignoring {msg.get('type')} "
              f"(office_role={role.value if role else None}, handler={handler})")
        return None
    try:
        validate_message(msg)
    except ProtocolError as exc:
        _event(worker_id, "rank_violation", logging.ERROR, handler=handler,
               type=msg.get("type"), sender=msg.get("from"), to=msg.get("to"),
               correlation_id=correlation_of(msg), outcome="retake", error=f"'{exc}'")
        return None
    _event(worker_id, "office_accept", logging.DEBUG, handler=handler, role=role.value,
           sender=msg.get("from"), correlation_id=correlation_of(msg))
    return role


def persona_prompt(agent_config, directive=None):
    """Step 2: the E6 stub brief for this worker, or agent.system_prompt when
    the cast file can't be read (WARN)."""
    role = office_role_of(agent_config)
    settings = office_settings(agent_config)
    try:
        return brief_stub.build_persona_prompt(
            role, directive, pack_dir=settings.get("pack_dir"),
            max_backstory_chars=settings.get("max_backstory_chars"))
    except (brief_stub.BriefError, ValueError) as exc:
        log.warning("office persona fallback role=%s error=%s", role.value if role else None, exc)
        print(f"[agent] WARN event=persona_fallback role={role.value if role else None} error='{exc}'")
        return agent_config.get("system_prompt", "")


def _speak(worker_id, llm_client, persona, prompt, fallback, correlation_id):
    """Step 3: one structured LLM call. Returns (line, emotion, ok); a failure
    or an empty line yields (fallback, "neutral", False)."""
    log.debug("office llm call event=enter worker=%s correlation_id=%s", worker_id, correlation_id)
    try:
        line, emotion = _complete_with_emotion(llm_client, persona, prompt)
    except Exception as exc:
        _event(worker_id, "llm_failed", logging.ERROR, correlation_id=correlation_id,
               error=f"'{exc}'")
        return fallback, "neutral", False
    if not (line or "").strip():
        _event(worker_id, "llm_empty_line", logging.WARNING, correlation_id=correlation_id)
        return fallback, emotion, False
    log.debug("office llm call event=exit worker=%s chars=%d", worker_id, len(line))
    return line.strip(), emotion, True


def _publish_line(worker_id, agent_config, producer, line, emotion=None, correlation_id=None,
                  *, to=None, addressees=None):
    """Publish a line this seat just spoke: the live transcript `office_line`
    and the v4 `character_say`, from one call (live_pane.publish_office_line)
    so the two never diverge. `addressees`: seat ids of the line's
    recipients (default `to`; [] for a line to the room). The scene_id
    instant is this module's _clock(). Never raises."""
    return live_pane.publish_office_line(worker_id, agent_config, producer, line, emotion,
                                         correlation_id, to=to, addressees=addressees,
                                         now=_clock())


def _with_extra(msg, extra):
    """Add non-None `extra` keys to a built office message's payload and
    re-validate (the protocol ignores unknown payload keys, so chain
    context like pr / issue / task can ride along)."""
    for key, value in (extra or {}).items():
        if value is not None:
            msg["payload"][key] = value
    return validate_message(msg)


def _slug(text, fallback="directive"):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40].strip("-")
    return slug or fallback


def _title_of(directive):
    if not directive:
        return "Untitled directive"
    if directive.get("title"):
        return str(directive["title"])
    first = re.split(r"(?<=[.!?])\s", (directive.get("text") or "").strip(), maxsplit=1)[0]
    return (first[:60].rstrip() or "Untitled directive")


def _branch_name(role, title, correlation_id):
    return f"{BRANCH_PREFIX}{role.value}/{_slug(title)}-{(correlation_id or 'nochain')[:8]}"


def extract_tasks(plan, fallback):
    """Bullet / numbered lines of `plan` as a task list (max 4); [fallback]
    when the plan has none."""
    tasks = [t for t in _TASK_LINE_RE.findall(plan or "") if t.strip()][:4]
    return tasks or [fallback]


# ── step 4: lane actions ─────────────────────────────────────────────────────
def _checkout_branch(git, branch, base):
    """Create `branch` from `base` (or re-use it on a retry). Falls back to
    branching off the current HEAD when `base` is unknown locally. Returns
    the HEAD sha the branch starts from."""
    try:
        return git.checkout_new_branch(branch, base)
    except GitError:
        log.debug("office git branch exists or base unknown branch=%s base=%s", branch, base)
    try:
        return git.checkout(branch)
    except GitError:
        log.warning("office git base unknown, branching from HEAD branch=%s base=%s", branch, base)
        return git.checkout_new_branch(branch)


def _find_or_open_pr(gitea, head, base, title, body):
    """The open PR for `head`, opening one if none exists (a bug-fix retry
    pushes to the same branch, so its PR already exists). Returns its number."""
    for pr in gitea.list_prs("open"):
        if ((pr.get("head") or {}).get("ref")) == head:
            log.debug("office gitea reuse pr number=%s head=%s", pr.get("number"), head)
            return pr.get("number")
    return (gitea.open_pr(head=head, base=base, title=title, body=body) or {}).get("number")


def lane_commit(role, git, gitea, *, edits, branch, base, title, body, message):
    """Write `edits` ({repo path: text | callable(old_text_or_None) -> text})
    on `branch`, commit, push, and open a PR. Every path is checked with
    lane_allows first (LaneViolation, nothing written). Returns
    {"branch", "commit", "pushed", "pr"}, or None when git is not configured.
    The workspace is switched back to `base` afterwards."""
    _trace("lane_commit enter role=%s branch=%s paths=%s", role.value, branch, list(edits))
    bad = [p for p in edits if not lane_allows(role, p)]
    if bad:
        log.error("office lane violation role=%s paths=%s", role.value, bad)
        raise LaneViolation(f"{role.value} may not write {bad}")
    if git is None:
        log.debug("office lane_commit skipped reason=no_workspace role=%s", role.value)
        return None
    _checkout_branch(git, branch, base)
    try:
        for path, edit in edits.items():
            full = Path(git.repo_path) / path
            old = full.read_text(encoding="utf-8") if full.exists() else None
            new = edit(old) if callable(edit) else edit
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(new, encoding="utf-8")
            log.debug("office lane write path=%s chars=%d", path, len(new))
        sha = git.commit_all(message)
        pushed = bool(sha) and git.push_branch(branch)
        pr = None
        if pushed and gitea is not None:
            pr = _find_or_open_pr(gitea, branch, base, title, body)
    finally:
        try:
            git.checkout(base)
        except GitError as exc:
            log.warning("office git could not return to base base=%s error=%s", base, exc)
    result = {"branch": branch, "commit": sha, "pushed": pushed, "pr": pr}
    log.info("office lane_commit done role=%s branch=%s commit=%s pushed=%s pr=%s",
             role.value, branch, sha, pushed, pr)
    return result


def _lane_action(worker_id, role, agent_config, correlation_id, **kwargs):
    """lane_commit with this worker's clients; any git/Gitea/lane failure is
    logged (ERROR) and returns None so the protocol message still goes out."""
    try:
        return lane_commit(role, build_git_client(agent_config), build_gitea_client(agent_config),
                           base=_base_branch(agent_config), **kwargs)
    except (LaneViolation, GitError, GiteaError, OSError) as exc:
        _event(worker_id, "lane_action_failed", logging.ERROR, role=role.value,
               correlation_id=correlation_id, error=f"'{exc}'")
        return None


def _gitea_call(worker_id, correlation_id, what, fn, *args, **kwargs):
    """Run one Gitea call; log and swallow failures (returns None)."""
    try:
        result = fn(*args, **kwargs)
    except (GiteaError, ValueError, OSError) as exc:
        _event(worker_id, "gitea_failed", logging.ERROR, op=what,
               correlation_id=correlation_id, error=f"'{exc}'")
        return None
    log.debug("office gitea done op=%s correlation_id=%s", what, correlation_id)
    return result if result is not None else True


def _review_and_merge(worker_id, agent_config, gitea, pr, body, correlation_id):
    """Tech Lead review: a COMMENT review (Gitea 422s APPROVE on a PR opened
    with the same shared token — OB-23), then merge when agent.office.merge_prs
    (default true). Returns True when merged."""
    if gitea is None or not pr:
        return False
    _gitea_call(worker_id, correlation_id, "review_pr", gitea.review_pr, pr, "COMMENT", body)
    if not _merge_prs(agent_config):
        return False
    return bool(_gitea_call(worker_id, correlation_id, "merge_pr", gitea.merge_pr, pr))


def _state(state_path, expression, **kwargs):
    if state_path:
        write_state(state_path, expression, **kwargs)


# ── CEO: issuing the day's directive (called by the day runner) ─────────────
def issue_directive(worker_id, agent_config, llm_client, producer, text, *, title=None,
                    day=None, recipients=DIRECTIVE_RECIPIENTS, state_path=None):
    """CEO: open the directive issue (CEO lane: issues), announce it, and send
    one `directive` to each recipient. The first directive starts the day's
    chain; the others join it (correlation_id = the first's id, causation_id
    = the first's id). Returns the sent messages ([] when not the CEO)."""
    _trace("issue_directive enter worker=%s recipients=%s", worker_id, recipients)
    if office_role_of(agent_config) is not OfficeRole.CEO:
        _event(worker_id, "issue_directive_refused", logging.ERROR, reason="not_ceo")
        return []
    day = day or _today_iso()
    title = title or _title_of({"text": text})
    directive = {"text": text, "title": title, "day": day}

    gitea = build_gitea_client(agent_config)
    issue = None
    if gitea is not None:
        created = _gitea_call(worker_id, None, "open_issue", gitea.open_issue,
                              title, text, labels=[DIRECTIVE_LABEL])
        issue = created.get("number") if isinstance(created, dict) else None
        directive["issue"] = issue

    persona = persona_prompt(agent_config, directive)
    line, emotion, _ = _speak(
        worker_id, llm_client, persona,
        f"It's {day}. You are giving the team today's directive: {text}\n\n"
        "Announce it to the office in 1-3 sentences, in character.",
        f"Today's directive: {title}.", None)

    sent, root = [], None
    for recipient in recipients:
        ids = {} if root is None else {"correlation_id": root["correlation_id"],
                                       "causation_id": root["id"]}
        msg = build_directive(worker_id, recipient, text, issue=issue, title=title, day=day, **ids)
        root = root or msg
        producer.send(msg)
        sent.append(msg)
    remember_directive(root["correlation_id"], text, title=title, issue=issue, day=day)
    _state(state_path, "speaking", action=f"directive: {title}", bubble=line, emotion=emotion)
    _publish_line(worker_id, agent_config, producer, line, emotion, root["correlation_id"],
                  addressees=[SEAT[r] for r in recipients])
    _event(worker_id, "directive_issued", issue=issue, recipients=len(sent),
           correlation_id=root["correlation_id"])
    return sent


# ── message handlers ─────────────────────────────────────────────────────────
DIRECTIVE_ROLES = frozenset({OfficeRole.ANALYST, OfficeRole.TECH_LEAD,
                             OfficeRole.MARKETING, OfficeRole.OFFICE_MANAGER})


def handle_directive(worker_id, agent_config, llm_client, producer, msg,
                     state_path=None, coding_backend=None):
    """Analyst: functional plan (docs/requirements/ PR) -> functional_plan to
    the Tech Lead. Tech Lead: acknowledge and wait for the functional plan.
    Marketing: copy (marketing/ PR) -> status_report to the CEO. Office
    Manager: CHANGELOG.md entry PR -> status_report to the CEO."""
    role = _accept(worker_id, agent_config, msg, DIRECTIVE_ROLES, "directive")
    if role is None:
        return
    payload = msg["payload"]
    cid = correlation_of(msg)
    directive = remember_directive(cid, payload["text"], title=payload.get("title"),
                                   issue=payload.get("issue"), day=payload.get("day"))
    title, day = _title_of(directive), directive["day"] or _today_iso()
    persona = persona_prompt(agent_config, directive)
    _state(state_path, "thinking", action=f"reading directive: {title}")

    if role is OfficeRole.TECH_LEAD:
        line, emotion, _ = _speak(
            worker_id, llm_client, persona,
            f"The CEO just issued today's directive: {payload['text']}\n\n"
            "The Analyst will send you the functional plan. Acknowledge the directive "
            "in 1-2 sentences, in character.",
            f"Noted: {title}. Waiting on the functional plan.", cid)
        _state(state_path, "speaking", action=f"acknowledged: {title}", bubble=line, emotion=emotion)
        _publish_line(worker_id, agent_config, producer, line, emotion, cid,
                      addressees=[msg.get("from")])
        _event(worker_id, "directive_acknowledged", role=role.value, correlation_id=cid)
        return

    if role is OfficeRole.ANALYST:
        plan, emotion, _ = _speak(
            worker_id, llm_client, persona,
            f"The CEO's directive: {payload['text']}\n\nWrite the functional plan: what "
            "Fraud-Stop must do and for whom, as 3-6 short requirement lines each starting "
            "with '- '. Put the whole plan in \"line\".",
            f"- Fraud-Stop must deliver: {payload['text']}", cid)
        doc = f"# Requirements: {title}\n\nDirective ({day}): {payload['text']}\n\n## Functional plan\n\n{plan}\n"
        lane = _lane_action(
            worker_id, role, agent_config, cid,
            edits={f"docs/requirements/{day}-{_slug(title)}.md": doc},
            branch=_branch_name(role, title, cid),
            title=f"Requirements: {title}", body=plan,
            message=f"docs(requirements): {title}"[:72])
        out = build_functional_plan(plan, sender=worker_id, directive_id=cid, reply_to=msg)
        out = _with_extra(out, {"directive": payload["text"], "title": title,
                                "issue": directive["issue"], "day": day,
                                "pr": (lane or {}).get("pr"), "branch": (lane or {}).get("branch")})
        producer.send(out)
        _state(state_path, "speaking", action=f"functional plan: {title}", bubble=plan, emotion=emotion)
        _publish_line(worker_id, agent_config, producer, plan, emotion, cid, addressees=[out["to"]])
        _event(worker_id, "functional_plan_sent", pr=(lane or {}).get("pr"), correlation_id=cid)
        return

    # Marketing / Office Manager: one lane artifact, then report up to the CEO.
    if role is OfficeRole.MARKETING:
        line, emotion, _ = _speak(
            worker_id, llm_client, persona,
            f"The CEO's directive: {payload['text']}\n\nWrite 2-4 sentences of landing-page "
            "copy selling this to banks, in your voice. Put the copy in \"line\".",
            f"Fraud-Stop: {title}.", cid)
        path = f"marketing/{day}-{_slug(title)}.md"
        edits = {path: f"# {title}\n\n{line}\n"}
        pr_title, message = f"Marketing: {title}", f"docs(marketing): {title}"[:72]
    else:
        line, emotion, _ = _speak(
            worker_id, llm_client, persona,
            f"The CEO's directive: {payload['text']}\n\nWrite one CHANGELOG line announcing "
            "that this work has started. Put only that line in \"line\".",
            f"Started: {title}.", cid)
        entry = f"- {day}: {line}"
        edits = {"CHANGELOG.md": lambda old: _append_changelog(old, entry)}
        pr_title, message = f"Changelog: {title}", f"chore(changelog): {title}"[:72]
    lane = _lane_action(worker_id, role, agent_config, cid, edits=edits,
                        branch=_branch_name(role, title, cid), title=pr_title,
                        body=line, message=message)
    report = build_status_report(worker_id, line, status="on_track", day=day, reply_to=msg)
    report = _with_extra(report, {"issue": directive["issue"], "directive_id": cid,
                                  "pr": (lane or {}).get("pr")})
    producer.send(report)
    _state(state_path, "speaking", action=f"reported: {title}", bubble=line, emotion=emotion)
    _publish_line(worker_id, agent_config, producer, line, emotion, cid, addressees=[report["to"]])
    _event(worker_id, "status_report_sent", role=role.value, pr=(lane or {}).get("pr"),
           correlation_id=cid)


def _append_changelog(old, entry):
    if not old:
        return f"# Changelog\n\n{entry}\n"
    return old.rstrip("\n") + f"\n{entry}\n"


def handle_functional_plan(worker_id, agent_config, llm_client, producer, msg,
                           state_path=None, coding_backend=None):
    """Tech Lead: combine the directive + functional plan into a technical
    plan (docs/design/ PR), COMMENT-review and merge the Analyst's PR, send
    technical_plan to the CEO and one task_assignment to the Engineer."""
    role = _accept(worker_id, agent_config, msg, {OfficeRole.TECH_LEAD}, "functional_plan")
    if role is None:
        return
    payload = msg["payload"]
    cid = correlation_of(msg)
    directive = directive_for(msg) or {"text": payload["plan"]}
    title, day = _title_of(directive), directive.get("day") or _today_iso()
    persona = persona_prompt(agent_config, directive)
    _state(state_path, "thinking", action=f"technical plan: {title}")

    fallback_task = f"Implement {title} in src/"
    plan, emotion, _ = _speak(
        worker_id, llm_client, persona,
        f"Directive: {directive.get('text')}\n\nThe Analyst's functional plan:\n{payload['plan']}\n\n"
        "Combine them into a technical plan: 1-4 engineering tasks for the Engineer, each on "
        "its own line starting with '- ', naming the src/ modules to change. Put the whole "
        "plan in \"line\".",
        f"- {fallback_task}", cid)
    tasks = extract_tasks(plan, fallback_task)

    gitea = build_gitea_client(agent_config)
    merged = _review_and_merge(worker_id, agent_config, gitea, payload.get("pr"),
                               f"Tech Lead review: folded into the technical plan.\n\n{plan}", cid)
    doc = (f"# Design: {title}\n\n## Functional plan\n\n{payload['plan']}\n\n"
           f"## Technical plan\n\n{plan}\n")
    lane = _lane_action(worker_id, role, agent_config, cid,
                        edits={f"docs/design/{day}-{_slug(title)}.md": doc},
                        branch=_branch_name(role, title, cid),
                        title=f"Design: {title}", body=plan,
                        message=f"docs(design): {title}"[:72])

    tech = build_technical_plan(plan, sender=worker_id, tasks=tasks, directive_id=cid, reply_to=msg)
    tech = _with_extra(tech, {"directive": directive.get("text"), "title": title,
                              "issue": directive.get("issue"), "day": day,
                              "pr": (lane or {}).get("pr"), "requirements_merged": merged})
    producer.send(tech)

    engineer = SEAT[OfficeRole.ENGINEER]
    told = [tech["to"]]
    if not can_direct(worker_id, engineer):
        _event(worker_id, "rank_violation", logging.ERROR, type="task_assignment",
               to=engineer, correlation_id=cid, outcome="not_sent")
    else:
        task_text = f"{title}\n" + "\n".join(f"- {t}" for t in tasks)
        producer.send(build_message(
            worker_id, engineer, "task_assignment",
            {"task": task_text, "retry_count": 0, "directive_id": cid, "title": title,
             "issue": directive.get("issue"), "directive": directive.get("text")},
            **reply_ids(tech)))
        told.append(engineer)
    _state(state_path, "speaking", action=f"delegated: {title}", bubble=plan, emotion=emotion)
    _publish_line(worker_id, agent_config, producer, plan, emotion, cid, addressees=told)
    _event(worker_id, "technical_plan_sent", tasks=len(tasks), requirements_merged=merged,
           correlation_id=cid)


def handle_technical_plan(worker_id, agent_config, llm_client, producer, msg,
                          state_path=None, coding_backend=None):
    """CEO: acknowledge the Tech Lead's plan and note it on the directive
    issue. Sends nothing (the Tech Lead has already delegated)."""
    role = _accept(worker_id, agent_config, msg, {OfficeRole.CEO}, "technical_plan")
    if role is None:
        return
    payload = msg["payload"]
    cid = correlation_of(msg)
    directive = directive_for(msg)
    persona = persona_prompt(agent_config, directive)
    line, emotion, _ = _speak(
        worker_id, llm_client, persona,
        f"The Tech Lead sent you the technical plan:\n{payload['plan']}\n\n"
        "Acknowledge it in 1-2 sentences, in character.",
        "Plan received. Proceed.", cid)
    issue = payload.get("issue") or (directive or {}).get("issue")
    gitea = build_gitea_client(agent_config)
    if gitea is not None and issue:
        _gitea_call(worker_id, cid, "comment_issue", gitea.comment_issue, issue,
                    f"Technical plan acknowledged.\n\n{payload['plan']}")
    _state(state_path, "speaking", action="acknowledged the technical plan", bubble=line,
           emotion=emotion)
    _publish_line(worker_id, agent_config, producer, line, emotion, cid, addressees=[msg.get("from")])
    _event(worker_id, "technical_plan_acknowledged", issue=issue, correlation_id=cid)


def handle_test_request(worker_id, agent_config, llm_client, producer, msg,
                        state_path=None, coding_backend=None):
    """Tester: run the reused tester flow (tester._run_tests_and_report)
    with the persona prompt, reporting test_passed / bug_report to the Tech
    Lead, then post the CI result as a comment on the Engineer's PR."""
    role = _accept(worker_id, agent_config, msg, {OfficeRole.TESTER}, "test_request")
    if role is None:
        return
    payload = msg["payload"]
    cid = correlation_of(msg)
    # The reused flow reads payload.task; a Tech-Lead-originated request may
    # only carry the protocol's required `description`.
    run_msg = {**msg, "payload": {"task": payload["description"], **payload}}
    config = {**agent_config, "system_prompt": persona_prompt(agent_config, directive_for(msg))}
    extra = {k: payload.get(k) for k in ("pr", "branch", "commit", "directive_id", "issue", "title")}
    sent = tester_handlers._run_tests_and_report(
        worker_id, config, llm_client, producer, run_msg, state_path,
        report_to=SEAT[OfficeRole.TECH_LEAD], extra=extra)
    verdict = (sent or {}).get("type")
    _publish_line(worker_id, agent_config, producer,
                  ((sent or {}).get("payload") or {}).get("narration"), None, cid,
                  addressees=[(sent or {}).get("to")] if (sent or {}).get("to") else [])
    gitea = build_gitea_client(agent_config)
    if gitea is not None and payload.get("pr") and verdict in ("test_passed", "bug_report"):
        body = ("CI: all tests passed." if verdict == "test_passed" else
                f"CI: tests failed ({sent['payload'].get('severity')}). {sent['payload'].get('repro')}")
        _gitea_call(worker_id, cid, "comment_issue", gitea.comment_issue, payload["pr"], body)
    _event(worker_id, "test_request_done", verdict=verdict, pr=payload.get("pr"), correlation_id=cid)


def handle_status_report(worker_id, agent_config, llm_client, producer, msg,
                         state_path=None, coding_backend=None):
    """CEO / Tech Lead: acknowledge a report from a direct report. The CEO
    also closes the directive issue when the Tech Lead reports it done.
    Terminal: sends nothing."""
    role = _accept(worker_id, agent_config, msg, {OfficeRole.CEO, OfficeRole.TECH_LEAD},
                   "status_report")
    if role is None:
        return
    payload = msg["payload"]
    cid = correlation_of(msg)
    directive = directive_for(msg)
    persona = persona_prompt(agent_config, directive)
    line, emotion, _ = _speak(
        worker_id, llm_client, persona,
        f"{msg['from']} reports ({payload.get('status') or 'update'}): {payload['summary']}\n\n"
        "React in 1-2 sentences, in character.",
        "Thanks, noted.", cid)
    closed = False
    sender_role = as_role(msg["from"])
    issue = payload.get("issue") or (directive or {}).get("issue")
    # An end-of-day wrap-up report (handle_wrap_up) only summarises: the issue
    # was already closed (or carried over) by the directive chain itself.
    wrap_up = payload.get("report") == WRAP_UP_REPORT
    if (role is OfficeRole.CEO and sender_role is OfficeRole.TECH_LEAD and not wrap_up
            and payload.get("status") == "done" and issue):
        gitea = build_gitea_client(agent_config)
        if gitea is not None:
            _gitea_call(worker_id, cid, "comment_issue", gitea.comment_issue, issue,
                        f"Done, per the Tech Lead: {payload['summary']}")
            closed = bool(_gitea_call(worker_id, cid, "close_issue", gitea.close_issue, issue))
    _state(state_path, "happy" if payload.get("status") == "done" else "speaking",
           action=f"report from {msg['from']}", bubble=line, emotion=emotion)
    _publish_line(worker_id, agent_config, producer, line, emotion, cid, addressees=[msg["from"]])
    _event(worker_id, "status_report_acknowledged", sender=msg["from"],
           status=payload.get("status"), wrap_up=wrap_up, issue_closed=closed,
           correlation_id=cid)


#: payload.report marker on a status_report sent in answer to wrap_up.
WRAP_UP_REPORT = "wrap_up"


def _wrap_up_status(directives):
    """status_report.status for the wrap-up: "done" when every directive of
    the day is done, "on_track" otherwise (and with no directive at all)."""
    statuses = [(d or {}).get("status") for d in directives or [] if isinstance(d, dict)]
    return "done" if statuses and all(s == "done" for s in statuses) else "on_track"


def handle_wrap_up(worker_id, agent_config, llm_client, producer, msg,
                   state_path=None, coding_backend=None):
    """Everyone (23:45 day-runner broadcast): each seat with a superior sends
    it ONE end-of-day status_report — Analyst / Marketing / Office Manager /
    Tech Lead to the CEO, Engineer / Tester to the Tech Lead (office.roles
    REPORTS_TO; build_status_report enforces the rank rule). The CEO and
    the Party Member stay quiet. One LLM line each, via the persona brief,
    published to the live transcript. Returns the sent report or None."""
    role = _accept(worker_id, agent_config, msg, set(OfficeRole), "wrap_up")
    if role is None:
        return None
    payload = msg["payload"]
    cid = correlation_of(msg)
    day = payload["day"]
    superior = REPORTS_TO.get(role)
    if superior is None:
        # CEO (the sender) and Party Member (never speaks, U6): nothing to report.
        _event(worker_id, "wrap_up_noted", role=role.value, reported=False, correlation_id=cid)
        return None
    directives = [d for d in payload.get("directives") or [] if isinstance(d, dict)]
    done = sum(d.get("status") == "done" for d in directives)
    titles = "; ".join(f"{d.get('title') or 'untitled'} ({d.get('status') or 'unknown'})"
                       for d in directives) or "no directive today"
    status = _wrap_up_status(directives)
    persona = persona_prompt(agent_config, _today)
    _state(state_path, "thinking", action="end-of-day report")
    line, emotion, _ = _speak(
        worker_id, llm_client, persona,
        f"It's 23:45 on {day}: the CEO wants end-of-day status reports. Today's directives: "
        f"{titles}. Give {superior.value.replace('_', ' ')} your status report from your own "
        "desk in 1-2 sentences, in character.",
        f"End of day {day}: {done} of {len(directives)} directive(s) done on my side.", cid)
    try:
        report = build_status_report(worker_id, line, status=status, day=day, reply_to=msg)
        report = _with_extra(report, {"report": WRAP_UP_REPORT,
                                      "directives_done": done, "directives": len(directives)})
    except ProtocolError as exc:
        _event(worker_id, "status_report_failed", logging.ERROR, handler="wrap_up",
               correlation_id=cid, error=f"'{exc}'")
        return None
    producer.send(report)
    _state(state_path, "speaking", action="end-of-day report", bubble=line, emotion=emotion)
    _publish_line(worker_id, agent_config, producer, line, emotion, cid, to=report["to"])
    _event(worker_id, "wrap_up_report_sent", role=role.value, to=report["to"], status=status,
           correlation_id=cid)
    return report


#: character_refresh reasons that open a new week (and so switch the week
#: trunk). revert / testctl refreshes only clear the in-process state.
WEEK_RESET_REASONS = frozenset({"weekly_reset"})


def _refresh_branch(payload):
    """The week trunk a character_refresh points at: loop_branch(payload.week)
    (the v4 payload has no branch); a legacy payload.branch is used only
    when the week is missing or invalid. None when neither is usable."""
    week = payload.get("week")
    try:
        return loop_branch(week)
    except ValueError:
        log.debug("office character_refresh bad week week=%r", week)
    legacy = str(payload.get("branch") or "").strip()
    return legacy or None


def handle_character_refresh(worker_id, agent_config, llm_client, producer, msg,
                             state_path=None, coding_backend=None):
    """Everyone (the v4 weekly-reset job's broadcast, from "character-updater",
    payload {campaign, week, characters, reason}): office seats forget the
    closing week's in-process state and, on a weekly_reset with their own
    Fraud-Stop workspace, fetch and check out the new week trunk
    loop_branch(payload.week). The Tester (read-only mount of the Engineer's
    clone) and seats without a workspace skip git. Another sender or a
    non-broadcast is a rank violation; another campaign, or a `characters`
    list without this seat's slug (or "*"), is ignored. Non-office workers
    ignore it. Returns {"refreshed", "branch", "checked_out"} or None."""
    role = office_role_of(agent_config)
    if role is None:
        print(f"[agent:{worker_id}] ignoring {CHARACTER_REFRESH} (not an office worker)")
        return None
    cid = correlation_of(msg)
    if msg.get("from") != REFRESH_SENDER or msg.get("to") != BROADCAST:
        _event(worker_id, "rank_violation", logging.ERROR, handler=CHARACTER_REFRESH,
               sender=msg.get("from"), to=msg.get("to"), correlation_id=cid, outcome="dropped")
        return None
    payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
    if payload.get("campaign") != OFFICE_CAMPAIGN:
        _event(worker_id, "character_refresh_skipped", reason="other_campaign",
               campaign=payload.get("campaign"), correlation_id=cid)
        return None
    characters = payload.get("characters") or ["*"]
    if isinstance(characters, str):
        characters = [characters]
    if "*" not in characters and role.value not in characters and worker_id not in characters:
        _event(worker_id, "character_refresh_skipped", reason="not_addressed", correlation_id=cid)
        return None
    _refresh_office_state()
    reason = payload.get("reason") or "weekly_reset"
    branch = _refresh_branch(payload) if reason in WEEK_RESET_REASONS else None
    checked_out = False
    if reason not in WEEK_RESET_REASONS:
        log.debug("office character_refresh git skipped reason=%s", reason)
    elif branch is None:
        _event(worker_id, "character_refresh_no_branch", logging.WARNING,
               week=payload.get("week"), correlation_id=cid)
    elif role is OfficeRole.TESTER:
        log.debug("office character_refresh git skipped role=tester reason=read_only_mount")
    else:
        git = build_git_client(agent_config, repo_path=getattr(coding_backend, "workspace", None)
                               if role is OfficeRole.ENGINEER else None)
        if git is None:
            log.debug("office character_refresh git skipped role=%s reason=no_workspace", role.value)
        else:
            checked_out = _checkout_week_branch(worker_id, git, branch, cid)
    _state(state_path, "idle", action=f"new week {payload.get('week')}", bubble=None)
    _event(worker_id, "character_refreshed", role=role.value, week=payload.get("week"),
           reason=reason, branch=branch, checked_out=checked_out, correlation_id=cid)
    return {"refreshed": True, "branch": branch, "checked_out": checked_out}


def _checkout_week_branch(worker_id, git, branch, correlation_id):
    """Fetch (the remote now has the fresh loop/<W>) and switch to `branch`
    (git DWIMs a tracking branch from origin). Returns True on success;
    failures are logged (never the remote URL / token) and return False."""
    try:
        log.debug("office git call op=fetch branch=%s", branch)
        fetched = git.fetch(tags=True, prune=True)
        if not fetched:
            _event(worker_id, "week_branch_fetch_failed", logging.WARNING, branch=branch,
                   correlation_id=correlation_id)
        log.debug("office git call op=checkout branch=%s", branch)
        git.checkout(branch)
    except (GitError, OSError) as exc:
        _event(worker_id, "week_branch_checkout_failed", logging.ERROR, branch=branch,
               correlation_id=correlation_id, error=f"'{exc}'")
        return False
    return True


def handle_phase_change(worker_id, agent_config, llm_client, producer, msg,
                        state_path=None, coding_backend=None):
    """Everyone: note the new day phase. Speaking roles react in one line
    (skipped with agent.office.narrate_phase_change: false); the Party
    Member only changes pose — never an LLM call, never a bubble."""
    role = _accept(worker_id, agent_config, msg, set(OfficeRole), "phase_change")
    if role is None:
        return
    global _phase
    phase = msg["payload"]["phase"]
    _phase = phase
    cid = correlation_of(msg)
    expression = "idle" if phase == PHASES[0] else "focused"
    if role is OfficeRole.PARTY_MEMBER or office_settings(agent_config).get("narrate_phase_change") is False:
        _state(state_path, "idle", action=f"phase: {phase}", bubble=None)
        _event(worker_id, "phase_noted", phase=phase, narrated=False, correlation_id=cid)
        return
    persona = persona_prompt(agent_config, directive_for(msg))
    line, emotion, ok = _speak(
        worker_id, llm_client, persona,
        f"The office day just moved into the '{phase}' phase. React in one short sentence, "
        "in character.", None, cid)
    _state(state_path, expression, action=f"phase: {phase}", bubble=line if ok else None,
           emotion=emotion)
    if ok:
        _publish_line(worker_id, agent_config, producer, line, emotion, cid, addressees=[])
    _event(worker_id, "phase_noted", phase=phase, narrated=ok, correlation_id=cid)


# ── office_role hooks called by the reused dev-team handlers ─────────────────
def engineer_prepare(worker_id, agent_config, coding_backend, msg):
    """coder.handle_task_assignment hook, before the coding run: check out the
    task's feature branch (office/engineer/task-<chain8>, re-used by bug-fix
    retries) in the coding backend's workspace. Returns a context dict for
    engineer_handoff, or None (no workspace / git failure -> the run happens
    on whatever branch is checked out)."""
    if office_role_of(agent_config) is not OfficeRole.ENGINEER:
        return None
    payload = msg.get("payload") or {}
    cid = correlation_of(msg)
    git = build_git_client(agent_config, repo_path=getattr(coding_backend, "workspace", None))
    if git is None:
        return None
    title = payload.get("title") or (payload.get("task") or "task").splitlines()[0]
    # Keyed on the chain only: a bug-fix re-assignment carries a different
    # task text ("Fix bug (...): ...") but must land on the same branch/PR.
    branch = f"{BRANCH_PREFIX}{OfficeRole.ENGINEER.value}/task-{(cid or 'nochain')[:8]}"
    base = _base_branch(agent_config)
    try:
        start = _checkout_branch(git, branch, base)
    except (GitError, OSError) as exc:
        _event(worker_id, "engineer_branch_failed", logging.ERROR, branch=branch,
               correlation_id=cid, error=f"'{exc}'")
        return None
    _event(worker_id, "engineer_branch_ready", branch=branch, start=(start or "")[:8],
           correlation_id=cid)
    return {"git": git, "branch": branch, "base": base, "start": start, "title": title}


def _changed_paths(git, from_ref, to_ref="HEAD"):
    """Repo-relative paths changed between two refs (lane enforcement)."""
    if not from_ref:
        return []
    out = subprocess.run(["git", "-C", git.repo_path, "diff", "--name-only", from_ref, to_ref],
                         capture_output=True, text=True, check=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def engineer_handoff(worker_id, agent_config, producer, msg, result, narration, ctx):
    """coder.handle_task_assignment hook, replacing commit_notification for an
    office Engineer: enforce the src/ lane on the commit, push the branch,
    open (or re-use) its PR, and send `test_request` to the Tester. A lane
    violation is a blocker: clarification_request to the assigner instead."""
    payload = msg.get("payload") or {}
    cid = correlation_of(msg)
    task = payload.get("task", "(no task description provided)")
    ids = reply_ids(msg)
    pr = branch = None
    if result is not None and result.success and ctx:
        git, branch = ctx["git"], ctx["branch"]
        try:
            paths = _changed_paths(git, ctx["start"])
        except (subprocess.CalledProcessError, OSError) as exc:
            _event(worker_id, "engineer_diff_failed", logging.ERROR, correlation_id=cid,
                   error=f"'{exc}'")
            paths = []
        bad = [p for p in paths if not lane_allows(OfficeRole.ENGINEER, p)]
        if bad:
            _event(worker_id, "lane_violation", logging.ERROR, role="engineer",
                   paths=",".join(bad), correlation_id=cid)
            producer.send(build_message(
                worker_id, msg.get("from") or SEAT[OfficeRole.TECH_LEAD], "clarification_request",
                {"task": task, "error": f"lane violation: engineer may only change src/, not {bad}"},
                **ids))
            return None
        if git.push_branch(branch):
            gitea = build_gitea_client(agent_config)
            if gitea is not None:
                pr = _gitea_call(worker_id, cid, "open_pr", _find_or_open_pr, gitea, branch,
                                 ctx["base"], f"Engineer: {ctx['title']}", f"{task}\n\n{narration}")
                pr = pr if isinstance(pr, int) else None
    out = build_test_request(
        task.splitlines()[0] if task.strip() else task, sender=worker_id, branch=branch,
        commit=getattr(result, "commit", None), reply_to=msg)
    out = _with_extra(out, {
        "task": task, "retry_count": payload.get("retry_count", 0), "coder_id": worker_id,
        "narration": narration, "pr": pr, "directive_id": payload.get("directive_id"),
        "issue": payload.get("issue"), "title": payload.get("title"),
        "directive": payload.get("directive")})
    producer.send(out)
    _publish_line(worker_id, agent_config, producer, narration, None, cid, addressees=[out["to"]])
    _event(worker_id, "test_request_sent", branch=branch, pr=pr, correlation_id=cid)
    return out


def tech_lead_after_test_passed(worker_id, agent_config, producer, msg, narration):
    """manager.handle_test_passed hook: COMMENT-review and merge the
    Engineer's PR, then send a `done` status_report to the CEO."""
    if office_role_of(agent_config) is not OfficeRole.TECH_LEAD:
        return None
    payload = msg.get("payload") or {}
    cid = correlation_of(msg)
    task = payload.get("task", "(no task description provided)")
    merged = _review_and_merge(worker_id, agent_config, build_gitea_client(agent_config),
                               payload.get("pr"), f"Tech Lead review: tests pass.\n\n{narration}", cid)
    directive = directive_for(msg) or {}
    try:
        report = build_status_report(worker_id, narration or f"'{task}' passed its tests.",
                                     status="done", day=directive.get("day"), reply_to=msg)
        report = _with_extra(report, {"task": task, "pr": payload.get("pr"), "merged": merged,
                                      "issue": payload.get("issue") or directive.get("issue"),
                                      "directive_id": payload.get("directive_id")})
    except ProtocolError as exc:
        _event(worker_id, "status_report_failed", logging.ERROR, correlation_id=cid,
               error=f"'{exc}'")
        return None
    producer.send(report)
    _publish_line(worker_id, agent_config, producer, narration, None, cid,
                  addressees=[report["to"]])
    _event(worker_id, "status_report_sent", role="tech_lead", merged=merged, correlation_id=cid)
    return report


# ── idle-tick hooks ──────────────────────────────────────────────────────────
def set_day_runner(runner):
    """Install the CEO's day runner (OB-30): called by ceo_idle_tick as
    runner(worker_id, agent_config, llm_client, producer, state_path).
    None uninstalls it. Returns the previous runner."""
    global _day_runner
    previous, _day_runner = _day_runner, runner
    log.info("office day_runner installed=%s", runner is not None)
    return previous


def get_day_runner():
    return _day_runner


def ceo_idle_tick(worker_id, agent_config, llm_client, producer, state_path=None):
    """IDLE_TICK_HOOKS["ceo"]: delegate to the installed day runner (default:
    none -> no-op; an `agent.office.day_runner` block auto-installs
    office.day_runner on the first tick). Never raises."""
    global _day_runner_autoinstall_done
    if office_role_of(agent_config) is not OfficeRole.CEO:
        return None
    if _day_runner is None and not _day_runner_autoinstall_done:
        # OB-30: with an agent.office.day_runner block, build and install the
        # day runner on the first CEO tick (once; set_day_runner still wins).
        _day_runner_autoinstall_done = True
        try:
            from office import day_runner
            if day_runner.day_runner_enabled(agent_config):
                set_day_runner(day_runner.build_day_runner(agent_config, worker_id=worker_id))
        except Exception as exc:
            _event(worker_id, "day_runner_install_failed", logging.ERROR, error=f"'{exc}'")
    if _day_runner is None:
        return None
    try:
        return _day_runner(worker_id, agent_config, llm_client, producer, state_path)
    except Exception as exc:
        _event(worker_id, "day_runner_failed", logging.ERROR, error=f"'{exc}'")
        return None


class ChoreScheduler:
    """Rotates the Office Manager's chores (CHORES) every `interval_s`; the
    first one is due one interval after start. `clock` is injectable."""

    def __init__(self, interval_s=DEFAULT_CHORE_INTERVAL_S, chores=CHORES, clock=None):
        self.interval_s = float(interval_s)
        self.chores = tuple(chores) or CHORES
        self.clock = clock or time.monotonic
        self._next = 0
        self._due_at = self.clock() + self.interval_s

    def due(self):
        """The chore due now (and advance), or None."""
        if self.clock() < self._due_at:
            return None
        chore = self.chores[self._next % len(self.chores)]
        self._next += 1
        self._due_at = self.clock() + self.interval_s
        return chore


def collect_garbage(gitea, base_branch=DEFAULT_BASE_BRANCH, prefix=BRANCH_PREFIX):
    """Office Manager branch GC: delete the head branches of CLOSED PRs
    (merged or abandoned) that start with `prefix`, unless an open PR still
    uses them or they are the base branch / a loop/ archive. Returns the
    deleted names; an already-gone branch (404) is skipped."""
    open_heads = {(pr.get("head") or {}).get("ref") for pr in gitea.list_prs("open")}
    deleted, seen = [], set()
    for pr in gitea.list_prs("closed"):
        ref = (pr.get("head") or {}).get("ref")
        if (not ref or ref in seen or ref in open_heads or ref == base_branch
                or not ref.startswith(prefix) or ref.startswith("loop/")):
            continue
        seen.add(ref)
        try:
            gitea.delete_branch(ref)
        except GiteaError as exc:
            if exc.status == 404:
                log.debug("office gc branch already gone name=%s", ref)
                continue
            log.error("office gc delete failed name=%s status=%s error=%s", ref, exc.status, exc)
            continue
        deleted.append(ref)
    log.info("office gc done deleted=%d", len(deleted))
    return deleted


def _get_chores(agent_config):
    global _chores
    if _chores is None:
        cfg = office_settings(agent_config).get("chores") or {}
        _chores = ChoreScheduler(interval_s=cfg.get("interval_s", DEFAULT_CHORE_INTERVAL_S),
                                 chores=cfg.get("rotation") or CHORES)
    return _chores


def office_manager_idle_tick(worker_id, agent_config, llm_client, producer, state_path=None):
    """IDLE_TICK_HOOKS["office_manager"]: every chore interval, do the next
    chore — coffee, cleanup (narrated) or garbage_collection (stale office/
    branch deletes through Gitea). Returns the chore done, or None. Never raises."""
    if office_role_of(agent_config) is not OfficeRole.OFFICE_MANAGER:
        return None
    try:
        chore = _get_chores(agent_config).due()
        if chore is None:
            return None
        detail = ""
        if chore == "garbage_collection":
            gitea = build_gitea_client(agent_config)
            deleted = collect_garbage(gitea, _base_branch(agent_config)) if gitea is not None else []
            detail = f" You deleted {len(deleted)} stale branches." if deleted else " Nothing was stale."
        persona = persona_prompt(agent_config, _today)
        line, emotion, ok = _speak(
            worker_id, llm_client, persona,
            f"You're doing an office chore: {chore.replace('_', ' ')}.{detail} "
            "Say one short line about it, in character.", None, None)
        _state(state_path, "focused" if chore == "garbage_collection" else "happy",
               action=f"chore: {chore}", bubble=line if ok else None, emotion=emotion)
        if ok:
            _publish_line(worker_id, agent_config, producer, line, emotion, addressees=[])
        _event(worker_id, "chore_done", chore=chore)
        return chore
    except Exception as exc:
        _event(worker_id, "chore_failed", logging.ERROR, error=f"'{exc}'")
        return None


def _observer_target(worker_id, agent_config):
    """Who the Party Member watches: the live speaker on the roundtable stage
    file (agent.office.observer.stage_path) when there is one, else the next
    seat in a slow rotation."""
    cfg = office_settings(agent_config).get("observer") or {}
    stage_path = cfg.get("stage_path")
    if stage_path:
        import gaze  # pure module; only needed when a stage file is configured
        stage = gaze.read_stage(stage_path)
        if gaze.stage_is_live(stage, time.time()) and stage.get("speaker") != worker_id:
            return stage["speaker"]
    others = [seat for seat in SEAT.values() if seat != worker_id]
    target = others[_observer["rotation"] % len(others)]
    _observer["rotation"] += 1
    return target


def observer_idle_tick(worker_id, agent_config, llm_client, producer, state_path=None):
    """IDLE_TICK_HOOKS["observer"]: every N ticks (agent.office.observer.
    every_ticks, default 3) turn the Party Member's gaze and broadcast an
    `observer_pose` {seat, pose, gaze_target}. No LLM, no text, no bubble —
    ever. Returns the sent message or None. Never raises."""
    if office_role_of(agent_config) is not OfficeRole.PARTY_MEMBER:
        return None
    try:
        cfg = office_settings(agent_config).get("observer") or {}
        every = max(1, int(cfg.get("every_ticks", DEFAULT_OBSERVER_EVERY_TICKS)))
        tick = _observer["tick"]
        _observer["tick"] += 1
        if tick % every:
            return None
        target = _observer_target(worker_id, agent_config)
        _state(state_path, "idle", action="observing", bubble=None, emotion="neutral")
        msg = build_message(worker_id, BROADCAST, OBSERVER_POSE,
                            {"seat": worker_id, "pose": OBSERVER_POSE_NAME, "gaze_target": target})
        producer.send(msg)
        _trace("observer_idle_tick exit target=%s", target)
        return msg
    except Exception as exc:
        _event(worker_id, "observer_tick_failed", logging.ERROR, error=f"'{exc}'")
        return None


__all__ = [
    "AUTO_BASE_BRANCH", "BRANCH_PREFIX", "CHORES", "WRAP_UP_REPORT", "DIRECTIVE_RECIPIENTS", "OBSERVER_POSE", "ChoreScheduler",
    "LaneViolation", "build_git_client", "build_gitea_client", "ceo_idle_tick",
    "collect_garbage", "current_phase", "directive_for", "engineer_handoff",
    "engineer_prepare", "extract_tasks", "get_day_runner", "handle_directive",
    "handle_character_refresh", "handle_functional_plan", "handle_phase_change",
    "handle_status_report", "handle_wrap_up", "loop_epoch_and_tz",
    "handle_technical_plan", "handle_test_request", "issue_directive", "lane_commit",
    "observer_idle_tick", "office_manager_idle_tick", "office_role_of", "office_settings",
    "persona_prompt", "remember_directive", "set_day_runner", "tech_lead_after_test_passed",
]
