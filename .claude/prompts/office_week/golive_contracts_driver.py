"""Go-live driver: full-week scene contracts (arc + segment) for ashiorid_office.

Unattended, resumable, everything through the generator API (job ids visible
in campaign-manager :8082). NEVER runs the dialogue stage.

Flow
  1. wait for vLLM (hermes3-70b on :8092) to answer /v1/models
  2. make sure generator config CONFIG_ID is the ACTIVE one (re-read to verify)
  3. ARC: submit stage=arc (first time: new run; afterwards the SAME run).
     plan_arc re-plans only the orders missing from arc_plan.yaml (verified in
     src/plan_arc.py: known_orders/missing_orders; batches are cut from the
     missing orders only), so re-submitting after a partially skipped plan
     fills just the holes. Repeats until all 28 orders exist.
  4. SEGMENT: per in-fiction day (4 segments) submit stage=segment with the same
     run and the day's ids. plan_segment resumes from tree.yaml, so unresolved
     nodes are retried. A leaf that exhausted its attempts is stored as an
     empty leaf and would NOT be retried, so after each chunk every brief is
     audited (exists, slots >= MIN_SLOTS, not needs_rebrief); bad ones have
     brief.yaml + tree.yaml moved aside (inside the container, root-owned;
     nothing is deleted) and the chunk is resubmitted.
  5. Before EVERY submission: vLLM healthy (waits, never fails), Ollama has no
     model resident (waits, never touches Ollama), config still active.
  6. A background thread appends `docker logs --since` of the generator
     container to golive_generator.log every LOG_EVERY_S seconds so
     'Skipping batch' reasons survive.

Progress: golive_status.json (atomic writes), golive_driver.log, golive_generator.log
Usage: .venv/bin/python golive_contracts_driver.py [--resume-run RUN] [--config-id N]
"""
import argparse
import datetime
import json
import logging
import os
import pathlib
import subprocess
import threading
import time
import urllib.error
import urllib.request

import yaml

API = "http://localhost:8001"
VLLM = "http://127.0.0.1:8092"
OLLAMA = "http://localhost:11434"
VLLM_ENV = pathlib.Path.home() / "environments/argyreServer/deployments/vllm-hermes3-70b/.env"
VLLM_MODEL = "hermes3-70b"
PACK = "ashiorid_office"
CONFIG_ID = 14
N_SEGMENTS = 28
CHUNK = 4                 # segments per segment-stage job (one in-fiction day)
MIN_SLOTS = 5             # < this (target_slots 10 * density_floor 0.5) = bad brief
MAX_ARC_ROUNDS = 8
MAX_SEG_ROUNDS = 6        # per chunk per pass
SEG_PASSES = 3            # whole-week passes over chunks that are still bad
DEADLINE_H = 12.0
LOG_EVERY_S = 180
CONTAINER = "virtualtubers-3layer-generator-1"

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
OUTPUT = REPO / "utilities/3LayersWeeklyGeneration/output"
STATUS_FILE = HERE / "golive_status.json"
LOG_FILE = HERE / "golive_driver.log"
GEN_LOG = HERE / "golive_generator.log"

logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("golive")
START = time.time()
lock = threading.Lock()
status = {"started": datetime.datetime.now().isoformat(timespec="seconds"),
          "config_id": CONFIG_ID, "run": None, "state": "starting", "jobs": [],
          "arc_growth": [], "chunks": {}, "problems": []}


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def save_status():
    with lock:
        status["updated"] = now()
        status["elapsed_h"] = round((time.time() - START) / 3600, 2)
        tmp = STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(status, indent=2, default=str))
        os.replace(tmp, STATUS_FILE)


def set_state(s):
    status["state"] = s
    log.info("state: %s", s)
    save_status()


def problem(msg):
    log.error("PROBLEM: %s", msg)
    status["problems"].append(f"{now()} {msg}")
    save_status()


