#!/usr/bin/env python3
"""Memory watchdog for vllm-agents on the GB10 (unified memory).

Every INTERVAL s: log MemAvailable + vllm-agents container state.
- MemAvailable < FLOOR_GIB  -> `docker stop vllm-agents` (crash guard)
- any model loaded in Ollama -> unload it via keep_alive=0 (no sudo needed)
Log: $LOG (default ~/.hermes/cache/scratch/vllm_mem_watch.log)
"""
import json, os, subprocess, sys, time, urllib.request

FLOOR_GIB = float(os.environ.get("FLOOR_GIB", "12"))
INTERVAL = float(os.environ.get("INTERVAL", "3"))
LOG = os.environ.get("LOG", os.path.expanduser("~/.hermes/cache/scratch/vllm_mem_watch.log"))
CONTAINER = os.environ.get("CONTAINER", "vllm-agents")


def mem_available_gib():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024 / 1024
    return -1.0


def ollama_loaded():
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=2) as r:
            return [m["name"] for m in json.load(r).get("models", [])]
    except Exception:
        return []


def ollama_unload(name):
    body = json.dumps({"model": name, "keep_alive": 0}).encode()
    req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as exc:
        return str(exc)
    return "ok"


def container_state():
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}", CONTAINER],
                       capture_output=True, text=True)
    return r.stdout.strip() or "absent"


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    with open(LOG, "a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def main():
    low = 999.0
    log(f"start floor={FLOOR_GIB}GiB container={CONTAINER}")
    while True:
        avail = mem_available_gib()
        low = min(low, avail)
        state = container_state()
        log(f"avail={avail:.1f}GiB min={low:.1f}GiB {CONTAINER}={state}")
        for name in ollama_loaded():
            log(f"OLLAMA loaded {name} -> unloading: {ollama_unload(name)}")
        if avail < FLOOR_GIB and state == "running":
            log(f"FLOOR BREACH {avail:.1f} < {FLOOR_GIB} -> docker stop {CONTAINER}")
            subprocess.run(["docker", "stop", "-t", "5", CONTAINER])
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
