#!/usr/bin/env bash
#
# bench_full_tuber_capacity.sh
#
# Fleet-portable benchmark: how many *full* tuber pipelines (virtual desktop
# + terminal render + video/audio encode) can this machine sustain
# concurrently? Unlike docs/hardware/encoding_only_minimum_cpu_8_tubers.md
# (encode step only, synthetic lavfi source), this drives the real
# production components side by side:
#
#   Xvfb (virtual desktop) -> xterm+tmux (the actual terminal renderer,
#   same as startup.sh) -> ffmpeg x11grab capture -> libx264 encode,
#   mixed with a PulseAudio null-sink audio source (same "vout" pattern as
#   audio_player.py / stream_supervisor.py's pulse_monitor_available()).
#
# Run on any Linux box in the fleet (native) or on a Windows box via WSL2
# (Xvfb needs a real X11/Linux environment — it will not run directly on
# Windows). Same script, same output format, on every machine — that's
# what makes results comparable across the fleet.
#
# ── One-time setup (needs root once per machine; the script itself never
#    calls sudo) ────────────────────────────────────────────────────────
#   sudo apt-get update && sudo apt-get install -y \
#       xvfb xterm tmux ffmpeg pulseaudio pulseaudio-utils
#
# ── Usage ──────────────────────────────────────────────────────────────
#   ./bench_full_tuber_capacity.sh                    # default sweep
#   RES=1280x720 BITRATE=2000 N_LIST="1 2 4 6 8" ./bench_full_tuber_capacity.sh
#
# On a Windows box via WSL where ffmpeg isn't installed in the Linux distro,
# WSL can still exec the Windows binary directly through interop — point
# FFMPEG at it instead of installing a second copy:
#   FFMPEG=/mnt/c/path/to/ffmpeg.exe ./bench_full_tuber_capacity.sh
# (Xvfb/xterm/tmux/pulseaudio have no such shortcut — those are genuinely
# Linux-only and still need the one-time apt-get install above for FULL mode.)
#
# Falls back to a proxy mode (synthetic lavfi video/audio, no real Xvfb/
# xterm/tmux/pulseaudio) if any of those tools are missing, so it still
# produces *some* number rather than hard-failing — but the header of its
# output says which mode ran. Proxy mode understates the real cost (no
# X server / terminal emulator overhead) — treat proxy-mode results as a
# lower bound, not the real answer, until the packages above are installed.
#
# Stops sweeping the first time a step fails to sustain realtime (mean
# speed < $MIN_SPEED) or trips $MAX_CPU_PCT total system load — that N-1
# is reported as this machine's sustained capacity.
#
set -uo pipefail

# ── Config (override via env) ───────────────────────────────────────────
RES="${RES:-1280x720}"
FPS="${FPS:-30}"
BITRATE="${BITRATE:-3000}"                # kbps, matches config/worker.yaml
DURATION="${DURATION:-20}"                # seconds per tuber per test step
N_LIST="${N_LIST:-1 2 4 6 8 10 12}"
MIN_SPEED="${MIN_SPEED:-0.98}"             # below this, this N is a fail
MAX_CPU_PCT="${MAX_CPU_PCT:-95}"           # total system %, this N is a fail
DISPLAY_BASE="${DISPLAY_BASE:-190}"        # avoid colliding with a real :N
WORKDIR="${WORKDIR:-/tmp/tuber_bench_$$}"
RESULTS_FILE="${RESULTS_FILE:-$WORKDIR/results.tsv}"
FFMPEG="${FFMPEG:-ffmpeg}"                 # override to a Windows ffmpeg.exe path under WSL interop if needed

mkdir -p "$WORKDIR"
PIDS=()   # every PID this script spawns, for cleanup + RSS accounting

cleanup() {
  for pid in "${PIDS[@]:-}"; do kill "$pid" >/dev/null 2>&1 || true; done
  wait 2>/dev/null || true
  pactl list short modules 2>/dev/null | grep null-sink | awk '{print $1}' | \
    while read -r m; do pactl unload-module "$m" >/dev/null 2>&1 || true; done
  rm -rf "$WORKDIR"
}
trap cleanup EXIT INT TERM

log() { echo "[bench] $*" >&2; }

# ── Capability probe ─────────────────────────────────────────────────────
have() { command -v "$1" >/dev/null 2>&1; }
have "$FFMPEG" || { echo "[bench] FATAL: \$FFMPEG ('$FFMPEG') not found/executable — set FFMPEG=/path/to/ffmpeg(.exe)" >&2; exit 1; }
FULL_MODE=1
for tool in Xvfb xterm tmux pulseaudio pactl paplay; do
  have "$tool" || { FULL_MODE=0; log "missing: $tool -> falling back to proxy mode"; }
