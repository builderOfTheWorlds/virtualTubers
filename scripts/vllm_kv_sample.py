#!/usr/bin/env python3
"""Sample vllm:kv_cache_usage_perc + running/waiting requests every N s (metrics need no key)."""
import re, sys, time, urllib.request
url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8092/metrics"
interval = float(sys.argv[2]) if len(sys.argv) > 2 else 2
want = ("vllm:kv_cache_usage_perc", "vllm:num_requests_running", "vllm:num_requests_waiting")
peak = {}
while True:
    try:
        text = urllib.request.urlopen(url, timeout=5).read().decode()
        vals = {}
        for line in text.splitlines():
            for w in want:
                if line.startswith(w):
                    vals[w.split(":")[1]] = float(line.rsplit(" ", 1)[1])
        for k, v in vals.items():
            peak[k] = max(peak.get(k, 0), v)
        print(time.strftime("%H:%M:%S"), vals, "peak", peak, flush=True)
    except Exception as exc:
        print(time.strftime("%H:%M:%S"), "err", exc, flush=True)
    time.sleep(interval)
