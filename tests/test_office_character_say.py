"""Tests for app/office/character_say.py and its one call site
(live_pane.publish_office_line, reached from every office handler that
speaks through agent_handlers.office._publish_line): every spoken office
line is also a v4 `character_say` (WP-15 contract, plan §3.2)."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import live_pane
from agent_handlers import office
from e2e_harness import FakeTestRunner, llm_reply, ok_result
from message_bus import BROADCAST, build_message
from office import character_say as cs
from office.protocol import (
    build_functional_plan,
    build_phase_change,
    build_status_report,
    build_technical_plan,
    build_test_request,
    build_wrap_up,
)
from office.roles import SEAT, OfficeRole as R
from test_agent_handlers_office import (
    DAY,
    DIRECTIVE,
    FakeGit,
    FakeGitea,
    ListProducer,
    RecordingLLM,
    cfg,
    directive_msg,
    make_pack,
)

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)      # Mon 12:00 NY -> build phase
SCENE = "office-2026-09-28-build"
ROSTER = ["ceo", "tech_lead", "analyst", "engineer", "tester", "marketing", "office_manager",
          "party_member"]
#: The WP-15 character_say payload, exactly (character_wp15_bus_contracts.yaml).
WP15_KEYS = ["campaign", "scene_id", "character", "addressees", "present", "text"]
CEO, TL, AN, ENG, TST, MKT, OM, PM = (SEAT[r] for r in (
    R.CEO, R.TECH_LEAD, R.ANALYST, R.ENGINEER, R.TESTER, R.MARKETING, R.OFFICE_MANAGER,
    R.PARTY_MEMBER))


# ── fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _office(monkeypatch):
    office._reset_office_state()
    monkeypatch.setattr(office, "_clock", lambda: NOW)
    for name in ("OFFICE_EPOCH", "OFFICE_TZ"):
        monkeypatch.delenv(name, raising=False)
    yield
    office._reset_office_state()


@pytest.fixture
def pack(tmp_path):
    return make_pack(tmp_path / "pack")


@pytest.fixture
def gitea():
    return FakeGitea()


@pytest.fixture
def wired(monkeypatch, gitea, tmp_path):
    repos = {}

    def git_for(config, repo_path=None):
        role = office.office_role_of(config)
        if role not in repos:
            path = tmp_path / "git" / role.value
            path.mkdir(parents=True)
            repos[role] = FakeGit(path)
        return repos[role]
    monkeypatch.setattr(office, "build_gitea_client", lambda config: gitea)
    monkeypatch.setattr(office, "build_git_client", git_for)
    monkeypatch.setattr(office, "_changed_paths",
                        lambda git, start, to_ref="HEAD": ["src/fraud_stop/rules.py"])
    return gitea


def seat(role, pack, say=True, **extra):
    """An office seat config with agent.office.character_say."""
    config = cfg(role, pack, **extra)
    if say is not None:
        config["office"]["character_say"] = say
    return config


def says(producer):
    return [m for m in producer.sent if m["type"] == cs.CHARACTER_SAY]


def assert_say(msg, sender, character, addressees, scene=SCENE):
    assert msg["type"] == "character_say" and msg["to"] == BROADCAST and msg["from"] == sender
    assert list(msg["payload"]) == WP15_KEYS
    assert msg["payload"] == {"campaign": "ashiorid_office", "scene_id": scene,
                              "character": character, "addressees": addressees,
                              "present": ROSTER, "text": msg["payload"]["text"]}
    assert isinstance(msg["payload"]["text"], str) and msg["payload"]["text"].strip()
    assert msg["id"] and msg["timestamp"] and msg["correlation_id"]


# ── contract shape ───────────────────────────────────────────────────────────
@pytest.mark.unit
def test_payload_keys_match_the_wp15_contract_exactly():
    assert list(cs.PAYLOAD_KEYS) == WP15_KEYS
    msg = cs.build_character_say(ENG, "engineer", "Pushed.", scene_id=SCENE,
                                 addressees=["tester"], correlation_id="chain-1")
    assert list(msg["payload"]) == WP15_KEYS
    assert set(msg) == {"id", "from", "to", "type", "payload", "timestamp", "correlation_id",
                        "causation_id"}
    assert msg["correlation_id"] == "chain-1"
    p = msg["payload"]
    assert (type(p["campaign"]), type(p["scene_id"]), type(p["character"]), type(p["addressees"]),
            type(p["present"]), type(p["text"])) == (str, str, str, list, list, str)


@pytest.mark.unit
@pytest.mark.parametrize("character, text", [(None, "hi"), ("", "hi"), ("engineer", ""),
                                             ("engineer", "   "), ("engineer", None)])
def test_build_rejects_missing_character_or_blank_text(character, text):
    with pytest.raises(cs.CharacterSayError):
        cs.build_character_say(ENG, character, text, scene_id=SCENE)


# ── rules: scene_id / addressees / present ───────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize("local, expected", [
    (datetime(2026, 9, 28, 0, 0, tzinfo=NY), "office-2026-09-28-off"),
    (datetime(2026, 9, 28, 5, 59, tzinfo=NY), "office-2026-09-28-off"),
    (datetime(2026, 9, 28, 6, 0, tzinfo=NY), "office-2026-09-28-morning"),
    (datetime(2026, 9, 28, 12, 0, tzinfo=NY), "office-2026-09-28-build"),
    (datetime(2026, 9, 28, 23, 45, tzinfo=NY), "office-2026-09-28-ship"),
    (datetime(2026, 9, 20, 9, 0, tzinfo=NY), "office-2026-09-20-morning"),   # before the epoch
])
def test_scene_id_is_local_date_and_phase(local, expected):
    assert cs.scene_id_for(local) == expected
    assert cs.scene_id_for(local.astimezone(timezone.utc)) == expected


@pytest.mark.unit
def test_scene_id_honours_the_office_tz():
    assert cs.scene_id_for(datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc), "UTC") == \
        "office-2026-09-28-off"
    assert cs.scene_id_for(datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc)) == \
        "office-2026-09-27-ship"


@pytest.mark.unit
@pytest.mark.parametrize("to, speaker, expected", [
    (None, "engineer", []),
    (BROADCAST, "ceo", []),
    (TST, "engineer", ["tester"]),
    ([CEO, ENG, CEO], "tech_lead", ["ceo", "engineer"]),       # de-duplicated, in order
    ([R.ANALYST, "marketing", "roundtable"], "ceo", ["analyst", "marketing"]),
    ([TL, "tech_lead"], "tech_lead", []),                      # never the speaker
])
def test_addressee_slugs(to, speaker, expected):
    assert cs.addressee_slugs(to, speaker=speaker) == expected


@pytest.mark.unit
def test_present_is_the_whole_roster_party_member_included():
    assert cs.present_slugs() == ROSTER and "party_member" in cs.present_slugs()


# ── gate ─────────────────────────────────────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize("say", [None, False, "yes"])
def test_flag_off_or_absent_emits_nothing(pack, say):
    producer = ListProducer()
    assert cs.publish_character_say(TL, seat(R.TECH_LEAD, pack, say=say), producer, "hi") is None
    assert live_pane.publish_office_line(TL, seat(R.TECH_LEAD, pack, say=say), producer,
                                         "hi") is None
    assert producer.sent == []


@pytest.mark.unit
@pytest.mark.parametrize("line", ["", "   ", None])
def test_empty_line_emits_nothing(pack, line):
    producer = ListProducer()
    live_pane.publish_office_line(TL, seat(R.TECH_LEAD, pack), producer, line, now=NOW)
    assert producer.sent == []


@pytest.mark.unit
def test_party_member_never_emits(pack):
    producer = ListProducer()
    config = seat(R.PARTY_MEMBER, pack, live_transcript=True)
    assert live_pane.publish_office_line(PM, config, producer, "I saw it all.", now=NOW) is None
    assert cs.publish_character_say(PM, config, producer, "I saw it all.", now=NOW) is None
    office.handle_phase_change(PM, config, RecordingLLM("pm"), producer,
                               build_phase_change("ship", DAY))
    office.handle_wrap_up(PM, config, RecordingLLM("pm"), producer, build_wrap_up(DAY))
    assert producer.sent == []


@pytest.mark.unit
def test_non_office_worker_emits_nothing():
    producer = ListProducer()
    assert cs.publish_character_say("coder", {"role": "coder", "office": {"character_say": True}},
                                    producer, "hi") is None
    assert producer.sent == []


@pytest.mark.unit
def test_bus_failure_never_raises(pack, capsys):
    class Broken:
        def send(self, message):
            raise RuntimeError("kafka down")
    assert cs.publish_character_say(TL, seat(R.TECH_LEAD, pack), Broken(), "hi", now=NOW) is None
    assert "event=character_say_publish_failed" in capsys.readouterr().out


@pytest.mark.unit
def test_transcript_and_character_say_carry_the_same_text(pack):
    producer = ListProducer()
    line = live_pane.publish_office_line(TL, seat(R.TECH_LEAD, pack, live_transcript=True),
                                         producer, "  Ship it.  ", "happy", "chain-1",
                                         to=CEO, now=NOW)
    office_line, say = producer.sent
    assert office_line is line and office_line["type"] == "office_line"
    assert say["payload"]["text"] == office_line["payload"]["text"] == "Ship it."
    assert say["correlation_id"] == office_line["correlation_id"] == "chain-1"
    assert_say(say, TL, "tech_lead", ["ceo"])


@pytest.mark.unit
def test_character_say_without_the_live_transcript(pack):
    producer = ListProducer()
    assert live_pane.publish_office_line(TL, seat(R.TECH_LEAD, pack), producer, "Hi.",
                                         addressees=[ENG], now=NOW) is None
    [say] = producer.sent
    assert_say(say, TL, "tech_lead", ["engineer"])


# ── every office handler that speaks: exactly one character_say ─────────────
@pytest.mark.integration
def test_ceo_issue_directive_addresses_its_four_recipients(pack, wired):
    producer = ListProducer()
    office.issue_directive(CEO, seat(R.CEO, pack), RecordingLLM("ceo"), producer, DIRECTIVE,
                           title="Velocity rule", day=DAY)
    [say] = says(producer)
    assert_say(say, CEO, "ceo", ["tech_lead", "analyst", "marketing", "office_manager"])
    assert say["correlation_id"] == producer.sent[0]["correlation_id"]


@pytest.mark.integration
@pytest.mark.parametrize("role, addressees", [
    (R.TECH_LEAD, ["ceo"]),          # acknowledges the CEO
    (R.ANALYST, ["tech_lead"]),      # functional_plan -> Tech Lead
    (R.MARKETING, ["ceo"]),          # status_report -> CEO
    (R.OFFICE_MANAGER, ["ceo"]),
])
def test_directive_handlers(pack, wired, role, addressees):
    producer = ListProducer()
    office.handle_directive(SEAT[role], seat(role, pack), RecordingLLM(role.value), producer,
                            directive_msg(to=role))
    [say] = says(producer)
    assert_say(say, SEAT[role], role.value, addressees)


@pytest.mark.integration
def test_tech_lead_functional_plan_addresses_ceo_and_engineer(pack, wired):
    fp = build_functional_plan("- flag bursts", sender=R.ANALYST, reply_to=directive_msg(to=R.TECH_LEAD))
    llm = RecordingLLM("tl", replies=[llm_reply("- add src/fraud_stop/rules/velocity.py")])
    producer = ListProducer()
    office.handle_functional_plan(TL, seat(R.TECH_LEAD, pack), llm, producer, fp)
    [say] = says(producer)
    assert_say(say, TL, "tech_lead", ["ceo", "engineer"])
    assert say["payload"]["text"].startswith("- add src/")


@pytest.mark.integration
def test_ceo_technical_plan_ack_addresses_the_tech_lead(pack, wired):
    producer = ListProducer()
    office.handle_technical_plan(CEO, seat(R.CEO, pack), RecordingLLM("ceo"), producer,
                                 build_technical_plan("- do it", sender=R.TECH_LEAD))
    [say] = says(producer)
    assert_say(say, CEO, "ceo", ["tech_lead"])


@pytest.mark.integration
@pytest.mark.parametrize("receiver, sender, addressees", [
    (R.CEO, R.TECH_LEAD, ["tech_lead"]),
    (R.TECH_LEAD, R.ENGINEER, ["engineer"]),
])
def test_status_report_ack_addresses_the_reporter(pack, wired, receiver, sender, addressees):
    producer = ListProducer()
    office.handle_status_report(SEAT[receiver], seat(receiver, pack), RecordingLLM("x"), producer,
                                build_status_report(sender, "halfway", status="on_track"))
    [say] = says(producer)
    assert_say(say, SEAT[receiver], receiver.value, addressees)


@pytest.mark.integration
def test_tester_verdict_addresses_the_tech_lead(pack, wired, monkeypatch):
    from agent_handlers import tester as tester_handlers
    runner = FakeTestRunner(outcomes=[True])
    monkeypatch.setattr(tester_handlers, "workspace_testable", runner.workspace_testable)
    monkeypatch.setattr(tester_handlers, "run_pytest", runner.run_pytest)
    msg = build_test_request("velocity rule", sender=R.ENGINEER, branch="office/engineer/x")
    msg["payload"].update(coder_id=ENG, task="velocity rule")
    producer = ListProducer()
    office.handle_test_request(TST, seat(R.TESTER, pack), RecordingLLM("t"), producer, msg)
    [say] = says(producer)
    assert_say(say, TST, "tester", ["tech_lead"])


@pytest.mark.integration
@pytest.mark.parametrize("role, addressees", [
    (R.ANALYST, ["ceo"]), (R.MARKETING, ["ceo"]), (R.OFFICE_MANAGER, ["ceo"]),
    (R.TECH_LEAD, ["ceo"]), (R.ENGINEER, ["tech_lead"]), (R.TESTER, ["tech_lead"]),
])
def test_wrap_up_report_addresses_the_superior(pack, role, addressees):
    producer = ListProducer()
    office.handle_wrap_up(SEAT[role], seat(role, pack), RecordingLLM(role.value), producer,
                          build_wrap_up(DAY, directives=[{"title": "V", "status": "done"}]))
    [say] = says(producer)
    assert_say(say, SEAT[role], role.value, addressees)


@pytest.mark.integration
def test_phase_change_reaction_is_to_the_room(pack):
    producer = ListProducer()
    office.handle_phase_change(AN, seat(R.ANALYST, pack),
                               RecordingLLM("an", replies=[llm_reply("Build time.")]), producer,
                               build_phase_change("build", DAY))
    [say] = says(producer)
    assert_say(say, AN, "analyst", [])
    assert say["payload"]["text"] == "Build time."


@pytest.mark.integration
def test_engineer_handoff_addresses_the_tester(pack, wired):
    msg = build_message(TL, ENG, "task_assignment", {"task": "velocity", "retry_count": 0})
    config = seat(R.ENGINEER, pack)
    ctx = office.engineer_prepare(ENG, config, type("B", (), {"workspace": "/w"})(), msg)
    producer = ListProducer()
    office.engineer_handoff(ENG, config, producer, msg, ok_result("c1"), "Pushed the rule.", ctx)
    [say] = says(producer)
    assert_say(say, ENG, "engineer", ["tester"])
    assert say["payload"]["text"] == "Pushed the rule."


@pytest.mark.integration
def test_tech_lead_after_test_passed_addresses_the_ceo(pack, wired):
    msg = build_message(TST, TL, "test_passed", {"task": "velocity"})
    producer = ListProducer()
    office.tech_lead_after_test_passed(TL, seat(R.TECH_LEAD, pack), producer, msg, "Merged it.")
    [say] = says(producer)
    assert_say(say, TL, "tech_lead", ["ceo"])


@pytest.mark.integration
def test_office_manager_chore_line_is_to_the_room(pack, wired):
    producer = ListProducer()
    config = seat(R.OFFICE_MANAGER, pack, chores={"interval_s": 0, "rotation": ["coffee"]})
    assert office.office_manager_idle_tick(OM, config, RecordingLLM("om"), producer) == "coffee"
    [say] = says(producer)
    assert_say(say, OM, "office_manager", [])


@pytest.mark.integration
def test_flag_off_handlers_emit_no_character_say(pack, wired):
    producer = ListProducer()
    office.handle_directive(AN, seat(R.ANALYST, pack, say=None), RecordingLLM("an"), producer,
                            directive_msg())
    office.handle_phase_change(TL, seat(R.TECH_LEAD, pack, say=False), RecordingLLM("tl"),
                               producer, build_phase_change("build", DAY))
    assert producer.sent and says(producer) == []