done

if [ "$FULL_MODE" = 1 ]; then
  export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-$WORKDIR/xdgrt}"
  mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
  pulseaudio -D --exit-idle-time=-1 --disallow-exit >/dev/null 2>&1 || \
    { log "pulseaudio daemon failed to start -> falling back to proxy mode"; FULL_MODE=0; }
fi

log "mode: $([ "$FULL_MODE" = 1 ] && echo FULL || echo PROXY) — res=${RES} fps=${FPS} bitrate=${BITRATE}k"

# ── Host header (so results are self-describing when compared later) ────
{
  echo "# host: $(hostname)"
  echo "# date: $(date -Iseconds)"
  echo "# cpu: $(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2 | sed 's/^ *//')"
  echo "# cores(logical): $(nproc)"
  echo "# ram_total_mb: $(free -m 2>/dev/null | awk '/^Mem:/{print $2}')"
  echo "# mode: $([ "$FULL_MODE" = 1 ] && echo FULL || echo PROXY)"
  echo "# res=${RES} fps=${FPS} bitrate=${BITRATE}k duration=${DURATION}s"
  printf "N\tmean_speed\tmin_speed\ttotal_cpu_pct\ttotal_rss_mb\tverdict\n"
} | tee "$RESULTS_FILE"

# ── CPU sampling (portable — /proc/stat deltas, no extra packages) ──────
read_cpu_jiffies() {
  read -r _ user nice system idle iowait irq softirq steal _ < /proc/stat
  echo "$((user+nice+system+irq+softirq+steal)) $((user+nice+system+irq+softirq+steal+idle+iowait))"
}
cpu_pct_over() { # $1 = seconds to sample over
  read -r b1 t1 < <(read_cpu_jiffies)
  sleep "$1"
  read -r b2 t2 < <(read_cpu_jiffies)
  awk -v b1="$b1" -v t1="$t1" -v b2="$b2" -v t2="$t2" \
    'BEGIN{ d=t2-t1; if (d<=0){print 0} else {printf "%.1f", (b2-b1)/d*100} }'
}

start_tuber_full() { # $1 = index
  local i="$1" disp="$((DISPLAY_BASE + $1))" res_x res_y cols rows
  res_x="${RES%x*}"; res_y="${RES#*x}"
  Xvfb ":${disp}" -screen 0 "${RES}x24" -ac +extension GLX >/dev/null 2>&1 &
  PIDS+=($!)
  for _ in $(seq 1 20); do [ -e "/tmp/.X11-unix/X${disp}" ] && break; sleep 0.1; done

  tmux -L "bench_${i}" new-session -d -s "t${i}" \
    "while true; do echo \"[\$(date +%T)] \$(shuf -n1 -e 'Hello there.' 'The story continues.' 'A quiet moment passes.' 'Something happens off-screen.' 'Narration line number '\$RANDOM)\"; sleep 0.\$((RANDOM % 9 + 2)); done"
  cols=$((res_x / 9)); rows=$((res_y / 18))
  DISPLAY=":${disp}" xterm -geometry "${cols}x${rows}+0+0" -bw 0 -fa Monospace -fs 11 \
    -bg black -fg white -e tmux -L "bench_${i}" attach -t "t${i}" >/dev/null 2>&1 &
  PIDS+=($!)

  pactl load-module module-null-sink "sink_name=vout${i}" \
    "sink_properties=device.description=vout${i}" >/dev/null 2>&1
  "$FFMPEG" -hide_banner -loglevel error -re -f lavfi -i "sine=frequency=220:sample_rate=44100" \
    -f pulse -device "vout${i}" narration_feed >/dev/null 2>&1 &
  PIDS+=($!)

  sleep 0.5   # let Xvfb/xterm/pulse settle before capture starts, same as a real worker's boot order
  "$FFMPEG" -hide_banner -y -f x11grab -video_size "${RES}" -framerate "${FPS}" -i ":${disp}" \
    -f pulse -i "vout${i}.monitor" \
    -c:v libx264 -preset veryfast -tune zerolatency -b:v "${BITRATE}k" -maxrate "${BITRATE}k" \
    -bufsize "$((BITRATE*2))k" -pix_fmt yuv420p -g 60 -c:a aac -b:a 128k -ar 44100 \
    -t "${DURATION}" -f null - > "${WORKDIR}/enc_${i}.log" 2>&1 &
  PIDS+=($!)
  echo $!
}

