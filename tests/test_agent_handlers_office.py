"""Tests for app/agent_handlers/office.py (OB-21): the office message
handlers, the office_role hooks in the reused coder/tester/manager
handlers, the office idle-tick hooks, and a fake-bus end-to-end run of the
whole directive chain.

Every external edge is faked: LLM (ScriptedLLM), coding backend and pytest
(tests/e2e_harness.py), git (FakeGit) and Gitea (FakeGitea). Character files
live in tmp_path — never the real campaigns/ashiorid_office pack.
"""
import json
from pathlib import Path

import pytest
import yaml

from agent_handlers import IDLE_TICK_HOOKS, MESSAGE_HANDLERS, office
from e2e_harness import OPERATOR, E2EHarness, FakeTestRunner, ScriptedLLM, llm_reply
from gitea_client import GiteaError
from message_bus import BROADCAST, build_message
from office.protocol import (
    OFFICE_MESSAGE_TYPES,
    build_directive,
    build_functional_plan,
    build_phase_change,
    build_status_report,
    build_test_request,
)
from office.roles import SEAT, OfficeRole as R

DAY = "2026-09-28"
DIRECTIVE = "Add a velocity rule that flags more than five transactions a minute."
CEO, TL, AN, ENG, TST, MKT, OM, PM = (SEAT[r] for r in (
    R.CEO, R.TECH_LEAD, R.ANALYST, R.ENGINEER, R.TESTER, R.MARKETING, R.OFFICE_MANAGER,
    R.PARTY_MEMBER))
HANDLER_ROLE = {R.CEO: "ceo", R.TECH_LEAD: "manager", R.ANALYST: "analyst", R.ENGINEER: "coder",
                R.TESTER: "tester", R.MARKETING: "marketing", R.OFFICE_MANAGER: "office_manager",
                R.PARTY_MEMBER: "observer"}


# ── fakes ────────────────────────────────────────────────────────────────────
class FakeGitea:
    """In-memory Gitea: issues, PRs (open -> closed on merge), branches."""

    def __init__(self, review_error=None):
        self.calls = []
        self.issues = {}
        self.prs = {}
        self.deleted = []
        self.review_error = review_error
        self._n = 0

    def _next(self):
        self._n += 1
        return self._n

    def open_issue(self, title, body="", labels=None):
        n = self._next()
        self.issues[n] = {"number": n, "title": title, "state": "open", "labels": labels}
        self.calls.append(("open_issue", n, title))
        return {"number": n}

    def comment_issue(self, number, body):
        self.calls.append(("comment_issue", number, body))
        return {"id": len(self.calls)}

    def close_issue(self, number):
        self.issues[number]["state"] = "closed"
        self.calls.append(("close_issue", number))
        return {"number": number, "state": "closed"}

    def open_pr(self, head, base, title, body=""):
        n = self._next()
        self.prs[n] = {"number": n, "head": {"ref": head}, "base": {"ref": base},
                       "title": title, "state": "open"}
        self.calls.append(("open_pr", n, head))
        return {"number": n}

    def list_prs(self, state="open"):
        return [dict(pr) for pr in self.prs.values() if state in ("all", pr["state"])]

    def review_pr(self, number, event, body=""):
        self.calls.append(("review_pr", number, event))
        if self.review_error:
            raise self.review_error
        return {"id": 1}

    def merge_pr(self, number, style="merge", delete_branch=True, message=None):
        self.prs[number]["state"] = "closed"
        self.calls.append(("merge_pr", number))
        return True

    def delete_branch(self, name):
        self.calls.append(("delete_branch", name))
        self.deleted.append(name)
        return True

    def ops(self, name):
        return [c for c in self.calls if c[0] == name]


class FakeGit:
    """GitClient stand-in on a real tmp directory (lane_commit writes files)."""

    def __init__(self, repo_path, push_ok=True):
        self.repo_path = str(repo_path)
        self.push_ok = push_ok
        self.calls = []
        self.branches = {"main"}
        self.current = "main"
        self._commits = 0

    def checkout_new_branch(self, name, start_point=None):
        from git_client import GitError
        if name in self.branches:
            raise GitError(f"branch {name} exists")
        self.branches.add(name)
        self.current = name
        self.calls.append(("checkout_new_branch", name, start_point))
        return f"sha-start-{name}"

    def checkout(self, ref):
        from git_client import GitError
        if ref not in self.branches:
            raise GitError(f"unknown ref {ref}")
        self.current = ref
        self.calls.append(("checkout", ref))
        return f"sha-{ref}"

    def commit_all(self, message):
        self._commits += 1
        self.calls.append(("commit_all", message))
        return f"commit-{self._commits}"

    def push_branch(self, name, force=False):
        self.calls.append(("push_branch", name))
        return self.push_ok