def http(method, url, body=None, timeout=60, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def past_deadline():
    return (time.time() - START) / 3600 >= DEADLINE_H


# ---------------------------------------------------------------- guards

_key_cache = {}


def vllm_keys():
    """Candidate bearer keys, never logged: the key the running container enforces
    first (it can differ from the deployment .env), then the .env one."""
    keys = []
    try:
        out = subprocess.run(["docker", "exec", "vllm-hermes3-70b", "sh", "-c",
                              'printf %s "$VLLM_API_KEY"'], capture_output=True,
                             text=True, timeout=20).stdout.strip()
        if out:
            keys.append(out)
    except Exception:
        pass
    try:
        for line in VLLM_ENV.read_text().splitlines():
            if line.startswith("VLLM_API_KEY="):
                keys.append(line.split("=", 1)[1].strip().strip('"').strip("'"))
    except Exception:
        pass
    return keys


def vllm_healthy():
    for key in vllm_keys():
        try:
            d = http("GET", f"{VLLM}/v1/models", timeout=10,
                     headers={"Authorization": f"Bearer {key}"})
            return any(m.get("id") == VLLM_MODEL for m in d.get("data", []))
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                continue
            return False
        except Exception as exc:
            log.debug("vllm probe failed: %s", exc)
            return False
    return False


def wait_vllm():
    """Block until vLLM answers. Never raises, never gives up."""
    waited = 0
    while not vllm_healthy():
        if waited == 0:
            log.warning("vLLM not healthy; waiting")
            status["vllm_down_since"] = now()
            save_status()
        waited += 20
        time.sleep(20)
        if waited % 300 == 0:
            log.warning("vLLM still down after %ds", waited)
    if waited:
        log.info("vLLM healthy again after ~%ds", waited)
        status["vllm_downtime_s"] = status.get("vllm_downtime_s", 0) + waited
    status.pop("vllm_down_since", None)
    save_status()


def wait_ollama_empty():
    """Wait (never touch Ollama) while it holds a model next to vLLM."""
    while True:
        try:
            loaded = http("GET", f"{OLLAMA}/api/ps", timeout=10).get("models") or []
        except Exception:
            return
        if not loaded:
            return
        log.warning("Ollama has resident model(s) %s next to the 70b; not submitting",
                    [m.get("name") for m in loaded])
        time.sleep(60)


def ensure_config():
    for _ in range(3):
        cfgs = http("GET", f"{API}/configs")
        active = [c["id"] for c in cfgs if c.get("is_active")]
        if active == [CONFIG_ID]:
            return
        log.warning("active config is %s, activating %s", active, CONFIG_ID)
        http("POST", f"{API}/configs/{CONFIG_ID}/activate")
    raise RuntimeError(f"could not activate config {CONFIG_ID}")


def preflight():
    wait_ollama_empty()
    wait_vllm()
    ensure_config()


# ---------------------------------------------------------------- jobs

def submit(stage, run=None, segments=None):
    while True:
        try:
            preflight()
            body = {"pack": PACK, "stage": stage}
            if run:
                body["run"] = run
            if segments:
                body["segments"] = segments
            job = http("POST", f"{API}/jobs", body)
            break
        except Exception as exc:
            log.warning("submit %s failed: %s; retry in 30s", stage, exc)
            time.sleep(30)
    log.info("submitted %s job %s run=%s segments=%s", stage, job["id"], job.get("run"), segments)
    with lock:
        status["jobs"].append({"id": job["id"], "stage": stage, "run": job.get("run"),
                               "segments": segments, "status": "queued",
                               "submitted": now(), "t0": time.time()})
    save_status()
    return job


def wait_job(job_id, on_tick=None, poll=20):
    while True:
        try:
            job = http("GET", f"{API}/jobs/{job_id}")
        except Exception as exc:
            log.warning("poll %s failed: %s", job_id, exc)
            time.sleep(poll)
            continue
        if on_tick:
            try:
                on_tick(job)
            except Exception as exc:
                log.debug("tick failed: %s", exc)
        if job["status"] in ("completed", "failed", "cancelled"):
            with lock:
                for j in status["jobs"]:
                    if j["id"] == job_id:
                        j["status"] = job["status"]
                        j["finished"] = now()
                        j["wall_s"] = round(time.time() - j["t0"], 1)
                        j["result"] = {k: v for k, v in (job.get("result") or {}).items()
                                       if k != "artifacts"}
                        j["n_artifacts"] = len((job.get("result") or {}).get("artifacts") or [])
                        j["error"] = job.get("error")
            save_status()
            log.info("job %s %s result=%s error=%s", job_id, job["status"],
                     {k: v for k, v in (job.get("result") or {}).items() if k != "artifacts"},
                     job.get("error"))
            return job
        with lock:
            for j in status["jobs"]:
                if j["id"] == job_id:
                    j["status"] = job["status"]
                    j["progress"] = job.get("progress")
        save_status()
        time.sleep(poll)


# ---------------------------------------------------------------- generator log pump

def log_pump(stop):
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)
    while not stop.is_set():
        t_now = datetime.datetime.now(datetime.timezone.utc)
        try:
            res = subprocess.run(
                ["docker", "logs", "--timestamps", "--since",
                 since.strftime("%Y-%m-%dT%H:%M:%SZ"), CONTAINER],
                capture_output=True, text=True, timeout=120)
            out = (res.stdout or "") + (res.stderr or "")
            if out.strip():
                with open(GEN_LOG, "a") as f:
                    f.write(f"### pull {t_now.isoformat(timespec='seconds')} since "
                            f"{since.isoformat(timespec='seconds')}\n{out}")
            since = t_now - datetime.timedelta(seconds=5)
        except Exception as exc:
            log.warning("log pump failed: %s", exc)
        stop.wait(LOG_EVERY_S)


