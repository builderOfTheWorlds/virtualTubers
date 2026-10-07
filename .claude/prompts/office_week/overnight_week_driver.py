"""Unattended overnight driver for the ashiorid_office full-week generation.

Everything goes through the generator API (job ids visible in the
campaign-manager dashboard); this script only sequences the stages:

  1. activate config 10 (full week, 28 blocks) and submit the arc stage
  2. for each in-fiction day (4 segments, in arc order):
       segment stage [day's ids] -> dialogue stage [day's ids]
       -> audit takes -> move bad slot dirs aside (inside the container,
          the files are root-owned) -> one dialogue retry for that day
  3. stop submitting new work after DEADLINE_H hours

Safety:
  - before every submission, if Ollama has a model resident next to vLLM,
    wait (and ask Ollama to unload it with keep_alive=0) — the 2026-09-29
    OOM was Ollama + vllm-hermes3-70b together on the GB10.
  - never deletes anything; bad takes are moved to slots_badSlots_auto/.

Progress: STATUS_FILE (json) + LOG_FILE in this directory.
Usage: .venv/bin/python overnight_week_driver.py [--resume-run RUN]
"""
import argparse
import datetime
import glob
import json
import logging
import pathlib
import re
import subprocess
import time
import urllib.request

import yaml

API = "http://localhost:8001"
OLLAMA = "http://localhost:11434"
PACK = "ashiorid_office"
CONFIG_ID = 10
DEADLINE_H = 8.0
HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
OUTPUT = REPO / "utilities/3LayersWeeklyGeneration/output"
CAST_DIR = REPO / "campaigns/ashiorid_office/cast"
CONTAINER = "virtualtubers-3layer-generator-1"
STATUS_FILE = HERE / "overnight_status.json"
LOG_FILE = HERE / "overnight_driver.log"

REFUSAL = re.compile(
    r"\b(I (?:will|can)(?: ?not|'t) (?:generate|write|roleplay|produce|create|help)"
    r"|as an AI|I'm (?:sorry|unable)|I am (?:sorry|unable)|I hope you understand"
    r"|I don't feel comfortable|I do not feel comfortable)\b", re.IGNORECASE)
NAME_PREFIX = re.compile(r"^[A-Za-z][A-Za-z .'-]{1,30}:\s")

logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("overnight")
START = time.time()
status = {"started": datetime.datetime.now().isoformat(timespec="seconds"),
          "config_id": CONFIG_ID, "run": None, "jobs": [], "days": {}, "state": "starting"}