class RecordingLLM(ScriptedLLM):
    """ScriptedLLM that also records the system prompt of every call."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.systems = []

    def complete(self, system_prompt, messages):
        self.systems.append(system_prompt)
        return super().complete(system_prompt, messages)


class ListProducer:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return message

    def types(self):
        return [m["type"] for m in self.sent]


# ── fixtures ─────────────────────────────────────────────────────────────────
def make_pack(root):
    for role in R:
        (root / "cast").mkdir(parents=True, exist_ok=True)
        (root / "profiles").mkdir(parents=True, exist_ok=True)
        (root / "cast" / f"{role.value}.yaml").write_text(
            yaml.safe_dump({"name": role.value, "system_prompt": f"You are the {role.value}."}),
            encoding="utf-8")
        (root / "profiles" / f"{role.value}.yaml").write_text(
            yaml.safe_dump({"id": role.value, "backstory": {"believed": f"I am {role.value}."}}),
            encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _fresh_office_state():
    office._reset_office_state()
    yield
    office._reset_office_state()


@pytest.fixture
def pack(tmp_path):
    return make_pack(tmp_path / "pack")


@pytest.fixture
def gitea():
    return FakeGitea()


@pytest.fixture
def gits(tmp_path):
    """Per-role FakeGit on tmp_path/git/<role>."""
    repos = {}

    def get(role):
        if role not in repos:
            path = tmp_path / "git" / role.value
            path.mkdir(parents=True)
            repos[role] = FakeGit(path)
        return repos[role]
    return get


@pytest.fixture
def wired(monkeypatch, gitea, gits):
    """Route office.build_gitea_client / build_git_client to the fakes."""
    monkeypatch.setattr(office, "build_gitea_client", lambda cfg: gitea)
    monkeypatch.setattr(office, "build_git_client",
                        lambda cfg, repo_path=None: gits(office.office_role_of(cfg)))
    monkeypatch.setattr(office, "_changed_paths", lambda git, start, to_ref="HEAD": ["src/fraud_stop/rules.py"])
    return gitea


def cfg(role, pack, **extra):
    return {"role": HANDLER_ROLE[role], "office_role": role.value,
            "system_prompt": f"CONFIG PROMPT {role.value}",
            "office": {"pack_dir": str(pack), **extra}}


def directive_msg(to=R.ANALYST, issue=3, **ids):
    return build_directive(R.CEO, to, DIRECTIVE, issue=issue, title="Velocity rule", day=DAY, **ids)


# ── registry ─────────────────────────────────────────────────────────────────
def test_registry_routes_every_handled_office_type():
    for msg_type in ("directive", "functional_plan", "technical_plan", "test_request",
                     "status_report", "phase_change"):
        assert msg_type in OFFICE_MESSAGE_TYPES
        assert MESSAGE_HANDLERS[msg_type] is getattr(office, f"handle_{msg_type}")


def test_idle_hooks_registered_for_office_handler_roles():
    assert IDLE_TICK_HOOKS["ceo"] is office.ceo_idle_tick
    assert IDLE_TICK_HOOKS["office_manager"] is office.office_manager_idle_tick
    assert IDLE_TICK_HOOKS["observer"] is office.observer_idle_tick


# ── step 1: rank / role checks ───────────────────────────────────────────────
def test_directive_from_engineer_is_rejected_as_retake(pack, wired, capsys):
    msg = build_message(ENG, AN, "directive", {"text": "do my bidding"})
    llm, producer = RecordingLLM("analyst"), ListProducer()
    office.handle_directive(AN, cfg(R.ANALYST, pack), llm, producer, msg)
    assert producer.sent == [] and llm.prompts == [] and wired.calls == []
    assert "event=rank_violation" in capsys.readouterr().out


def test_directive_ignored_by_non_office_worker(pack, wired):
    llm, producer = RecordingLLM("coder"), ListProducer()
    office.handle_directive("coder", {"role": "coder"}, llm, producer, directive_msg())
    assert producer.sent == [] and llm.prompts == []


def test_directive_ignored_by_engineer(pack, wired):
    llm, producer = RecordingLLM("eng"), ListProducer()
    msg = build_directive(R.TECH_LEAD, R.ENGINEER, "refactor")
    office.handle_directive(ENG, cfg(R.ENGINEER, pack), llm, producer, msg)
    assert producer.sent == [] and llm.prompts == []


def test_phase_change_from_non_clock_sender_rejected(pack):
    msg = build_message(ENG, BROADCAST, "phase_change", {"phase": "build", "day": DAY})
    llm, producer = RecordingLLM("x"), ListProducer()
    office.handle_phase_change(AN, cfg(R.ANALYST, pack), llm, producer, msg)
    assert llm.prompts == [] and office.current_phase() is None


# ── step 2: persona prompt ───────────────────────────────────────────────────
def test_persona_prompt_is_cast_plus_backstory_plus_directive(pack, wired):
    llm = RecordingLLM("analyst")
    office.handle_directive(AN, cfg(R.ANALYST, pack), llm, ListProducer(), directive_msg())
    system = llm.systems[0]
    assert system.startswith("You are the analyst.")
    assert "I am analyst." in system and DIRECTIVE in system and "(issue #3)" in system


def test_persona_prompt_falls_back_to_config_system_prompt(tmp_path, wired, capsys):
    llm = RecordingLLM("analyst")
    office.handle_directive(AN, cfg(R.ANALYST, tmp_path / "missing"), llm, ListProducer(),
                            directive_msg())
    assert llm.systems[0].startswith("CONFIG PROMPT analyst")
    assert "event=persona_fallback" in capsys.readouterr().out


# ── handle_directive per role ────────────────────────────────────────────────
def test_analyst_writes_requirements_pr_and_sends_functional_plan(pack, wired, gits):
    llm = RecordingLLM("analyst", replies=[llm_reply("- For whom? Banks.\n- Flag bursts.")])
    producer = ListProducer()
    msg = directive_msg()
    office.handle_directive(AN, cfg(R.ANALYST, pack), llm, producer, msg)

    [fp] = producer.sent
    assert (fp["type"], fp["from"], fp["to"]) == ("functional_plan", AN, TL)
    assert fp["payload"]["cc"] == [CEO] and fp["payload"]["pr"] == 1
    assert fp["payload"]["directive"] == DIRECTIVE and fp["payload"]["issue"] == 3
    assert fp["correlation_id"] == msg["correlation_id"] and fp["causation_id"] == msg["id"]
    git = gits(R.ANALYST)
    doc = next(Path(git.repo_path).glob("docs/requirements/*.md"))
    assert doc.name == f"{DAY}-velocity-rule.md" and "Flag bursts." in doc.read_text()
    assert ("push_branch", fp["payload"]["branch"]) in git.calls
    assert git.current == "main"


def test_analyst_llm_failure_still_sends_fallback_plan(pack, wired):
    producer = ListProducer()
    office.handle_directive(AN, cfg(R.ANALYST, pack), RecordingLLM("a", down=True), producer,
                            directive_msg())
    [fp] = producer.sent
    assert fp["type"] == "functional_plan" and DIRECTIVE in fp["payload"]["plan"]


def test_analyst_without_git_or_gitea_still_sends(pack, monkeypatch):
    monkeypatch.setattr(office, "build_gitea_client", lambda c: None)
    monkeypatch.setattr(office, "build_git_client", lambda c, repo_path=None: None)
    producer = ListProducer()
    office.handle_directive(AN, cfg(R.ANALYST, pack), RecordingLLM("a"), producer, directive_msg())
    assert producer.types() == ["functional_plan"] and "pr" not in producer.sent[0]["payload"]


def test_tech_lead_acknowledges_directive_without_sending(pack, wired):
    llm, producer = RecordingLLM("tl"), ListProducer()
    msg = directive_msg(to=R.TECH_LEAD)
    office.handle_directive(TL, cfg(R.TECH_LEAD, pack), llm, producer, msg)
    assert producer.sent == [] and len(llm.prompts) == 1
    assert office.directive_for({"correlation_id": msg["correlation_id"]})["issue"] == 3


@pytest.mark.parametrize("role,path_glob,message_prefix", [
    (R.MARKETING, "marketing/*.md", "docs(marketing)"),
    (R.OFFICE_MANAGER, "CHANGELOG.md", "chore(changelog)"),
])
def test_marketing_and_office_manager_commit_lane_and_report_to_ceo(
        pack, wired, gits, role, path_glob, message_prefix):
    producer = ListProducer()
    office.handle_directive(SEAT[role], cfg(role, pack), RecordingLLM(role.value), producer,
                            directive_msg(to=role))
    [report] = producer.sent
    assert (report["type"], report["from"], report["to"]) == ("status_report", SEAT[role], CEO)
    assert report["payload"]["status"] == "on_track" and report["payload"]["pr"] == 1
    git = gits(role)
    assert list(Path(git.repo_path).glob(path_glob))
    assert any(c[0] == "commit_all" and c[1].startswith(message_prefix) for c in git.calls)


def test_office_manager_appends_to_existing_changelog(pack, wired, gits):
    git = gits(R.OFFICE_MANAGER)
    changelog = Path(git.repo_path) / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n- old entry\n", encoding="utf-8")
    office.handle_directive(OM, cfg(R.OFFICE_MANAGER, pack),
                            RecordingLLM("om", replies=[llm_reply("Velocity rule started.")]),
                            ListProducer(), directive_msg(to=R.OFFICE_MANAGER))
    text = changelog.read_text()
    assert text.startswith("# Changelog\n\n- old entry\n") and f"- {DAY}: Velocity rule started." in text


# ── lanes ────────────────────────────────────────────────────────────────────
def test_lane_commit_rejects_out_of_lane_path(gits, gitea):
    git = gits(R.ANALYST)
    with pytest.raises(office.LaneViolation):
        office.lane_commit(R.ANALYST, git, gitea, edits={"src/evil.py": "x"}, branch="office/a/x",
                           base="main", title="t", body="b", message="m")
    assert git.calls == [] and gitea.calls == []


def test_lane_commit_skips_pr_when_push_fails(tmp_path, gitea):
    git = FakeGit(tmp_path, push_ok=False)
    result = office.lane_commit(R.TESTER, git, gitea, edits={"tests/test_x.py": "pass\n"},
                                branch="office/tester/x", base="main", title="t", body="b",
                                message="test: x")
    assert result == {"branch": "office/tester/x", "commit": "commit-1", "pushed": False, "pr": None}
    assert gitea.calls == [] and git.current == "main"


@pytest.mark.parametrize("plan,expected", [
    ("- add rule\n- wire api", ["add rule", "wire api"]),
    ("1. one\n2) two\n* three", ["one", "two", "three"]),
    ("no bullets at all", ["FALLBACK"]),
    ("- a\n- b\n- c\n- d\n- e", ["a", "b", "c", "d"]),
])
def test_extract_tasks(plan, expected):
    assert office.extract_tasks(plan, "FALLBACK") == expected


# ── Tech Lead: functional plan ───────────────────────────────────────────────
def test_tech_lead_functional_plan_reviews_merges_and_delegates(pack, wired):
    wired.open_pr("office/analyst/velocity-rule-abc", "main", "Requirements")  # PR #1
    root = directive_msg(to=R.TECH_LEAD)
    fp = build_functional_plan("- flag bursts", sender=R.ANALYST, reply_to=root)
    fp["payload"].update(pr=1, directive=DIRECTIVE, title="Velocity rule", issue=3, day=DAY)
    llm = RecordingLLM("tl", replies=[llm_reply("- add src/fraud_stop/rules/velocity.py\n- wire it")])
    producer = ListProducer()
    office.handle_functional_plan(TL, cfg(R.TECH_LEAD, pack), llm, producer, fp)

    tech, task = producer.sent
    assert (tech["type"], tech["to"]) == ("technical_plan", CEO)
    assert tech["payload"]["tasks"] == ["add src/fraud_stop/rules/velocity.py", "wire it"]
    assert tech["payload"]["requirements_merged"] is True and tech["payload"]["pr"] == 2
    assert (task["type"], task["from"], task["to"]) == ("task_assignment", TL, ENG)
    assert task["causation_id"] == tech["id"] and task["correlation_id"] == root["correlation_id"]
    assert task["payload"]["retry_count"] == 0 and task["payload"]["issue"] == 3
    assert ("review_pr", 1, "COMMENT") in wired.calls and ("merge_pr", 1) in wired.calls


def test_tech_lead_review_422_does_not_stall_the_chain(pack, monkeypatch, gits):
    gitea = FakeGitea(review_error=GiteaError(422, "cannot approve your own PR", "POST", "/reviews"))
    monkeypatch.setattr(office, "build_gitea_client", lambda c: gitea)
    monkeypatch.setattr(office, "build_git_client", lambda c, repo_path=None: None)
    gitea.open_pr("office/analyst/x", "main", "Requirements")
    fp = build_functional_plan("- plan", sender=R.ANALYST)
    fp["payload"]["pr"] = 1
    producer = ListProducer()
    office.handle_functional_plan(TL, cfg(R.TECH_LEAD, pack), RecordingLLM("tl"), producer, fp)
    assert producer.types() == ["technical_plan", "task_assignment"]


def test_tech_lead_merge_can_be_disabled(pack, wired):
    wired.open_pr("office/analyst/x", "main", "Requirements")
    fp = build_functional_plan("- plan", sender=R.ANALYST)
    fp["payload"]["pr"] = 1
    producer = ListProducer()
    office.handle_functional_plan(TL, cfg(R.TECH_LEAD, pack, merge_prs=False), RecordingLLM("tl"),
                                  producer, fp)
    assert wired.ops("merge_pr") == [] and producer.sent[0]["payload"]["requirements_merged"] is False


# ── CEO: technical plan / status report ─────────────────────────────────────
def test_ceo_acknowledges_technical_plan_on_issue(pack, wired):
    from office.protocol import build_technical_plan
    msg = build_technical_plan("- do it", sender=R.TECH_LEAD)
    msg["payload"]["issue"] = 3
    producer = ListProducer()
    office.handle_technical_plan(CEO, cfg(R.CEO, pack), RecordingLLM("ceo"), producer, msg)
    assert producer.sent == [] and wired.ops("comment_issue")[0][1] == 3


def test_ceo_closes_issue_only_on_tech_lead_done(pack, wired):
    wired.open_issue("Velocity rule")
    producer, llm = ListProducer(), RecordingLLM("ceo")
    on_track = build_status_report(R.MARKETING, "copy drafted", status="on_track")
    on_track["payload"]["issue"] = 1
    office.handle_status_report(CEO, cfg(R.CEO, pack), llm, producer, on_track)
    assert wired.ops("close_issue") == []
    done = build_status_report(R.TECH_LEAD, "shipped", status="done")
    done["payload"]["issue"] = 1
    office.handle_status_report(CEO, cfg(R.CEO, pack), llm, producer, done)
    assert wired.ops("close_issue") == [("close_issue", 1)] and producer.sent == []


def test_tech_lead_acknowledges_engineer_status_report(pack, wired):
    msg = build_status_report(R.ENGINEER, "halfway there", status="on_track")
    llm = RecordingLLM("tl")
    office.handle_status_report(TL, cfg(R.TECH_LEAD, pack), llm, ListProducer(), msg)
    assert len(llm.prompts) == 1 and wired.ops("close_issue") == []


# ── Tester: test_request ─────────────────────────────────────────────────────
@pytest.mark.parametrize("passed,verdict", [(True, "test_passed"), (False, "bug_report")])
def test_tester_reports_to_tech_lead_and_comments_ci_on_pr(pack, wired, monkeypatch, passed, verdict):
    runner = FakeTestRunner(outcomes=[passed])
    from agent_handlers import tester as tester_handlers
    monkeypatch.setattr(tester_handlers, "workspace_testable", runner.workspace_testable)
    monkeypatch.setattr(tester_handlers, "run_pytest", runner.run_pytest)
    msg = build_test_request("velocity rule", sender=R.ENGINEER, branch="office/engineer/x")
    msg["payload"].update(pr=7, coder_id=ENG, task="velocity rule", issue=3)
    llm, producer = RecordingLLM("tester"), ListProducer()
    office.handle_test_request(TST, cfg(R.TESTER, pack), llm, producer, msg)

    [out] = producer.sent
    assert (out["type"], out["to"]) == (verdict, TL)
    assert out["payload"]["pr"] == 7 and out["payload"]["issue"] == 3
    assert runner.runs == [f"/data/repos/{ENG}"]
    assert llm.systems[0].startswith("You are the tester.")
    [(_, pr, body)] = wired.ops("comment_issue")
    assert pr == 7 and body.startswith("CI:")


def test_tester_from_tech_lead_without_task_uses_description(pack, wired, monkeypatch):
    from agent_handlers import tester as tester_handlers
    monkeypatch.setattr(tester_handlers, "workspace_testable", lambda w: False)
    monkeypatch.setattr(tester_handlers, "_decide_test_outcome", lambda: (True, None))
    msg = build_test_request("regression sweep", sender=R.TECH_LEAD)
    producer = ListProducer()
    office.handle_test_request(TST, cfg(R.TESTER, pack), RecordingLLM("t"), producer, msg)
    assert producer.sent[0]["payload"]["task"] == "regression sweep"


# ── Engineer hooks ───────────────────────────────────────────────────────────
def test_engineer_lane_violation_blocks_handoff(pack, wired, gits, monkeypatch):
    monkeypatch.setattr(office, "_changed_paths", lambda git, start, to_ref="HEAD": ["tests/test_x.py"])
    from e2e_harness import ok_result
    msg = build_message(TL, ENG, "task_assignment", {"task": "velocity", "retry_count": 0})
    ctx = office.engineer_prepare(ENG, cfg(R.ENGINEER, pack), type("B", (), {"workspace": "/w"})(), msg)
    producer = ListProducer()
    assert office.engineer_handoff(ENG, cfg(R.ENGINEER, pack), producer, msg,
                                   ok_result("c1"), "done", ctx) is None
    [blocker] = producer.sent
    assert (blocker["type"], blocker["to"]) == ("clarification_request", TL)
    assert "lane violation" in blocker["payload"]["error"]
    assert not any(c[0] == "push_branch" for c in gits(R.ENGINEER).calls)


def test_engineer_prepare_reuses_branch_on_retry(pack, wired, gits):
    msg = build_message(TL, ENG, "task_assignment", {"task": "velocity", "retry_count": 0})
    backend = type("B", (), {"workspace": "/w"})()
    first = office.engineer_prepare(ENG, cfg(R.ENGINEER, pack), backend, msg)
    retry = dict(msg, id="retry-id", payload={"task": "velocity", "retry_count": 1})
    second = office.engineer_prepare(ENG, cfg(R.ENGINEER, pack), backend, retry)
    assert first["branch"] == second["branch"] and first["branch"].startswith("office/engineer/")
    assert ("checkout", first["branch"]) in gits(R.ENGINEER).calls


def test_non_office_coder_is_unchanged(tmp_path, monkeypatch):
    """No office_role -> the classic commit_notification to 'tester'."""
    h = E2EHarness(tmp_path, monkeypatch)
    h.dev_team()
    h.assign("coder", "task")
    h.bus.run_until_quiet()
    assert "commit_notification" in [m["type"] for m in h.bus.log]
    assert "test_request" not in [m["type"] for m in h.bus.log]


# ── phase_change ─────────────────────────────────────────────────────────────
def test_phase_change_speaking_role_narrates(pack, tmp_path):
    state = tmp_path / "state.json"
    llm = RecordingLLM("an", replies=[llm_reply("Build time.", "happy")])
    office.handle_phase_change(AN, cfg(R.ANALYST, pack), llm, ListProducer(),
                               build_phase_change("build", DAY, previous="morning"), str(state))
    assert office.current_phase() == "build"
    assert json.loads(state.read_text())["bubble"] == "Build time."


def test_phase_change_observer_never_calls_llm(pack, tmp_path):
    state = tmp_path / "state.json"
    llm = RecordingLLM("pm")
    office.handle_phase_change(PM, cfg(R.PARTY_MEMBER, pack), llm, ListProducer(),
                               build_phase_change("ship", DAY), str(state))
    assert llm.prompts == [] and json.loads(state.read_text())["bubble"] is None


# ── idle hooks ───────────────────────────────────────────────────────────────
def test_ceo_idle_tick_default_is_noop(pack):
    producer = ListProducer()
    assert office.get_day_runner() is None
    assert office.ceo_idle_tick(CEO, cfg(R.CEO, pack), RecordingLLM("ceo"), producer) is None
    assert producer.sent == []


def test_ceo_idle_tick_calls_installed_day_runner_and_swallows_errors(pack):
    calls = []
    office.set_day_runner(lambda *args: calls.append(args) or "ran")
    assert office.ceo_idle_tick(CEO, cfg(R.CEO, pack), None, None, "state") == "ran"
    assert calls[0][0] == CEO and calls[0][4] == "state"
    # Not the CEO -> the runner is never called.
    assert office.ceo_idle_tick(TL, cfg(R.TECH_LEAD, pack), None, None) is None and len(calls) == 1

    def boom(*args):
        raise RuntimeError("runner broke")
    office.set_day_runner(boom)
    assert office.ceo_idle_tick(CEO, cfg(R.CEO, pack), None, None) is None


def test_issue_directive_refused_for_non_ceo(pack, wired):
    producer = ListProducer()
    assert office.issue_directive(TL, cfg(R.TECH_LEAD, pack), RecordingLLM("tl"), producer, "x") == []
    assert producer.sent == [] and wired.calls == []


def test_office_manager_chores_rotate(pack, wired):
    config = cfg(R.OFFICE_MANAGER, pack, chores={"interval_s": 0})
    llm = RecordingLLM("om")
    done = [office.office_manager_idle_tick(OM, config, llm, ListProducer()) for _ in range(4)]
    assert done == ["coffee", "cleanup", "garbage_collection", "coffee"]
    assert "coffee" in llm.prompts[0]


def test_office_manager_waits_for_interval(pack, wired):
    config = cfg(R.OFFICE_MANAGER, pack, chores={"interval_s": 3600})
    assert office.office_manager_idle_tick(OM, config, RecordingLLM("om"), ListProducer()) is None


def test_office_manager_idle_tick_never_raises(pack, monkeypatch):
    def broken(cfg):
        raise RuntimeError("no gitea")
    monkeypatch.setattr(office, "build_gitea_client", broken)
    config = cfg(R.OFFICE_MANAGER, pack, chores={"interval_s": 0, "rotation": ["garbage_collection"]})
    assert office.office_manager_idle_tick(OM, config, RecordingLLM("om"), ListProducer()) is None


def test_collect_garbage_only_deletes_closed_office_branches():
    gitea = FakeGitea()
    for head in ("office/engineer/a", "office/analyst/b", "feature/human", "office/engineer/open"):
        gitea.open_pr(head, "main", head)
    for n in (1, 2, 3):
        gitea.prs[n]["state"] = "closed"
    gitea.open_pr("office/engineer/a", "main", "reopened elsewhere")  # still open -> keep
    assert office.collect_garbage(gitea) == ["office/analyst/b"]


def test_collect_garbage_skips_already_deleted_branch():
    gitea = FakeGitea()
    gitea.open_pr("office/x/gone", "main", "t")
    gitea.prs[1]["state"] = "closed"

    def gone(name):
        raise GiteaError(404, "not found", "DELETE", name)
    gitea.delete_branch = gone
    assert office.collect_garbage(gitea) == []


def test_observer_emits_pose_only_every_n_ticks(pack, tmp_path):
    state = tmp_path / "state.json"
    config = cfg(R.PARTY_MEMBER, pack, observer={"every_ticks": 2})
    llm, producer = RecordingLLM("pm"), ListProducer()
    results = [office.observer_idle_tick(PM, config, llm, producer, str(state)) for _ in range(4)]
    assert [r is not None for r in results] == [True, False, True, False]
    assert llm.prompts == []
    for msg in producer.sent:
        assert msg["type"] == office.OBSERVER_POSE and msg["to"] == BROADCAST
        assert set(msg["payload"]) == {"seat", "pose", "gaze_target"}
        assert msg["payload"]["gaze_target"] in SEAT.values() and msg["payload"]["gaze_target"] != PM
    assert producer.sent[0]["payload"]["gaze_target"] != producer.sent[1]["payload"]["gaze_target"]
    assert json.loads(state.read_text())["bubble"] is None


def test_observer_follows_live_speaker_from_stage_file(pack, tmp_path):
    import gaze
    stage = tmp_path / "stage.json"
    gaze.write_stage(str(stage), TL, [CEO], 5.0)
    config = cfg(R.PARTY_MEMBER, pack, observer={"every_ticks": 1, "stage_path": str(stage)})
    msg = office.observer_idle_tick(PM, config, RecordingLLM("pm"), ListProducer())
    assert msg["payload"]["gaze_target"] == TL


def test_observer_gitea_client_is_read_only(pack):
    client = office.build_gitea_client(cfg(R.PARTY_MEMBER, pack, gitea={"base_url": "http://x"}))
    assert client.read_only and client.token_env == office.OBSERVER_TOKEN_ENV
    assert office.build_git_client(cfg(R.PARTY_MEMBER, pack, workspace="/w")) is None


def test_gitea_client_defaults_and_disable(pack):
    client = office.build_gitea_client(cfg(R.ANALYST, pack, gitea={"enabled": True}))
    assert (client.owner, client.repo, client.read_only) == ("gitea_admin", "fraud-stop", False)
    assert office.build_gitea_client(cfg(R.ANALYST, pack)) is None
    assert office.build_gitea_client(cfg(R.ANALYST, pack, gitea={"enabled": False})) is None


# ── fake-bus end to end ──────────────────────────────────────────────────────
@pytest.fixture
def office_team(tmp_path, monkeypatch, pack, wired):
    def build(outcomes=(True,)):
        h = E2EHarness(tmp_path, monkeypatch, test_runner=FakeTestRunner(outcomes=list(outcomes)))
        team = {}
        for role in (R.CEO, R.TECH_LEAD, R.ANALYST, R.ENGINEER, R.TESTER, R.MARKETING,
                     R.OFFICE_MANAGER):
            seat = SEAT[role]
            replies = [llm_reply("Noted."), llm_reply("- add src/fraud_stop/rules/velocity.py")] \
                if role is R.TECH_LEAD else []
            backend = None
            if role is R.ENGINEER:
                from e2e_harness import FakeCodingBackend
                backend = FakeCodingBackend(seat)
            team[role] = h.add_worker(seat, HANDLER_ROLE[role], llm=RecordingLLM(seat, replies=replies),
                                      coding_backend=backend, office_role=role.value,
                                      office={"pack_dir": str(pack)})
        return h, team
    return build


@pytest.mark.integration
def test_e2e_directive_chain_reaches_ceo(office_team, wired):
    h, team = office_team()
    ceo = team[R.CEO]
    office.set_day_runner(lambda wid, cfg_, llm, producer, state_path=None: office.issue_directive(
        wid, cfg_, llm, producer, DIRECTIVE, title="Velocity rule", day=DAY, state_path=state_path))

    office.ceo_idle_tick(ceo.worker_id, ceo.agent_config, ceo.llm, h.bus.producer_for(CEO),
                         ceo.state_path)
    root = h.bus.log[0]
    h.bus.run_until_quiet()

    assert h.bus.triples() == [
        (CEO, TL, "directive"),
        (CEO, AN, "directive"),
        (CEO, MKT, "directive"),
        (CEO, OM, "directive"),
        (AN, TL, "functional_plan"),
        (MKT, CEO, "status_report"),
        (OM, CEO, "status_report"),
        (TL, CEO, "technical_plan"),
        (TL, ENG, "task_assignment"),
        (ENG, BROADCAST, "coding_run_report"),
        (ENG, TL, "task_complete"),
        (ENG, TST, "test_request"),
        (TST, TL, "test_passed"),
        (TL, OPERATOR, "manager_report"),
        (TL, CEO, "status_report"),
    ]
    assert h.bus.assert_chain_consistent(root) == h.bus.log
    final = h.bus.log[-1]
    assert h.bus.lineage(final) == [
        "directive", "directive", "functional_plan", "technical_plan", "task_assignment",
        "test_request", "test_passed", "status_report"]
    assert final["payload"]["status"] == "done" and final["payload"]["merged"] is True
    assert h.bus.undelivered == []

    # The Engineer really "committed" (fake backend) on its feature branch, and
    # the commit rode through test_request.
    test_request = h.bus.of_type("test_request")[0]["payload"]
    assert test_request["commit"] == f"{ENG}-c1" and test_request["coder_id"] == ENG
    assert test_request["branch"] == f"office/engineer/task-{root['id'][:8]}"
    assert team[R.ENGINEER].coding_backend.tasks[0].startswith("Velocity rule\n- add src/")
    assert h.test_runner.runs == [f"/data/repos/{ENG}"]

    # Gitea: issue opened and closed by the CEO; every lane PR opened; the TL
    # COMMENT-reviewed (never APPROVE) and merged the requirements + code PRs.
    prs = {pr["head"]["ref"].split("/")[1]: pr for pr in wired.prs.values()}
    assert set(prs) == {"analyst", "marketing", "office_manager", "tech_lead", "engineer"}
    assert wired.issues[1]["state"] == "closed" and wired.issues[1]["labels"] == ["directive"]
    assert {c[2] for c in wired.ops("review_pr")} == {"COMMENT"}
    merged = {c[1] for c in wired.ops("merge_pr")}
    assert merged == {prs["analyst"]["number"], prs["engineer"]["number"]}
    assert ("comment_issue", prs["engineer"]["number"], "CI: all tests passed.") in wired.calls

    # Persona prompts came from the stub brief with today's directive.
    for role in (R.TECH_LEAD, R.ANALYST, R.MARKETING, R.OFFICE_MANAGER):
        assert any(s.startswith(f"You are the {role.value}.") and DIRECTIVE in s
                   for s in team[role].llm.systems)


@pytest.mark.integration
def test_e2e_bug_then_fix_reuses_engineer_pr(office_team, wired):
    h, team = office_team(outcomes=[False, True])
    ceo = team[R.CEO]
    office.issue_directive(CEO, ceo.agent_config, ceo.llm, h.bus.producer_for(CEO), DIRECTIVE,
                           title="Velocity rule", day=DAY)
    h.bus.run_until_quiet()

    types = [(m["from"], m["to"], m["type"]) for m in h.bus.log]
    assert (TST, TL, "bug_report") in types and (TL, ENG, "task_assignment") in types
    assert types[-1] == (TL, CEO, "status_report")
    requests = h.bus.of_type("test_request")
    assert len(requests) == 2 and requests[0]["payload"]["pr"] == requests[1]["payload"]["pr"]
    assert requests[1]["payload"]["retry_count"] == 1
    assert len([pr for pr in wired.prs.values() if "/engineer/" in pr["head"]["ref"]]) == 1
    assert wired.issues[1]["state"] == "closed"