start_tuber_proxy() { # $1 = index — no Xvfb/xterm/tmux/pulseaudio, pure ffmpeg lavfi
  local i="$1"
  "$FFMPEG" -hide_banner -y -re \
    -f lavfi -i "testsrc2=size=${RES}:rate=${FPS}" \
    -f lavfi -i "sine=frequency=220:sample_rate=44100" \
    -c:v libx264 -preset veryfast -tune zerolatency -b:v "${BITRATE}k" -maxrate "${BITRATE}k" \
    -bufsize "$((BITRATE*2))k" -pix_fmt yuv420p -g 60 -c:a aac -b:a 128k -ar 44100 \
    -t "${DURATION}" -f null - > "${WORKDIR}/enc_${i}.log" 2>&1 &
  PIDS+=($!)
  echo $!
}

stop_all_for_step() {
  for pid in "${PIDS[@]:-}"; do kill "$pid" >/dev/null 2>&1 || true; done
  wait 2>/dev/null || true
  pactl list short modules 2>/dev/null | grep null-sink | awk '{print $1}' | \
    while read -r m; do pactl unload-module "$m" >/dev/null 2>&1 || true; done
  tmux -L bench_ls ls >/dev/null 2>&1  # no-op, keeps set -e-free style consistent
  for s in "${WORKDIR}"/tmux_socks_unused; do :; done
  PIDS=()
}

run_step() { # $1 = N
  local n="$1" enc_pids=()
  rm -f "${WORKDIR}"/enc_*.log
  for i in $(seq 1 "$n"); do
    if [ "$FULL_MODE" = 1 ]; then enc_pids+=("$(start_tuber_full "$i")")
    else enc_pids+=("$(start_tuber_proxy "$i")"); fi
  done

  # sample total CPU for roughly the test duration while streams run
  cpu_pct=$(cpu_pct_over "$((DURATION - 2 > 3 ? DURATION - 2 : 3))")

  wait 2>/dev/null || true

  # collect per-stream speed
  local speeds=() rss_total=0
  for i in $(seq 1 "$n"); do
    local sp
    sp=$(tr '\r' '\n' < "${WORKDIR}/enc_${i}.log" 2>/dev/null | grep -o 'speed=[0-9.]*x' | tail -1 | tr -d 'speedx=')
    [ -z "$sp" ] && sp="0"
    speeds+=("$sp")
  done
  for pid in "${PIDS[@]:-}"; do
    rss_total=$((rss_total + $(ps -o rss= -p "$pid" 2>/dev/null | tr -d ' ' || echo 0)))
  done
  rss_mb=$(awk -v r="$rss_total" 'BEGIN{printf "%.0f", r/1024}')

  mean_speed=$(printf '%s\n' "${speeds[@]}" | awk '{s+=$1; c++} END{if(c>0) printf "%.3f", s/c; else print 0}')
  min_speed=$(printf '%s\n' "${speeds[@]}" | sort -n | head -1)

  stop_all_for_step

  verdict="pass"
  awk -v m="$mean_speed" -v min="$MIN_SPEED" 'BEGIN{exit !(m<min)}' && verdict="FAIL(speed)"
  awk -v c="$cpu_pct" -v max="$MAX_CPU_PCT" 'BEGIN{exit !(c>max)}' && verdict="FAIL(cpu)"

  printf "%s\t%s\t%s\t%s\t%s\t%s\n" "$n" "$mean_speed" "$min_speed" "$cpu_pct" "$rss_mb" "$verdict" | tee -a "$RESULTS_FILE"

  [ "$verdict" = "pass" ]
}

last_good=0
for n in $N_LIST; do
  log "=== testing N=${n} ==="
  if run_step "$n"; then last_good="$n"; else log "N=${n} failed — stopping sweep"; break; fi
done

log "sustained capacity on this machine: N=${last_good} (mode: $([ "$FULL_MODE" = 1 ] && echo FULL || echo PROXY))"
echo "# sustained_capacity: ${last_good}" >> "$RESULTS_FILE"
log "full results: ${RESULTS_FILE} (copy this file off before the workdir is cleaned up)"
cp "$RESULTS_FILE" "./bench_result_$(hostname)_$(date +%Y%m%d_%H%M%S).tsv" 2>/dev/null && \
  log "also copied to ./bench_result_$(hostname)_*.tsv in the current directory"