def save_status():
    status["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    status["elapsed_h"] = round((time.time() - START) / 3600, 2)
    STATUS_FILE.write_text(json.dumps(status, indent=2))


def http(method, url, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def past_deadline():
    return (time.time() - START) / 3600 >= DEADLINE_H


def guard_ollama():
    """Block while Ollama holds a model; ask it to unload (keep_alive=0)."""
    while True:
        try:
            loaded = http("GET", f"{OLLAMA}/api/ps", timeout=10).get("models") or []
        except Exception as exc:  # Ollama down is fine for us
            log.debug("ollama ps failed: %s", exc)
            return
        if not loaded:
            return
        names = [m.get("name") for m in loaded]
        log.warning("ollama has models resident next to vLLM: %s; requesting unload", names)
        for name in names:
            try:
                http("POST", f"{OLLAMA}/api/generate", {"model": name, "keep_alive": 0}, timeout=60)
            except Exception as exc:
                log.error("ollama unload %s failed: %s", name, exc)
        time.sleep(60)


def submit(stage, run=None, segments=None):
    guard_ollama()
    body = {"pack": PACK, "stage": stage}
    if run:
        body["run"] = run
    if segments:
        body["segments"] = segments
    job = http("POST", f"{API}/jobs", body)
    log.info("submitted %s job %s run=%s segments=%s", stage, job["id"], job.get("run"), segments)
    status["jobs"].append({"id": job["id"], "stage": stage, "segments": segments,
                           "status": "queued"})
    save_status()
    return job


def wait(job_id, poll=30):
    while True:
        try:
            job = http("GET", f"{API}/jobs/{job_id}")
        except Exception as exc:
            log.warning("poll %s failed: %s", job_id, exc)
            time.sleep(poll)
            continue
        if job["status"] in ("completed", "failed", "cancelled"):
            for j in status["jobs"]:
                if j["id"] == job_id:
                    j["status"] = job["status"]
                    j["result"] = {k: v for k, v in (job.get("result") or {}).items()
                                   if k != "artifacts"}
                    j["error"] = job.get("error")
            save_status()
            log.info("job %s %s result=%s error=%s", job_id, job["status"],
                     {k: v for k, v in (job.get("result") or {}).items() if k != "artifacts"},
                     job.get("error"))
            return job
        time.sleep(poll)


def run_stage(stage, run=None, segments=None, retries=1):
    for attempt in range(retries + 1):
        job = wait(submit(stage, run, segments)["id"])
        if job["status"] == "completed":
            return job
        log.error("%s stage attempt %d failed: %s", stage, attempt + 1, job.get("error"))
    return job


def silent_cast():
    out = set()
    for f in CAST_DIR.glob("*.yaml"):
        speech = str((yaml.safe_load(f.read_text()) or {}).get("speech") or "").strip().lower()
        if speech.startswith("none") or "never speaks" in speech:
            out.add(f.stem)
    return out


def audit_segment(run_dir, seg_id, silent):
    """Return (takes, words, {slot_id: [reasons]}) for one segment."""
    seg_dir = run_dir / "segments" / seg_id
    brief_path = seg_dir / "brief.yaml"
    if not brief_path.exists():
        return 0, 0, {}
    brief = yaml.safe_load(brief_path.read_text()) or {}
    slots = {s["slot_id"]: s for s in brief.get("slots") or []}
    bad, words, takes = {}, 0, 0
    for f in sorted(glob.glob(str(seg_dir / "slots" / "*" / "*.yaml"))):
        takes += 1
        slot_id = pathlib.Path(f).parent.name
        allowed = set((slots.get(slot_id) or {}).get("participants") or [])
        beats = (yaml.safe_load(open(f)) or {}).get("beats") or []
        reasons = []
        if not beats:
            reasons.append("empty")
        for b in beats:
            text = str(b.get("text", "")).split("||")[0]
            words += len(text.split())
            spk = b.get("speaker")
            if b.get("kind") == "dialogue" and allowed and spk not in allowed:
                reasons.append(f"outside_cast:{spk}")
            if b.get("kind") == "dialogue" and spk in silent:
                reasons.append(f"silent_spoke:{spk}")
            if REFUSAL.search(text):
                reasons.append("refusal")
            if b.get("kind") == "narration" and NAME_PREFIX.match(text):
                reasons.append("unmatched_speaker_prefix")
        if reasons:
            bad[slot_id] = sorted(set(reasons))
    return takes, words, bad


def move_aside(run, seg_id, slot_ids):
    base = f"/data/output/{run}/segments/{seg_id}"
    for slot_id in slot_ids:
        cmd = ["docker", "exec", CONTAINER, "sh", "-c",
               f"mkdir -p {base}/slots_badSlots_auto && "
               f"mv {base}/slots/{slot_id} {base}/slots_badSlots_auto/{slot_id}_$(date +%s)"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            log.error("move aside %s/%s failed: %s", seg_id, slot_id, res.stderr.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume-run", help="skip the arc stage and continue this run")
    args = ap.parse_args()
    save_status()

    http("POST", f"{API}/configs/{CONFIG_ID}/activate")
    log.info("activated config %s", CONFIG_ID)

    if args.resume_run:
        run = args.resume_run
    else:
        status["state"] = "arc"
        save_status()
        arc = run_stage("all")
        run = arc["run"]
        status["run"] = run
        if arc["status"] != "completed":
            status["state"] = "arc_failed"
            save_status()
            return
    status["run"] = run
    run_dir = OUTPUT / run
    plan = yaml.safe_load((run_dir / "arc_plan.yaml").read_text())["segments"]
    seg_ids = [s["id"] for s in sorted(plan, key=lambda s: s["order"])]
    status["planned_segments"] = len(seg_ids)
    log.info("arc plan has %d segments", len(seg_ids))
    silent = silent_cast()

    for day_index in range(0, len(seg_ids), 4):
        day = seg_ids[day_index:day_index + 4]
        key = f"day{day_index // 4}"
        if past_deadline():
            log.warning("deadline reached before %s; stopping", key)
            status["state"] = "deadline"
            break
        status["state"] = f"{key}:segment"
        status["days"][key] = {"segments": day}
        save_status()
        # Explicit segment lists always re-plan (runner._select_segments), so
        # on a resume skip segments that already have a brief.
        to_plan = [s for s in day
                   if not (run_dir / "segments" / s / "brief.yaml").exists()]
        if to_plan:
            run_stage("segment", run, to_plan)
        if past_deadline():
            status["state"] = "deadline"
            break
        status["state"] = f"{key}:dialogue"
        save_status()
        run_stage("dialogue", run, day)

        totals = {"takes": 0, "words": 0, "bad": {}}
        for seg_id in day:
            t, w, bad = audit_segment(run_dir, seg_id, silent)
            totals["takes"] += t
            totals["words"] += w
            if bad:
                totals["bad"][seg_id] = bad
        log.info("%s audit: %s", key, {k: (v if k != "bad" else {s: len(b) for s, b in v.items()})
                                       for k, v in totals.items()})
        if totals["bad"] and not past_deadline():
            for seg_id, bad in totals["bad"].items():
                move_aside(run, seg_id, list(bad))
            status["state"] = f"{key}:dialogue_retry"
            save_status()
            run_stage("dialogue", run, list(totals["bad"]), retries=0)
            totals["after_retry"] = {}
            for seg_id in totals["bad"]:
                t, w, bad = audit_segment(run_dir, seg_id, silent)
                totals["after_retry"][seg_id] = {"takes": t, "words": w, "bad": bad}
        status["days"][key]["audit"] = totals
        save_status()
    else:
        status["state"] = "done"

    # Final whole-run tally.
    grand = {"takes": 0, "words": 0, "bad_slots": 0}
    for seg_id in seg_ids:
        t, w, bad = audit_segment(run_dir, seg_id, silent)
        grand["takes"] += t
        grand["words"] += w
        grand["bad_slots"] += len(bad)
    status["final"] = grand
    save_status()
    log.info("finished state=%s final=%s", status["state"], grand)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log.exception("driver crashed: %s", exc)
        status["state"] = f"crashed: {exc}"
        save_status()
        raise
