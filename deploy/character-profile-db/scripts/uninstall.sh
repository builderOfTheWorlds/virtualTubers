#!/bin/bash
#
# character-profile-db — remove what install.sh set up on mafober
#
# Safe by default: removes only the CT 101 bind mount that install.sh added.
# The ZFS dataset (every character, fragment and job row) is kept unless you
# pass --purge-data AND type the dataset name to confirm. Fragments are LLM
# output that cannot be regenerated — take a backup before purging.
#
# Delete the Portainer stack first (Stacks -> character-profile-db -> Delete);
# this script refuses to run while either of its containers still exists.
#
# Usage (as root on mafober):
#   ./uninstall.sh                remove the bind mount, keep the data
#   ./uninstall.sh --purge-data   also destroy the dataset and its snapshots
#   ./uninstall.sh --dry-run      print what would change, change nothing
#
# Overrides via environment: CT_ID, DATASET (must match install.sh).

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────
CT_ID="${CT_ID:-101}"
DATASET="${DATASET:-tank_0/utilities/character-profile-db}"
CT_PATH="/${DATASET}"
CONTAINERS=("character-profile-db" "character-profile-db-init")

# ── Colors / Logging ─────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

usage() {
    cat <<EOF
Usage: $0 [--purge-data] [--dry-run]

  (no flags)     remove the CT $CT_ID bind mount, keep the ZFS dataset
  --purge-data   also destroy $DATASET and its snapshots (typed confirmation)
  --dry-run      print what would change, change nothing
EOF
}

# ── Arguments ────────────────────────────────────────────────────────────────
DRY_RUN=0
PURGE_DATA=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)    DRY_RUN=1 ;;
        --purge-data) PURGE_DATA=1 ;;
        -h|--help)    usage; exit 0 ;;
        *)            log_error "Unknown option: $1"; usage; exit 1 ;;
    esac
    shift
done

# ── Preconditions ────────────────────────────────────────────────────────────
if [[ $(id -u) -ne 0 ]]; then
    log_error "This script must be run as root on the Proxmox host (use sudo)"
    exit 1
fi

for cmd in pct zfs; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        log_error "'$cmd' not found — run this on the Proxmox host (mafober), not inside CT $CT_ID"
        exit 1
    fi
done

# ── Helpers ──────────────────────────────────────────────────────────────────
run() {
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "  [dry-run] $*"
    else
        "$@"
    fi
}

ct_running()       { pct status "$CT_ID" 2>/dev/null | grep -q 'status: running'; }
dataset_exists()   { zfs list -H -o name "$DATASET" >/dev/null 2>&1; }
host_path()        { zfs get -H -o value mountpoint "$DATASET"; }
mount_live_in_ct() { pct exec "$CT_ID" -- mountpoint -q "$CT_PATH" >/dev/null 2>&1; }

find_mp_key() {
    pct config "$CT_ID" | awk -v src="$1" '
        /^mp[0-9]+:/ && !found {
            key = $1; sub(/:$/, "", key)
            spec = $0; sub(/^mp[0-9]+:[ \t]*/, "", spec)
            split(spec, parts, ",")
            if (parts[1] == src) { print key; found = 1 }
        }'
}

# ── Uninstall ────────────────────────────────────────────────────────────────
log_info "Removing character-profile-db storage wiring (CT $CT_ID, dataset $DATASET)"
if [[ $DRY_RUN -eq 1 ]]; then
    log_warn "Dry run — nothing will be changed"
fi

if ! ct_running; then
    log_error "CT $CT_ID is not running — start it so this script can check the stack is gone"
    exit 1
fi

# 1. The stack must be gone: removing storage under a live Postgres corrupts it.
for name in "${CONTAINERS[@]}"; do
    if pct exec "$CT_ID" -- docker inspect "$name" >/dev/null 2>&1; then
        log_error "Container $name still exists in CT $CT_ID."
        log_error "Delete the character-profile-db stack in Portainer first, then re-run."
        exit 1
    fi
done
log_info "No character-profile-db containers in CT $CT_ID"

if dataset_exists; then
    hp=$(host_path)
else
    hp="/$DATASET"
fi

# 2. Bind mount
key=$(find_mp_key "$hp")
if [[ -n "$key" ]]; then
    log_info "Removing $key ($hp) from CT $CT_ID"
    run pct set "$CT_ID" -delete "$key"
else
    log_info "CT $CT_ID has no mount point for $hp"
fi

# 3. Data
if ! dataset_exists; then
    log_info "ZFS dataset $DATASET does not exist — no data to keep or purge"
elif [[ $PURGE_DATA -eq 0 ]]; then
    log_info "Kept ZFS dataset $DATASET ($(zfs list -H -o used "$DATASET") used)"
    log_info "Destroy it later with: $0 --purge-data"
else
    echo ""
    log_warn "--purge-data destroys the character database and every snapshot of it:"
    zfs list -r -t filesystem,snapshot -o name,used,creation "$DATASET"
    echo ""
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "  [dry-run] zfs destroy -r $DATASET"
    elif mount_live_in_ct; then
        log_error "$CT_PATH is still mounted inside CT $CT_ID (removed mounts stay until the CT"
        log_error "reboots). Reboot CT $CT_ID at a quiet time, then re-run with --purge-data."
        exit 1
    else
        read -r -p "Type the dataset name ($DATASET) to destroy it: " answer
        if [[ "$answer" != "$DATASET" ]]; then
            log_error "Not confirmed — the dataset was kept"
            exit 1
        fi
        zfs destroy -r "$DATASET"
        log_info "Destroyed $DATASET"
    fi
fi

if [[ $DRY_RUN -eq 0 ]] && [[ -n "$key" ]] && mount_live_in_ct; then
    log_warn "$CT_PATH stays mounted inside CT $CT_ID until its next reboot (harmless)."
fi

echo ""
log_info "character-profile-db uninstall finished"