# ---------------------------------------------------------------- arc

def read_arc(run):
    path = OUTPUT / run / "arc_plan.yaml"
    if not path.exists():
        return []
    try:
        return (yaml.safe_load(path.read_text()) or {}).get("segments") or []
    except Exception as exc:
        log.warning("cannot read arc plan: %s", exc)
        return []


def arc_orders(run):
    return sorted({s["order"] for s in read_arc(run)})


def do_arc(run):
    for rnd in range(1, MAX_ARC_ROUNDS + 1):
        have = arc_orders(run) if run else []
        if len(have) >= N_SEGMENTS:
            return run
        if past_deadline():
            problem("deadline hit during arc")
            return run
        set_state(f"arc round {rnd} (have {len(have)}/{N_SEGMENTS})")
        job = submit("arc", run)
        run = job["run"]
        status["run"] = run
        save_status()
        last = {"n": len(have)}

        def tick(j, last=last):
            n = len(arc_orders(run))
            if n != last["n"]:
                last["n"] = n
                status["arc_growth"].append({"t": now(), "elapsed_s": round(time.time() - START),
                                             "segments": n})
                log.info("arc plan now has %d segments", n)
                save_status()
        job = wait_job(job["id"], on_tick=tick)
        have = arc_orders(run)
        log.info("arc round %d -> job %s, plan has %d/%d", rnd, job["status"], len(have), N_SEGMENTS)
        if len(have) >= N_SEGMENTS:
            return run
        problem(f"arc round {rnd}: job {job['status']} err={job.get('error')} "
                f"plan {len(have)}/{N_SEGMENTS}; missing {sorted(set(range(N_SEGMENTS)) - set(have))}")
        time.sleep(30)
    return run


# ---------------------------------------------------------------- segments

def brief_info(run, seg_id):
    p = OUTPUT / run / "segments" / seg_id / "brief.yaml"
    if not p.exists():
        return None
    try:
        b = yaml.safe_load(p.read_text()) or {}
    except Exception:
        return {"slots": 0, "needs_rebrief": True, "unreadable": True}
    unresolved = 0
    tp = p.with_name("tree.yaml")
    if tp.exists():
        try:
            tree = yaml.safe_load(tp.read_text()) or {}
            unresolved = sum(1 for n in tree.values()
                             if isinstance(n, dict) and n.get("kind") is None)
        except Exception:
            unresolved = 1
    return {"slots": len(b.get("slots") or []), "needs_rebrief": bool(b.get("needs_rebrief")),
            "unresolved_nodes": unresolved}


