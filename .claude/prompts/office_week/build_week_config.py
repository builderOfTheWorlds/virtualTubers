"""Build the ashiorid_office full-week generator config from the active
full-day config (generator_configs id 9) and save it through the API.

The week is 28 six-hour blocks, Sunday 00:00 -> Saturday 24:00
(the weekly loop resets Sunday 00:00, rituals.md / build plan U3).
arc.day_phases gets 28 entries (one per order) so every block carries a
day-specific focus, not just the generic 4-block day.

Story (OB-40): a Fraud-Stop push toward the Halvard & Sons pilot going live
Saturday. Keystone: Wednesday's production fraud miss at Corvane, which
opens a regulator audit Thursday. Secondary threads: Marketing's launch,
the Party Member spy rumour, office life.

Usage: python3 build_week_config.py <base_config.yaml>   (prints new config id)
The base file carries the vLLM key inline; it is never printed.
"""
import json
import sys
import urllib.request

import yaml

API = "http://localhost:8001"

CLOCKS = [("00:00-06:00", "off-hours", []),
          ("06:00-12:00", "morning", ["first-standup"]),
          ("12:00-18:00", "build", []),
          ("18:00-00:00", "ship", [])]

# One entry per day, four blocks each: off-hours, morning, build, ship.
DAYS = [
    ("Sunday", [
        "Week reset night. The office is dark after the loop turns over at "
        "midnight; the Office Manager tidies alone, the Party Member sits unseen "
        "with the notebook, the others are at home dreading or relishing the week.",
        "First standup of the week. The CEO announces the Halvard & Sons pilot "
        "goes live on Saturday; the Analyst and Tech Lead turn that into a plan. "
        "Nervous energy, coffee, a whiteboard countdown.",
        "The Engineer starts the pilot integration, the Tester builds the "
        "Halvard test set, Marketing drafts the launch announcement. Lunch "
        "gossip about Halvard's suspiciously well-informed questions.",
        "First ship of the week: a small release goes out clean. Marketing "
        "previews the launch copy, garbage collection at 23:30, everyone "
        "goes home optimistic.",
    ]),
    ("Monday", [
        "Off-hours. The Engineer cannot sleep and messages the Tester about a "
        "flaky test; the Office Manager restocks the kitchen; the Party Member "
        "is already at his desk when the lights come on.",
        "Standup: Halvard's due-diligence list lands overnight, asking about "
        "learned scoring and explainability. The CEO wants answers by Friday. "
        "The spy rumour about the Party Member resurfaces.",
        "Build. The Analyst maps Halvard's questions to Fraud-Stop features; "
        "the Tech Lead quietly refuses to promise learned scoring. Lunch is "
        "an argument about whether to fake it.",
        "Ship. A reasons-text change for explainability goes out. Marketing "
        "rehearses the Halvard pitch on the Office Manager. Lights out.",
    ]),
    ("Tuesday", [
        "Off-hours. Rain on the windows. The Office Manager finds a printout "
        "of Halvard's questions dated before they were sent. She puts it back.",
        "Standup: the CEO asks Corvane for a reference for Halvard. The Tech "
        "Lead warns the velocity rule is being pushed hard by Corvane's volume.",
        "Build. The Engineer and Tester grind through the pilot integration "
        "bug loop. Marketing calls Corvane about the reference and gets a "
        "lukewarm maybe.",
        "Ship. A rushed rules release goes out late to hit the pilot schedule; "
        "the Tester flags one amount-outlier test as 'amber, not red'. Nobody "
        "stops the release.",
    ]),
    ("Wednesday", [
        "Off-hours. Overnight, a burst of card fraud at Corvane slips past "
        "Fraud-Stop. Nobody in the office knows yet; only the Party Member's "
        "pen moves in the dark.",
        "Standup is interrupted: Corvane calls about the fraud that got "
        "through. The CEO's directive collapses into crisis. The amber test "
        "from last night is remembered by everyone at once.",
        "The keystone of the week. War room in the Moonwell room: the "
        "Engineer traces the miss to the rushed release, the Tester proves "
        "it, the Tech Lead takes responsibility. The fraud miss is flagged.",
        "Ship. An emergency rollback and hotfix. Marketing pauses the launch "
        "campaign. The CEO stays after lights out, staring at the Halvard "
        "countdown on the whiteboard.",
    ]),
    ("Thursday", [
        "Off-hours. Nobody sleeps well. The Office Manager leaves extra coffee "
        "out. The Party Member writes more than usual.",
        "Standup: the regulator opens an audit after Corvane reports the miss. "
        "The CEO asks whether the Saturday pilot is still possible. The spy "
        "rumour turns into an accusation at the coffee machine.",
        "Build. The Analyst prepares the audit evidence; the Engineer adds "
        "the missing test; the Tech Lead rewrites the release checklist. "
        "Halvard asks, before anyone told them, about 'the Corvane incident'.",
        "Ship. The fix is re-released with the full test pass. Marketing "
        "rewrites the launch around honesty and explainable verdicts. "
        "Garbage collection, quieter than usual.",
    ]),
    ("Friday", [
        "Off-hours. The Engineer rehearses the demo at home; the Tester "
        "reruns the suite at 03:00; the Office Manager polishes the Moonwell "
        "room table.",
        "Standup: the regulator's first questions arrive. The CEO decides the "
        "pilot goes ahead if the Friday demo is clean. The Analyst counts "
        "the open risks out loud.",
        "Friday demo at 17:00 in the Moonwell room: the Engineer shows the "
        "week's work running on staging, the Tester says what broke, "
        "Marketing says how to sell it, the CEO decides Halvard hears first.",
        "Ship. The pilot build is cut and tagged. Small celebrations, one "
        "unresolved worry about the audit. Everyone goes home late.",
    ]),
    ("Saturday", [
        "Off-hours before launch day. The office is dark except for the "
        "monitoring dashboard; the Party Member watches it with the notebook "
        "open.",
        "Standup: launch day. The CEO's last directive before the Halvard "
        "pilot goes live. Nerves, coffee, a regulator email nobody wants to "
        "open until after go-live.",
        "Build. The Halvard pilot goes live at noon; the Tester and Engineer "
        "watch the first real verdicts arrive. Marketing's launch goes out. "
        "The first flagged Halvard transaction is caught correctly.",
        "Ship and wind-down. The pilot holds. The audit is still open. The "
        "week ends at midnight and the loop will reset Sunday; garbage "
        "collection, lights out, the notebook closes last.",
    ]),
]


