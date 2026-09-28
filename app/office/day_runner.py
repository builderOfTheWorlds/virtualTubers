"""
office/day_runner.py
The CEO-as-GM day runner (OB-30): the office day's clock-driven script,
run from the CEO worker's idle tick (agent_handlers.office.ceo_idle_tick ->
the runner installed with set_day_runner).

Every tick it places `now` on the office clock (office.clock.office_time)
and does whatever is due, once:

    06:00          day_start, phase_change(morning), then today's directive
    12:00 / 18:00  phase_change(build) / phase_change(ship)
    s1..s3         follow-up directives (at most 2) once the previous one is
                   done; the stall detector (fallback replay after N idle min)
    23:45          wrap-up: the CEO asks for status reports (wrap_up
                   broadcast), narrates the day, comments on open issues
    00:00          day_end(yesterday), phase_change(off), then the s0 hand-off
                   to the ambient/replay playlist (no live work in s0)

Directive sources, first hit wins: 1) the arc plan's spine for today
(OB-40, injectable, default none); 2) a corpus session tagged `feature`,
role-attributed + leak-audited via OB-12 and rewritten by the CEO's LLM as
a Fraud-Stop directive; 3) the open Fraud-Stop Gitea issues backlog.

Everything the runner has done today lives in a small JSON state file
(relay_io.atomic_write_json), so a restart mid-day never re-emits
day_start, a phase_change, the wrap-up or a second directive. The clock,
the sources, the playlist, the completion probe and the activity probe are
all injectable. See docs/office_day_runner.md.

SECURITY: no token is read or logged here (Gitea goes through
agent_handlers.office.build_gitea_client, which uses env-var names only);
corpus session content is never logged, and a rewritten directive that
fails the leak audit is refused.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Callable, Optional, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from agent_handlers.manager import MAX_BUG_RETRIES
from agent_state import write_state
from message_bus import BROADCAST, build_message
from office.clock import DEFAULT_TZ, PHASES, office_time
from office.protocol import (
    CLOCK_SENDER,
    CLOCK_SENDERS,
    build_day_end,
    build_day_start,
    build_phase_change,
)
from relay_io import atomic_write_json, read_json

log = logging.getLogger(__name__)
TRACE = 5
logging.addLevelName(TRACE, "TRACE")

# ── constants ────────────────────────────────────────────────────────────────
STATE_VERSION = 1
DEFAULT_STATE_PATH = "/data/world-state/office_day_runner.json"
DEFAULT_EPOCH = date(2026, 9, 27)          # a Sunday; only loop_week depends on it
WORK_START_HOUR = 6
WRAP_UP_AT = dtime(23, 45)
MAX_FOLLOW_UPS = 2                         # plan guard: 1 directive + at most 2 follow-ups
MAX_DIRECTIVE_ATTEMPTS = MAX_BUG_RETRIES   # plan guard: reuse the manager's retry cap
DEFAULT_STALL_MINUTES = 45.0
DEFAULT_RETRY_BACKOFF_S = 300.0
DEFAULT_COMPLETION_POLL_S = 300.0
DEFAULT_CORPUS_TAG = "feature"
DIRECTIVE_LABEL = "directive"              # agent_handlers.office.DIRECTIVE_LABEL
WRAP_UP = "wrap_up"                        # CEO broadcast asking for end-of-day reports
REPLAY_REQUEST = "replay_request"          # handled by agent_handlers.replay_relay
SOURCES = ("arc", "feature", "backlog")
_MAX_USED_REFS = 500
_MAX_TEXT_CHARS = 1500

REWRITE_INSTRUCTION = (
    "You are choosing today's directive for the Fraud-Stop team (Fraud-Stop is an "
    "enterprise SaaS that banks send transactions through to get a fraud verdict: a "
    "score 0-1000 and APPROVE/REVIEW/DECLINE, keyed on account_id). Below is the "
    "outline of a real feature-building work session. Rewrite it as ONE concrete "
    "Fraud-Stop feature directive the team can build today. Reply with ONLY a JSON "
    'object: {"title": <at most 8 words>, "text": <1-3 sentences>}. Never include '
    "secrets, credentials, IP addresses, file system paths or usernames."
)


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


def _event(worker_id, event, level=logging.INFO, **fields):
    """One structured key=value line to the console (docker logs; the agent
    loop doesn't configure logging) and to the module logger — the same
    format as agent_handlers.office._event."""
    kv = " ".join(f"{k}={v}" for k, v in fields.items())
    line = f"event={event} {kv}".rstrip()
    prefix = {logging.ERROR: "ERROR ", logging.WARNING: "WARN "}.get(level, "")
    print(f"[agent:{worker_id}] {prefix}day_runner {line}")
    log.log(level, "day_runner worker=%s %s", worker_id, line)


def _office():
    """agent_handlers.office, imported lazily: it installs this runner, so a
    top-level import would make office/ depend on agent_handlers at import
    time (and tests monkeypatch names on it, which a lazy lookup honours)."""
    from agent_handlers import office as office_handlers
    return office_handlers


# ── injectable contracts ─────────────────────────────────────────────────────
#: A directive source: source(day, context) -> {"text", "title"?, "ref"?} | None.
#: `context` keys: kind ("primary" | "follow_up"), index (0 = primary),
#: office_time (OfficeTime), directives (today's so far), used_refs (set of
#: refs used on any day), worker_id, agent_config, llm_client, persona (the
#: CEO persona prompt, lazily built — call it), gitea (GiteaClient | None).
#: A source that raises is logged and skipped, like one that returns None.
DirectiveSource = Callable[[str, dict], Optional[dict]]


@runtime_checkable
class Playlist(Protocol):
    """The ambient/replay playlist contract (OB-33 implements it in
    app/office/playlist.py; this module never imports it).

    Both calls return either a replay request dict — {"episode": <name>,
    "speed"?, "cast"?, "worker_name"?} — which the runner sends as a
    `replay_request` bus message to `replay_target`, or None when the
    playlist queued something itself (or has nothing). They may raise; the
    runner logs and carries on. An optional `resume_live(day, context)`
    method is called at day_start so the playlist can stand down.
    `context` keys: worker_id, day, reason ("off_hours" | "stall"),
    directives (today's directive records), idle_minutes (stall only).
    """

    def off_hours(self, day: str, context: dict) -> Optional[dict]: ...

    def stall(self, day: str, context: dict) -> Optional[dict]: ...


# ── built-in directive sources ───────────────────────────────────────────────
def _truncate(text, limit=_MAX_TEXT_CHARS):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _parse_directive_reply(reply):
    """LLM reply -> (title, text). JSON first; a plain non-JSON reply is the
    text itself. Raises ValueError when there is nothing usable."""
    reply = (reply or "").strip()
    start, end = reply.find("{"), reply.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(reply[start:end + 1])
        except ValueError:
            data = None
        if isinstance(data, dict) and str(data.get("text") or "").strip():
            return _truncate(data.get("title") or "", 80) or None, _truncate(data["text"])
    if reply and not reply.startswith("{"):
        return None, _truncate(reply)
    raise ValueError("no directive in the LLM reply")


class CorpusFeatureSource:
    """Source 2: a sessionCorpus export record tagged `tag` (default
    `feature`), not used on an earlier day, oldest first. The record goes
    through OB-12 role attribution (redaction + the leak audit as a hard
    gate); its outline is rewritten by the CEO's LLM as a Fraud-Stop
    directive, and the rewrite is leak-audited again. `loader()` returns the
    records (default: read the JSONL `export_path`)."""

    name = "feature"

    def __init__(self, export_path=None, tag=DEFAULT_CORPUS_TAG, loader=None, max_candidates=5):
        self.export_path = export_path
        self.tag = tag
        self.loader = loader or self._load_export
        self.max_candidates = max(1, int(max_candidates))

    def _load_export(self):
        if not self.export_path:
            return []
        records = []
        try:
            with open(self.export_path, encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(record, dict):
                        records.append(record)
        except OSError as exc:
            log.warning("day_runner corpus export unreadable path=%s error=%s",
                        os.path.basename(str(self.export_path)), exc)
            return []
        log.debug("day_runner corpus export loaded records=%d", len(records))
        return records

    @staticmethod
    def _outline(episode, limit=30):
        """A short, already-redacted outline of an attributed episode."""
        lines = []
        for ev in episode.get("events", [])[:limit]:
            gist = ev.get("text") or f"{ev.get('tool')}: {ev.get('input_summary') or ''}"
            lines.append(f"[{ev.get('role')}] {_truncate(gist, 160)}")
        return "\n".join(lines)

    def __call__(self, day, context):
        from office.role_attribution import AttributionError, attribute
        from session_log_parser import audit

        _trace("CorpusFeatureSource enter day=%s", day)
        llm_client = context.get("llm_client")
        if llm_client is None:
            log.debug("day_runner feature source skipped reason=no_llm")
            return None
        used = context.get("used_refs") or set()
        candidates = [r for r in self.loader()
                      if self.tag in (r.get("tags") or [])
                      and f"corpus:{r.get('session_id')}" not in used]
        candidates.sort(key=lambda r: (str(r.get("started_at") or ""), str(r.get("session_id"))))
        log.debug("day_runner feature candidates=%d tag=%s", len(candidates), self.tag)
        for record in candidates[: self.max_candidates]:
            ref = f"corpus:{record.get('session_id')}"
            try:
                episode = attribute(record)
            except AttributionError as exc:
                log.warning("day_runner feature session rejected ref=%s reason=%s", ref, exc)
                continue
            persona = context.get("persona")
            system = f"{persona() if callable(persona) else persona or ''}\n\n{REWRITE_INSTRUCTION}"
            reply = llm_client.complete(
                system.strip(),
                [{"role": "user", "content": f"Today is {day}.\n\nSession outline:\n"
                                             f"{self._outline(episode)}"}])
            title, text = _parse_directive_reply(reply)
            if audit(json.dumps({"title": title, "text": text})) is not None:
                # Never echo the match: it is the secret.
                log.error("day_runner feature rewrite failed the leak audit ref=%s", ref)
                continue
            log.info("day_runner feature directive rewritten ref=%s chars=%d", ref, len(text))
            return {"text": text, "title": title, "ref": ref}
        return None


class GiteaIssueBacklog:
    """Source 3: the oldest open Fraud-Stop issue (lowest number) that is
    not a directive issue (label `directive`, opened by the CEO) and was not
    used on an earlier day. `label`, when set, keeps only issues carrying it."""

    name = "backlog"

    def __init__(self, label=None, skip_labels=(DIRECTIVE_LABEL,)):
        self.label = label
        self.skip_labels = frozenset(skip_labels or ())

    def __call__(self, day, context):
        _trace("GiteaIssueBacklog enter day=%s label=%s", day, self.label)
        gitea = context.get("gitea")
        if gitea is None:
            log.debug("day_runner backlog source skipped reason=no_gitea")
            return None
        log.debug("day_runner gitea call op=list_issues state=open label=%s", self.label)
        issues = gitea.list_issues("open", labels=[self.label] if self.label else None) or []
        used = context.get("used_refs") or set()
        for issue in sorted(issues, key=lambda i: int(i.get("number") or 0)):
            number = issue.get("number")
            labels = {(lb or {}).get("name") for lb in issue.get("labels") or []}
            if number is None or labels & self.skip_labels or f"issue:{number}" in used:
                continue
            title = _truncate(issue.get("title") or f"Issue #{number}", 80)
            body = _truncate(issue.get("body") or "")
            text = f"{title}. {body}".strip() if body else title
            log.info("day_runner backlog issue picked number=%s", number)
            return {"text": f"{text} (backlog issue #{number})", "title": title,
                    "ref": f"issue:{number}"}
        log.debug("day_runner backlog empty open=%d", len(issues))
        return None


def issue_closed_probe(directive, context):
    """Default completion probe: a directive with an issue is done once its
    issue is no longer among the open `directive` issues (the CEO closes it
    on the Tech Lead's done report). None = unknown (no issue / no Gitea)."""
    gitea, issue = context.get("gitea"), directive.get("issue")
    if gitea is None or issue is None:
        return None
    log.debug("day_runner gitea call op=list_issues state=open label=%s", DIRECTIVE_LABEL)
    open_numbers = {i.get("number") for i in gitea.list_issues("open", labels=[DIRECTIVE_LABEL]) or []}
    return issue not in open_numbers


def mtime_probe(paths):
    """Activity probe over files: the newest mtime (epoch s) among `paths`
    (e.g. the avatar state files or the show log), or None."""
    paths = [p for p in (paths or []) if p]

    def probe():
        newest = None
        for path in paths:
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            newest = mtime if newest is None else max(newest, mtime)
        return newest
    return probe


def load_factory(spec):
    """'package.module:attr' -> the attribute (None for a falsy spec).
    Raises ValueError for a malformed spec, ImportError/AttributeError when
    it can't be resolved."""
    if not spec:
        return None
    module_name, sep, attr = str(spec).partition(":")
    if not sep or not module_name or not attr:
        raise ValueError(f"factory spec must be 'module:attr', got {spec!r}")
    return getattr(importlib.import_module(module_name), attr)


# ── the runner ───────────────────────────────────────────────────────────────
def _fresh_state():
    return {"version": STATE_VERSION, "day": None, "day_started": False, "day_chain": None,
            "day_ended": False, "wrapped_up": False, "phase_key": None, "phase": None,
            "off_hours_day": None, "directives": [], "attempts": 0, "next_attempt_at": 0.0,
            "exhausted": False, "last_progress": None, "last_stall_at": None,
            "last_poll_at": None, "used_refs": []}


class DayRunner:
    """The runner ceo_idle_tick calls: runner(worker_id, agent_config,
    llm_client, producer, state_path). Returns the list of messages it sent
    on this tick (possibly empty). Never raises past __call__'s guard."""

    def __init__(self, *, clock=None, epoch=DEFAULT_EPOCH, tz=DEFAULT_TZ,
                 state_path=DEFAULT_STATE_PATH, arc_provider=None, feature_source=None,
                 backlog_source=None, playlist=None, completion_probe=issue_closed_probe,
                 activity_probe=None, gitea_factory=None, issue_directive=None,
                 stall_minutes=DEFAULT_STALL_MINUTES, retry_backoff_s=DEFAULT_RETRY_BACKOFF_S,
                 completion_poll_s=DEFAULT_COMPLETION_POLL_S, max_follow_ups=MAX_FOLLOW_UPS,
                 replay_target=None, recipients=None):
        if isinstance(epoch, str):
            epoch = date.fromisoformat(epoch)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.epoch = epoch
        self.tz = tz
        try:
            self.zone = ZoneInfo(tz)
        except Exception as exc:  # ZoneInfoNotFoundError is a KeyError
            log.error("day_runner unknown time zone tz=%r error=%s", tz, exc)
            raise ValueError(f"unknown time zone {tz!r}") from exc
        self.state_path = state_path
        self.sources = {"arc": arc_provider, "feature": feature_source, "backlog": backlog_source}
        self.playlist = playlist
        self.completion_probe = completion_probe
        self.activity_probe = activity_probe
        self.gitea_factory = gitea_factory
        self.issue_directive = issue_directive
        self.stall_s = max(1.0, float(stall_minutes) * 60.0)
        self.retry_backoff_s = max(0.0, float(retry_backoff_s))
        self.completion_poll_s = max(0.0, float(completion_poll_s))
        # The plan's guard is "at most 2 follow-ups": config may lower it, never raise it.
        self.max_follow_ups = max(0, min(MAX_FOLLOW_UPS, int(max_follow_ups)))
        self.replay_target = replay_target
        self.recipients = recipients
        self._state = None

    # -- persistence --
    @property
    def state(self):
        if self._state is None:
            self._state = self._load()
        return self._state

    def _load(self):
        if not self.state_path:
            return _fresh_state()
        log.debug("day_runner state read path=%s", self.state_path)
        data = read_json(self.state_path)
        if data is None:
            if os.path.exists(self.state_path):
                log.warning("day_runner state unreadable, starting fresh path=%s", self.state_path)
            return _fresh_state()
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            log.warning("day_runner state version mismatch, starting fresh path=%s", self.state_path)
            return _fresh_state()
        state = _fresh_state()
        state.update(data)
        log.info("day_runner state restored day=%s started=%s ended=%s directives=%d",
                 state["day"], state["day_started"], state["day_ended"], len(state["directives"]))
        return state

    def save(self):
        if not self.state_path:
            return
        try:
            log.debug("day_runner state write path=%s", self.state_path)
            atomic_write_json(self.state_path, self.state, fsync=True)
        except (OSError, TypeError, ValueError) as exc:
            log.error("day_runner state write failed path=%s error=%s", self.state_path, exc)
            print(f"[agent] ERROR day_runner event=state_write_failed error='{exc}'")

    # -- helpers --
    def _sender(self, worker_id):
        return worker_id if worker_id in CLOCK_SENDERS else CLOCK_SENDER

    def _gitea(self, agent_config):
        factory = self.gitea_factory or _office().build_gitea_client
        try:
            return factory(agent_config)
        except Exception as exc:
            log.error("day_runner gitea client unavailable error=%s", exc)
            return None

    def _send(self, producer, msg, sent):
        producer.send(msg)
        sent.append(msg)
        return msg

    def _progress(self, now_ts):
        self.state["last_progress"] = now_ts

    def _directive_summaries(self):
        return [{"title": d.get("title"), "issue": d.get("issue"), "status": d.get("status"),
                 "source": d.get("source"), "kind": d.get("kind")}
                for d in self.state["directives"]]

    def _playlist_call(self, method, worker_id, producer, day, sent, **extra):
        """Call playlist.<method>(day, context); send a replay_request when it
        returns one. Returns True when the playlist was reached."""
        if self.playlist is None:
            _event(worker_id, f"{method}_no_playlist", logging.WARNING, day=day)
            return False
        context = {"worker_id": worker_id, "day": day, "reason": method,
                   "directives": self._directive_summaries(), **extra}
        try:
            log.debug("day_runner playlist call method=%s day=%s", method, day)
            request = getattr(self.playlist, method)(day, context)
        except Exception as exc:
            _event(worker_id, "playlist_failed", logging.ERROR, method=method, error=f"'{exc}'")
            return False
        if isinstance(request, dict) and str(request.get("episode") or "").strip():
            payload = {"episode": str(request["episode"]).strip(), "reason": method, "day": day}
            for key in ("speed", "cast", "worker_name"):
                if request.get(key) is not None:
                    payload[key] = request[key]
            msg = build_message(worker_id, self.replay_target or worker_id, REPLAY_REQUEST, payload)
            self._send(producer, msg, sent)
            _event(worker_id, "replay_requested", reason=method, to=msg["to"],
                   correlation_id=msg["correlation_id"])
        elif request is not None:
            _event(worker_id, "playlist_bad_request", logging.WARNING, method=method)
        return True

    # -- clock emissions --
    def _close_day(self, worker_id, agent_config, llm_client, producer, sent, late):
        st = self.state
        day = st["day"]
        summary = f"{len(st['directives'])} directive(s), " \
                  f"{sum(d.get('status') == 'done' for d in st['directives'])} done"
        msg = build_day_end(day, summary=summary, sender=self._sender(worker_id),
                            correlation_id=st["day_chain"])
        self._send(producer, msg, sent)
        st["day_ended"] = True
        self.save()
        _event(worker_id, "day_end", day=day, late=late, correlation_id=st["day_chain"])

    def _start_day(self, worker_id, day, producer, sent, now_ts):
        st = self.state
        carried = {k: st.get(k) for k in ("used_refs", "phase", "phase_key", "off_hours_day")}
        self._state = st = _fresh_state()
        st.update({k: v for k, v in carried.items() if v is not None})
        st.update(day=day, day_started=True)
        msg = build_day_start(day, sender=self._sender(worker_id))
        st["day_chain"] = msg["correlation_id"]
        self._progress(now_ts)
        self._send(producer, msg, sent)
        self.save()
        _event(worker_id, "day_start", day=day, correlation_id=st["day_chain"])
        resume = getattr(self.playlist, "resume_live", None)
        if callable(resume):
            try:
                resume(day, {"worker_id": worker_id, "day": day, "reason": "day_start"})
            except Exception as exc:
                _event(worker_id, "playlist_failed", logging.ERROR, method="resume_live",
                       error=f"'{exc}'")

    def _phase_edge(self, worker_id, day, segment, producer, sent, chain):
        st = self.state
        key = f"{day}:s{segment}"
        if st["phase_key"] == key:
            return
        phase = PHASES[segment]
        msg = build_phase_change(phase, day, previous=st.get("phase"), segment=segment,
                                 sender=self._sender(worker_id), correlation_id=chain)
        self._send(producer, msg, sent)
        st.update(phase_key=key, phase=phase)
        self.save()
        _event(worker_id, "phase_change", day=day, phase=phase, segment=segment,
               correlation_id=msg["correlation_id"])

    def _wrap_up(self, worker_id, agent_config, llm_client, producer, sent, state_path):
        st = self.state
        st["wrapped_up"] = True
        self.save()     # before the side effects: a crash never repeats the wrap-up
        office = _office()
        directives = self._directive_summaries()
        msg = build_message(worker_id, BROADCAST, WRAP_UP,
                            {"day": st["day"], "directives": directives,
                             "request": "status_report"},
                            correlation_id=st["day_chain"])
        self._send(producer, msg, sent)
        done = sum(d["status"] == "done" for d in directives)
        titles = "; ".join(f"{d['title']} ({d['status']})" for d in directives) or "no directive"
        line, emotion, ok = office._speak(
            worker_id, llm_client, office.persona_prompt(agent_config),
            f"It's 23:45 on {st['day']}: time to wrap up. Today's directives: {titles}. "
            "Ask the team for their end-of-day status reports and sum up the day in 1-3 "
            "sentences, in character.",
            f"That's a wrap for today: {done} of {len(directives)} done. Reports on my desk, please.",
            st["day_chain"])
        if state_path:
            write_state(state_path, "speaking", action="end-of-day wrap-up", bubble=line,
                        emotion=emotion)
        gitea = self._gitea(agent_config)
        if gitea is not None:
            for d in st["directives"]:
                if d.get("issue") and d.get("status") != "done":
                    log.debug("day_runner gitea call op=comment_issue issue=%s", d["issue"])
                    office._gitea_call(worker_id, st["day_chain"], "comment_issue",
                                       gitea.comment_issue, d["issue"],
                                       f"End of day {st['day']}: still open, carried over.")
        _event(worker_id, "wrap_up", day=st["day"], directives=len(directives), done=done,
               narrated=ok, correlation_id=st["day_chain"])

    # -- directives --
    def _pick(self, worker_id, agent_config, llm_client, day, ot, kind, gitea):
        st = self.state
        office = _office()
        persona_cache = {}

        def persona():
            if "p" not in persona_cache:
                persona_cache["p"] = office.persona_prompt(agent_config)
            return persona_cache["p"]

        context = {"kind": kind, "index": len(st["directives"]), "office_time": ot,
                   "directives": self._directive_summaries(), "used_refs": set(st["used_refs"]),
                   "worker_id": worker_id, "agent_config": agent_config,
                   "llm_client": llm_client, "persona": persona, "gitea": gitea}
        for name in SOURCES:
            source = self.sources.get(name)
            if source is None:
                log.debug("day_runner source absent name=%s", name)
                continue
            try:
                log.debug("day_runner source call name=%s kind=%s", name, kind)
                picked = source(day, context)
            except Exception as exc:
                _event(worker_id, "source_failed", logging.ERROR, source=name, error=f"'{exc}'")
                continue
            if isinstance(picked, dict) and str(picked.get("text") or "").strip():
                log.debug("day_runner source hit name=%s ref=%s", name, picked.get("ref"))
                return name, picked
            log.debug("day_runner source miss name=%s", name)
        return None, None

    def _try_directive(self, worker_id, agent_config, llm_client, producer, sent, day, ot,
                       now_ts, kind, state_path):
        st = self.state
        gitea = self._gitea(agent_config)
        source, picked = self._pick(worker_id, agent_config, llm_client, day, ot, kind, gitea)
        if picked is None:
            self._failed_attempt(worker_id, now_ts, kind, "no_source")
            return
        record = {"kind": kind, "source": source, "ref": picked.get("ref"),
                  "title": picked.get("title"), "issue": None, "correlation_id": None,
                  "status": "issuing", "issued_at": now_ts}
        st["directives"].append(record)
        if record["ref"]:
            st["used_refs"] = (st["used_refs"] + [record["ref"]])[-_MAX_USED_REFS:]
        # Persist BEFORE issuing: a crash mid-issue must never produce a second
        # directive (a leftover "issuing" record counts as issued on restart).
        self.save()
        issue = self.issue_directive or _office().issue_directive
        kwargs = {"title": picked.get("title"), "day": day, "state_path": state_path}
        if self.recipients:
            kwargs["recipients"] = self.recipients
        try:
            log.debug("day_runner issue_directive call source=%s kind=%s", source, kind)
            msgs = issue(worker_id, agent_config, llm_client, producer, picked["text"], **kwargs)
        except Exception as exc:  # ProtocolError, Gitea/LLM surprises
            _event(worker_id, "issue_directive_failed", logging.ERROR, source=source,
                   error=f"'{exc}'")
            msgs = []
        if not msgs:
            st["directives"].remove(record)
            self._failed_attempt(worker_id, now_ts, kind, "issue_failed")
            return
        sent.extend(msgs)
        root = msgs[0]
        record.update(status="active", correlation_id=root.get("correlation_id"),
                      issue=(root.get("payload") or {}).get("issue"),
                      title=(root.get("payload") or {}).get("title") or record["title"])
        st["last_poll_at"] = now_ts
        self._progress(now_ts)
        self.save()
        _event(worker_id, "directive_chosen", kind=kind, source=source, ref=record["ref"],
               issue=record["issue"], correlation_id=record["correlation_id"])

    def _failed_attempt(self, worker_id, now_ts, kind, reason):
        st = self.state
        st["attempts"] += 1
        st["next_attempt_at"] = now_ts + self.retry_backoff_s
        if st["attempts"] >= MAX_DIRECTIVE_ATTEMPTS:
            st["exhausted"] = True
            _event(worker_id, "directive_retries_exhausted", logging.WARNING, kind=kind,
                   reason=reason, attempts=st["attempts"], cap=MAX_DIRECTIVE_ATTEMPTS)
        else:
            _event(worker_id, "directive_attempt_failed", logging.WARNING, kind=kind,
                   reason=reason, attempts=st["attempts"], cap=MAX_DIRECTIVE_ATTEMPTS)
        self.save()

    def _poll_completion(self, worker_id, agent_config, now_ts):
        st = self.state
        active = [d for d in st["directives"] if d.get("status") in ("active", "issuing")]
        if not active or self.completion_probe is None:
            return
        if st["last_poll_at"] is not None and now_ts - st["last_poll_at"] < self.completion_poll_s:
            return
        st["last_poll_at"] = now_ts
        context = {"gitea": self._gitea(agent_config), "worker_id": worker_id,
                   "agent_config": agent_config}
        for d in active:
            try:
                done = self.completion_probe(d, context)
            except Exception as exc:
                _event(worker_id, "completion_probe_failed", logging.ERROR, error=f"'{exc}'")
                done = None
            log.debug("day_runner completion probe issue=%s done=%s", d.get("issue"), done)
            if done:
                d["status"] = "done"
                self._progress(now_ts)
                _event(worker_id, "directive_done", issue=d.get("issue"),
                       correlation_id=d.get("correlation_id"))
        self.save()

    def _manage_directives(self, worker_id, agent_config, llm_client, producer, sent, day, ot,
                           now_ts, state_path):
        st = self.state
        self._poll_completion(worker_id, agent_config, now_ts)
        if st["exhausted"] or now_ts < st["next_attempt_at"]:
            return
        directives = st["directives"]
        if not directives:
            self._try_directive(worker_id, agent_config, llm_client, producer, sent, day, ot,
                                now_ts, "primary", state_path)
            return
        if any(d.get("status") != "done" for d in directives):
            return      # one active directive at a time
        if len(directives) - 1 >= self.max_follow_ups:
            return
        self._try_directive(worker_id, agent_config, llm_client, producer, sent, day, ot,
                            now_ts, "follow_up", state_path)

    def _check_stall(self, worker_id, producer, sent, day, now_ts):
        st = self.state
        last = st["last_progress"]
        if self.activity_probe is not None:
            try:
                seen = self.activity_probe()
            except Exception as exc:
                log.error("day_runner activity probe failed error=%s", exc)
                seen = None
            if seen is not None and (last is None or seen > last):
                last = seen
        if last is None:
            st["last_progress"] = last = now_ts
        if st["last_stall_at"] is not None and st["last_stall_at"] > last:
            last = st["last_stall_at"]  # re-arm N minutes after the previous fallback
        idle = now_ts - last
        log.debug("day_runner stall check idle_s=%.0f threshold_s=%.0f", idle, self.stall_s)
        if idle < self.stall_s:
            return
        st["last_stall_at"] = now_ts
        self.save()
        _event(worker_id, "stall_detected", logging.WARNING, day=day,
               idle_minutes=round(idle / 60.0, 1))
        self._playlist_call("stall", worker_id, producer, day, sent,
                            idle_minutes=round(idle / 60.0, 1))

    # -- the tick --
    def tick(self, worker_id, agent_config, llm_client, producer, state_path=None):
        now = self.clock()
        now_ts = now.timestamp()
        ot = office_time(now, self.epoch, self.tz)
        local = now.astimezone(self.zone)
        today = local.date().isoformat()
        st = self.state
        sent = []
        _trace("day_runner tick enter local=%s segment=%s day=%s", local, ot.segment, st["day"])

        if ot.segment == 0:
            # 00:00-06:00: close the work day that just ended, then hand s0 to the playlist.
            if st["day_started"] and not st["day_ended"]:
                late = st["day"] != (local.date() - timedelta(days=1)).isoformat()
                if not st["wrapped_up"]:
                    self._wrap_up(worker_id, agent_config, llm_client, producer, sent, state_path)
                self._close_day(worker_id, agent_config, llm_client, producer, sent, late)
            self._phase_edge(worker_id, today, 0, producer, sent, None)
            if st["off_hours_day"] != today:
                st["off_hours_day"] = today
                self.save()
                reached = self._playlist_call("off_hours", worker_id, producer, today, sent)
                _event(worker_id, "off_hours_handoff", day=today, playlist=reached)
            return sent

        if st["day"] != today:
            if st["day_started"] and not st["day_ended"]:
                # The runner missed a whole night (down across 00:00): close the old day late.
                if not st["wrapped_up"]:
                    self._wrap_up(worker_id, agent_config, llm_client, producer, sent, state_path)
                self._close_day(worker_id, agent_config, llm_client, producer, sent, True)
            self._start_day(worker_id, today, producer, sent, now_ts)
            st = self.state
        self._phase_edge(worker_id, today, ot.segment, producer, sent, st["day_chain"])

        if local.time() >= WRAP_UP_AT:
            if not st["wrapped_up"]:
                self._wrap_up(worker_id, agent_config, llm_client, producer, sent, state_path)
            return sent

        self._manage_directives(worker_id, agent_config, llm_client, producer, sent, today, ot,
                                now_ts, state_path)
        self._check_stall(worker_id, producer, sent, today, now_ts)
        _trace("day_runner tick exit sent=%d", len(sent))
        return sent

    def __call__(self, worker_id, agent_config, llm_client, producer, state_path=None):
        try:
            return self.tick(worker_id, agent_config, llm_client, producer, state_path)
        except Exception as exc:
            _event(worker_id, "tick_failed", logging.ERROR, error=f"'{exc}'")
            return []


# ── construction from config ─────────────────────────────────────────────────
def day_runner_settings(agent_config):
    """The `agent.office.day_runner` block ({} when absent; {"enabled": True}
    for a bare `day_runner: true`)."""
    office_cfg = (agent_config or {}).get("office")
    cfg = office_cfg.get("day_runner") if isinstance(office_cfg, dict) else None
    if cfg is True:
        return {"enabled": True}
    return cfg if isinstance(cfg, dict) else {}


def day_runner_enabled(agent_config):
    cfg = day_runner_settings(agent_config)
    return bool(cfg) and cfg.get("enabled", True) is not False


def _build_plugin(worker_id, spec, agent_config, what):
    """Resolve a 'module:factory' spec and call factory(agent_config). A bad
    spec is logged and treated as absent (the runner still runs)."""
    try:
        factory = load_factory(spec)
        return factory(agent_config) if factory is not None else None
    except Exception as exc:
        _event(worker_id, "plugin_unavailable", logging.ERROR, what=what, spec=spec,
               error=f"'{exc}'")
        return None


def build_day_runner(agent_config, *, worker_id="ceo", clock=None, **overrides):
    """DayRunner from `agent.office.day_runner` (docs/office_day_runner.md).
    Keyword `overrides` win over config (tests, or a caller wiring its own
    playlist / arc provider)."""
    cfg = day_runner_settings(agent_config)
    office_cfg = (agent_config or {}).get("office") or {}
    kwargs = {
        "clock": clock,
        "epoch": cfg.get("epoch") or office_cfg.get("epoch") or DEFAULT_EPOCH,
        "tz": cfg.get("tz") or office_cfg.get("tz") or DEFAULT_TZ,
        "state_path": cfg.get("state_path", DEFAULT_STATE_PATH),
        "stall_minutes": cfg.get("stall_minutes", DEFAULT_STALL_MINUTES),
        "retry_backoff_s": cfg.get("retry_backoff_s", DEFAULT_RETRY_BACKOFF_S),
        "completion_poll_s": cfg.get("completion_poll_s", DEFAULT_COMPLETION_POLL_S),
        "max_follow_ups": cfg.get("max_follow_ups", MAX_FOLLOW_UPS),
        "replay_target": cfg.get("replay_target"),
    }
    if "arc_provider" not in overrides:
        kwargs["arc_provider"] = _build_plugin(worker_id, cfg.get("arc_provider"), agent_config,
                                               "arc_provider")
    if "playlist" not in overrides:
        kwargs["playlist"] = _build_plugin(worker_id, cfg.get("playlist"), agent_config,
                                           "playlist")
    if "feature_source" not in overrides:
        export = cfg.get("corpus_export")
        kwargs["feature_source"] = CorpusFeatureSource(
            export, tag=cfg.get("corpus_tag") or DEFAULT_CORPUS_TAG) if export else None
    if "backlog_source" not in overrides:
        kwargs["backlog_source"] = None if cfg.get("backlog") is False else \
            GiteaIssueBacklog(label=cfg.get("backlog_label"))
    if "activity_probe" not in overrides and cfg.get("activity_paths"):
        kwargs["activity_probe"] = mtime_probe(cfg.get("activity_paths"))
    kwargs.update(overrides)
    runner = DayRunner(**kwargs)
    _event(worker_id, "built", tz=runner.tz, epoch=runner.epoch,
           state_path=runner.state_path, stall_minutes=runner.stall_s / 60.0,
           sources=",".join(n for n in SOURCES if runner.sources.get(n)) or "none",
           playlist=runner.playlist is not None)
    return runner


__all__ = [
    "CorpusFeatureSource", "DayRunner", "DirectiveSource", "GiteaIssueBacklog",
    "MAX_DIRECTIVE_ATTEMPTS", "MAX_FOLLOW_UPS", "Playlist", "REPLAY_REQUEST", "WRAP_UP",
    "WRAP_UP_AT", "build_day_runner", "day_runner_enabled", "day_runner_settings",
    "issue_closed_probe", "load_factory", "mtime_probe",
]