def brief_ok(info):
    return (bool(info) and info["slots"] >= MIN_SLOTS and not info["needs_rebrief"]
            and not info.get("unresolved_nodes"))


def move_aside(run, seg_id):
    base = f"/data/output/{run}/segments/{seg_id}"
    cmd = ["docker", "exec", CONTAINER, "sh", "-c",
           f"d={base}/_redo_auto_$(date +%s) && mkdir -p $d && "
           f"mv {base}/brief.yaml {base}/tree.yaml $d/ 2>/dev/null; true"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    log.info("moved aside %s (rc=%s %s)", seg_id, res.returncode, res.stderr.strip())


def do_segments(run, seg_ids):
    chunks = [seg_ids[i:i + CHUNK] for i in range(0, len(seg_ids), CHUNK)]
    for pass_no in range(1, SEG_PASSES + 1):
        todo = 0
        for ci, chunk in enumerate(chunks):
            key = f"chunk{ci}"
            for rnd in range(1, MAX_SEG_ROUNDS + 1):
                infos = {s: brief_info(run, s) for s in chunk}
                bad = [s for s in chunk if not brief_ok(infos[s])]
                status["chunks"][key] = {"segments": chunk, "briefs": infos, "pass": pass_no}
                save_status()
                if not bad:
                    break
                if past_deadline():
                    problem("deadline hit during segments")
                    return
                # A brief that exists but is bad (empty leaves) will not be
                # re-planned from its checkpoint: move it aside first.
                # (A brief with unresolved tree nodes is a crashed job: plain
                # resubmit resumes it from tree.yaml, so leave those in place.)
                for s in bad:
                    if infos[s] is not None and not infos[s].get("unresolved_nodes"):
                        move_aside(run, s)
                set_state(f"segment pass {pass_no} {key} round {rnd} bad={len(bad)}")
                job = submit("segment", run, bad)
                job = wait_job(job["id"])
                if job["status"] != "completed":
                    problem(f"segment {key} round {rnd}: {job['status']} {job.get('error')}")
                    time.sleep(30)
            infos = {s: brief_info(run, s) for s in chunk}
            status["chunks"][key] = {"segments": chunk, "briefs": infos, "pass": pass_no}
            todo += sum(1 for s in chunk if not brief_ok(infos[s]))
            save_status()
        if todo == 0:
            return
        problem(f"segment pass {pass_no}: {todo} segments still bad")


# ---------------------------------------------------------------- main

def main():
    global CONFIG_ID
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume-run", help="continue this run (skip a fresh arc if complete)")
    ap.add_argument("--config-id", type=int, default=CONFIG_ID)
    args = ap.parse_args()
    CONFIG_ID = args.config_id
    status["config_id"] = CONFIG_ID
    save_status()

    stop = threading.Event()
    threading.Thread(target=log_pump, args=(stop,), daemon=True).start()
    try:
        run = args.resume_run
        status["run"] = run
        run = do_arc(run)
        status["run"] = run
        plan = sorted(read_arc(run), key=lambda s: s["order"])
        status["planned_segments"] = len(plan)
        status["segment_ids"] = [s["id"] for s in plan]
        save_status()
        if len(plan) < N_SEGMENTS:
            problem(f"arc incomplete: {len(plan)}/{N_SEGMENTS}")
        if plan:
            do_segments(run, [s["id"] for s in plan])
        briefed = sum(1 for s in plan if brief_ok(brief_info(run, s["id"])))
        status["final"] = {"arc_segments": len(plan), "briefs_ok": briefed}
        set_state("done" if briefed == N_SEGMENTS else "done_with_problems")
    except Exception as exc:
        log.exception("driver crashed: %s", exc)
        status["state"] = f"crashed: {exc}"
        save_status()
        raise
    finally:
        time.sleep(1)
        stop.set()
        # final log pull so the tail survives
        try:
            res = subprocess.run(["docker", "logs", "--timestamps", "--since", "10m", CONTAINER],
                                 capture_output=True, text=True, timeout=120)
            with open(GEN_LOG, "a") as f:
                f.write("### final pull\n" + res.stdout + res.stderr)
        except Exception:
            pass


if __name__ == "__main__":
    main()
