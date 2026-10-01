#!/usr/bin/env bash
# Poll one generator job until it finishes or MAX_S elapses, printing job
# progress next to memory, Ollama, vLLM health and watchdog state each tick.
# Usage: watch_job.sh <job_id> [max_seconds] [interval]
JOB="$1"; MAX_S="${2:-280}"; IV="${3:-20}"
D=/home/secus/environments/argyreServer/deployments/vllm-hermes3-70b
HERE="$(cd "$(dirname "$0")" && pwd)"
end=$(( $(date +%s) + MAX_S ))
while :; do
  j=$(curl -s --max-time 5 "http://localhost:8001/jobs/$JOB")
  st=$(echo "$j" | python3 "$HERE/job_status_line.py")
  wd=$(pgrep -f "mem_watchdog.sh --kill" >/dev/null && echo up || echo DOWN)
  echo "$(date +%T) $st | avail=$(awk '/MemAvailable/ {printf "%.1f", $2/1048576}' /proc/meminfo)GiB vllm=$(docker inspect -f '{{.State.Health.Status}}' vllm-hermes3-70b 2>/dev/null) ollama=[$(ollama ps 2>/dev/null | awk 'NR>1{print $1}' | tr '\n' ' ')] wd=$wd"
  case "$st" in completed*|failed*|cancelled*) echo "$j" | python3 "$HERE/job_status_line.py" --final; break;; esac
  [ "$wd" = DOWN ] && { echo "WATCHDOG DOWN"; tail -3 $D/mem_watchdog.log; break; }
  [ $(date +%s) -ge $end ] && break
  sleep "$IV"
done
