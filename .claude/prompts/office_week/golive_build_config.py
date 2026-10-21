"""Derive the go-live week-contracts generator config from config 10 and save it
through the generator API (does NOT activate it; the driver does that).

Changes versus config 10 (everything else is copied verbatim, key included,
and the key is never printed):
  - arc.max_attempts 3 -> 5, arc.batch_size stays 4 (= one in-fiction day, so
    every batch is exactly Mon..Sun's four blocks and the day_phases block
    list lines up with the batch), arc model max_tokens 2048 -> 4096
  - segment.max_attempts 3 -> 5, segment concurrency 2 -> 4
  - every day_phases block's focus gets a pacing/continuity guidance suffix
    (the only free-text channel the arc prompt exposes): off-hours blocks are
    light, morning blocks must not restage the arrival, every block starts in
    media res from the previous block's continuity_out and must change state.
  - dialogue section untouched (the dialogue stage is never run).

Usage: python3 golive_build_config.py [base_config_id=10]   (prints new id)
"""
import json
import subprocess
import sys
import urllib.request

import yaml

API = "http://localhost:8001"
NAME = "ashiorid_office week contracts (hermes3-70b, golive v2)"

SUFFIX = {
    0: (" GUIDANCE: off-hours block, keep it LIGHT: one or two characters at most, "
        "quiet, no meeting, no arrival scene. End on one small concrete change the "
        "next morning inherits."),
    1: (" GUIDANCE: do NOT restage the Office Manager arriving or people walking "
        "in as the main event; the day is already underway. Continue directly from "
        "the previous block's continuity_out and advance the plot by one concrete "
        "development (a decision, a discovery or a changed relationship)."),
    2: (" GUIDANCE: start in media res from the previous block's continuity_out. "
        "No arrival, no new standup. Show work in progress and end on a decision "
        "or discovery that was not in the previous block."),
    3: (" GUIDANCE: start in media res from the previous block's continuity_out. "
        "No arrival. Resolve or escalate the day's thread and end on a concrete "
        "state change the next off-hours block can pick up."),
}


def container_key():
    """The key the RUNNING vLLM container actually enforces (it can differ from the
    deployment .env when compose was started with VLLM_API_KEY exported in the shell).
    Never printed."""
    out = subprocess.run(["docker", "exec", "vllm-hermes3-70b", "sh", "-c",
                          'printf %s "$VLLM_API_KEY"'], capture_output=True, text=True).stdout
    return out.strip()


def main(base_id):
    base = json.load(urllib.request.urlopen(f"{API}/configs/{base_id}"))
    cfg = yaml.safe_load(base["config_yaml"])
    arc = cfg["arc"]
    assert arc["hours_total"] == 168 and arc["segment_hours"] == 6
    assert len(arc["day_phases"]) == 28
    arc["max_attempts"] = 5
    arc["batch_size"] = 4
    for m in arc["models"].values():
        m["max_tokens"] = 4096
    for i, phase in enumerate(arc["day_phases"]):
        if "GUIDANCE:" not in phase["focus"]:
            phase["focus"] = phase["focus"].rstrip() + SUFFIX[i % 4]
    key = container_key()
    assert key, "could not read the running vLLM container's key"
    for section in ("arc", "segment", "dialogue"):
        for m in cfg[section]["models"].values():
            m["api_key"] = key
    cfg["segment"]["max_attempts"] = 5
    cfg["segment"]["concurrency"] = 4
    header = ("# ashiorid_office week contracts for go-live (168h, 28 blocks Sun 00:00 -> Sat 24:00)\n"
              "# derived from config %s by .claude/prompts/office_week/golive_build_config.py\n"
              "# arc batch=4 (one in-fiction day), max_attempts 5, per-block pacing guidance.\n"
              % base_id)
    body = header + yaml.safe_dump(cfg, sort_keys=False, width=100)
    payload = json.dumps({
        "name": NAME,
        "description": ("Go-live week contracts: Halvard pilot week, Wed fraud-miss keystone, "
                        "Thu audit; arc max_attempts 5, batch 4, light off-hours/no-restaged-arrival "
                        "guidance. Arc+segment only."),
        "config_yaml": body}).encode()
    req = urllib.request.Request(f"{API}/configs", data=payload,
                                 headers={"Content-Type": "application/json"})
    resp = json.load(urllib.request.urlopen(req))
    print(resp.get("id"))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 10)