def day_phases():
    phases = []
    for day, focuses in DAYS:
        for (clock, label, spine), focus in zip(CLOCKS, focuses):
            phases.append({"clock": f"{day} {clock}", "label": f"{day.lower()}-{label}",
                           "focus": focus, "spine_scenes": list(spine)})
    assert len(phases) == 28
    return phases


def main(base_path):
    cfg = yaml.safe_load(open(base_path))
    cfg["arc"]["hours_total"] = 168
    cfg["arc"]["segment_hours"] = 6
    cfg["arc"]["batch_size"] = 4          # one day per arc batch
    cfg["arc"]["day_phases"] = day_phases()
    cfg["budget"]["target_total_hours"] = 168
    cfg["budget"]["target_total_words"] = 28 * 1050
    # 8 lines x 45 words does not fit in 300 tokens (cut-off takes, 2026-09-30).
    for m in cfg["dialogue"]["models"].values():
        m["max_tokens"] = 600
    header = ("# ashiorid_office full week (168h, 28 blocks Sun 00:00 -> Sat 24:00),\n"
              "# built by .claude/prompts/office_week/build_week_config.py from\n"
              "# config 9 (full day). Halvard pilot week; keystone Wednesday fraud miss.\n")
    body = header + yaml.safe_dump(cfg, sort_keys=False, width=100)
    payload = json.dumps({"name": "ashiorid_office full week (vllm hermes3-70b, 28 blocks)",
                          "description": "Halvard pilot week, Wed fraud-miss keystone, Thu audit, Sat go-live",
                          "config_yaml": body}).encode()
    req = urllib.request.Request(f"{API}/configs", data=payload,
                                 headers={"Content-Type": "application/json"})
    resp = json.load(urllib.request.urlopen(req))
    print(resp.get("id"))


if __name__ == "__main__":
    main(sys.argv[1])
