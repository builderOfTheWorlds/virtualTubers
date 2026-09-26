#!/bin/bash
#
# character-profile-db — storage setup on mafober (the Proxmox VE host)
#
# The pgvector Postgres for the character generator/updater runs as a
# Portainer stack inside CT 101 (the Docker LXC, 192.168.1.120). Its data lives
# on its own ZFS dataset, bind-mounted into CT 101, so it survives CT rebuilds
# (mafober_summary.md, lesson 8). This script prepares that storage and checks
# the result; the stack itself is deployed from Portainer (README.md).
#
#   1. zfs create tank_0/utilities/character-profile-db
#   2. chown it to 999:999 (the postgres user in the pgvector Debian image)
#   3. pct set 101 -mpN <dataset>,mp=<same path>  (next free mount-point index)
#   4. confirm the mount is live inside CT 101; reboot CT 101 only on request
#
# Idempotent: re-running skips whatever is already in place.
#
# Usage (as root on mafober):
#   ./install.sh               prepare the storage
#   ./install.sh --dry-run     print what would change, change nothing
#   ./install.sh --reboot-ct   also reboot CT 101 if the mount is not live yet
#                              (asks for a typed confirmation first)
#   ./install.sh --verify      after deploying the stack: health-check it all
#
# Overrides via environment: CT_ID, DATASET, CT_IP, DB_PORT,
# CHARACTER_DB_NAME, CHARACTER_DB_USER (defaults below).

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────
CT_ID="${CT_ID:-101}"
DATASET="${DATASET:-tank_0/utilities/character-profile-db}"
CT_PATH="/${DATASET}"      # path inside CT 101 — docker-compose.yml binds this
CT_IP="${CT_IP:-192.168.1.120}"
DB_PORT="${DB_PORT:-5433}"
DB_UID=999                 # postgres user/group in pgvector/pgvector:*-bookworm
DB_GID=999
DB_CONTAINER="character-profile-db"
INIT_CONTAINER="character-profile-db-init"
APP_DB="${CHARACTER_DB_NAME:-character_profile}"
APP_USER="${CHARACTER_DB_USER:-character_profile}"

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
Usage: $0 [--dry-run] [--reboot-ct] [--verify]

  (no flags)    create the ZFS dataset, set its owner, bind-mount it into CT $CT_ID
  --dry-run     print what would change, change nothing
  --reboot-ct   reboot CT $CT_ID if the new mount is not live yet (typed confirmation)
  --verify      after deploying the Portainer stack: check storage, containers, database
EOF
}

# ── Arguments ────────────────────────────────────────────────────────────────
DRY_RUN=0
REBOOT_CT=0
MODE="install"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)   DRY_RUN=1 ;;
        --reboot-ct) REBOOT_CT=1 ;;
        --verify)    MODE="verify" ;;
        -h|--help)   usage; exit 0 ;;
        *)           log_error "Unknown option: $1"; usage; exit 1 ;;
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

for name in "$APP_DB" "$APP_USER"; do
    if [[ ! "$name" =~ ^[a-z_][a-z0-9_]*$ ]]; then
        log_error "Invalid database/role name '$name' (lowercase letters, digits, underscore)"
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

ct_running()     { pct status "$CT_ID" 2>/dev/null | grep -q 'status: running'; }
dataset_exists() { zfs list -H -o name "$DATASET" >/dev/null 2>&1; }
host_path()      { zfs get -H -o value mountpoint "$DATASET"; }

# True when CT_PATH is a mount point inside the CT (not just a directory on
# the parent dataset — the failure mode this whole script exists to avoid).
mount_live_in_ct() { pct exec "$CT_ID" -- mountpoint -q "$CT_PATH" >/dev/null 2>&1; }

# The mpN key whose source is $1, or nothing. awk reads to the end (no early
# exit) so pct never takes a SIGPIPE, which pipefail would turn into a failure.
find_mp_key() {
    pct config "$CT_ID" | awk -v src="$1" '
        /^mp[0-9]+:/ && !found {
            key = $1; sub(/:$/, "", key)
            spec = $0; sub(/^mp[0-9]+:[ \t]*/, "", spec)
            split(spec, parts, ",")
            if (parts[1] == src) { print key; found = 1 }
        }'
}

next_free_mp_index() {
    local used i
    used=$(pct config "$CT_ID" | sed -n 's/^mp\([0-9][0-9]*\):.*/\1/p')
    for ((i = 0; i < 256; i++)); do
        if ! grep -qx "$i" <<<"$used"; then
            echo "$i"
            return 0
        fi
    done
    return 1
}

reboot_ct() {
    local answer i
    log_warn "CT $CT_ID must be rebooted for the new mount to appear inside it."
    log_warn "That restarts EVERY container in CT $CT_ID: Portainer, Gitea, the stack-wide"
    log_warn "Postgres on :5432, Plex, qBittorrent, Grafana, Prometheus."
    read -r -p "Type 'reboot $CT_ID' to continue: " answer
    if [[ "$answer" != "reboot $CT_ID" ]]; then
        log_error "Not confirmed — nothing was rebooted"
        exit 3
    fi
    log_info "Rebooting CT $CT_ID..."
    pct reboot "$CT_ID"
    for ((i = 0; i < 60; i++)); do
        if ct_running && mount_live_in_ct; then
            log_info "CT $CT_ID is back and $CT_PATH is mounted inside it"
            return 0
        fi
        sleep 2
    done
    log_error "CT $CT_ID did not come back with $CT_PATH mounted within 120s (pct status $CT_ID)"
    exit 1
}

print_next_steps() {
    echo ""
    echo "=========================================================="
    echo "  Storage ready for character-profile-db"
    echo "=========================================================="
    echo ""
    echo "Next:"
    echo "  1. Portainer (https://$CT_IP:9443) -> Stacks -> Add stack"
    echo "     Name: character-profile-db"
    echo "     Web editor: paste deploy/character-profile-db/docker-compose.yml"
    echo "     Environment variables -> Advanced mode: paste .env.example with real passwords"
    echo "     Deploy the stack"
    echo "  2. Back here: $0 --verify"
    echo ""
}

# ── Install ──────────────────────────────────────────────────────────────────
do_install() {
    local hp owner key idx

    log_info "Preparing storage for character-profile-db (CT $CT_ID, dataset $DATASET)"
    if [[ $DRY_RUN -eq 1 ]]; then
        log_warn "Dry run — nothing will be changed"
    fi

    if ! ct_running; then
        log_error "CT $CT_ID is not running (pct status $CT_ID)"
        exit 1
    fi

    # 1. Dataset. 16K records suit Postgres's 8K pages far better than the
    # 128K default (less read-modify-write); compression is inherited.
    if dataset_exists; then
        log_info "ZFS dataset $DATASET already exists"
    else
        log_info "Creating ZFS dataset $DATASET (recordsize=16K, atime=off)"
        run zfs create -o recordsize=16K -o atime=off "$DATASET"
    fi

    if dataset_exists; then
        hp=$(host_path)
    else
        hp="/$DATASET"   # dry run: not created yet
    fi

    # 2. Ownership (the directory itself only — never recursive over live data)
    owner=$(stat -c '%u:%g' "$hp" 2>/dev/null || echo "missing")
    if [[ "$owner" == "$DB_UID:$DB_GID" ]]; then
        log_info "$hp is already owned by $DB_UID:$DB_GID"
    else
        log_info "Setting owner of $hp to $DB_UID:$DB_GID (was $owner)"
        run chown "$DB_UID:$DB_GID" "$hp"
    fi

    # 3. Bind mount into the CT
    key=$(find_mp_key "$hp")
    if [[ -n "$key" ]]; then
        log_info "CT $CT_ID already has $key -> $hp"
    else
        if ! idx=$(next_free_mp_index); then
            log_error "No free mount-point index on CT $CT_ID"
            exit 1
        fi
        log_info "Adding mp$idx to CT $CT_ID: $hp -> $CT_PATH"
        run pct set "$CT_ID" "-mp$idx" "$hp,mp=$CT_PATH"
    fi

    if [[ $DRY_RUN -eq 1 ]]; then
        log_info "Dry run finished"
        return 0
    fi

    # 4. Live inside the CT?
    if mount_live_in_ct; then
        log_info "$CT_PATH is mounted inside CT $CT_ID"
    elif [[ $REBOOT_CT -eq 1 ]]; then
        reboot_ct
    else
        log_warn "The mount is configured but not live inside CT $CT_ID yet."
        log_warn "Do NOT deploy the stack until it is: Postgres would write into CT $CT_ID's"
        log_warn "view of the parent dataset instead of $DATASET."
        log_warn "At a quiet time, run:  $0 --reboot-ct"
        exit 3
    fi

    print_next_steps
}

# ── Verify ───────────────────────────────────────────────────────────────────
do_verify() {
    local hp out failed=0

    log_info "Verifying character-profile-db"

    if ! ct_running; then
        log_error "CT $CT_ID is not running"
        exit 1
    fi
    if ! dataset_exists; then
        log_error "ZFS dataset $DATASET not found — run $0 without flags first"
        exit 1
    fi
    hp=$(host_path)

    if mount_live_in_ct; then
        log_info "OK   $CT_PATH is a live mount inside CT $CT_ID"
    else
        log_error "FAIL $CT_PATH is not mounted inside CT $CT_ID"
        failed=1
    fi

    out=$(pct exec "$CT_ID" -- docker inspect -f '{{.State.Health.Status}}' "$DB_CONTAINER" 2>/dev/null || true)
    if [[ "$out" == "healthy" ]]; then
        log_info "OK   container $DB_CONTAINER is healthy"
    else
        log_error "FAIL container $DB_CONTAINER health: ${out:-container not found}"
        failed=1
    fi

    out=$(pct exec "$CT_ID" -- docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' "$INIT_CONTAINER" 2>/dev/null || true)
    if [[ "$out" == "exited 0" ]]; then
        log_info "OK   $INIT_CONTAINER finished (role, database, extension in place)"
    else
        log_error "FAIL $INIT_CONTAINER state: ${out:-container not found} (docker logs $INIT_CONTAINER)"
        failed=1
    fi

    out=$(pct exec "$CT_ID" -- docker exec "$DB_CONTAINER" psql -U postgres -d postgres -tAc \
        "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '$APP_DB'" 2>/dev/null || true)
    if [[ "$out" == "$APP_USER" ]]; then
        log_info "OK   database $APP_DB is owned by role $APP_USER"
    else
        log_error "FAIL database $APP_DB owner: ${out:-database missing} (expected $APP_USER)"
        failed=1
    fi

    out=$(pct exec "$CT_ID" -- docker exec "$DB_CONTAINER" psql -U postgres -d "$APP_DB" -tAc \
        "SELECT extversion FROM pg_extension WHERE extname = 'vector'" 2>/dev/null || true)
    if [[ -n "$out" ]]; then
        log_info "OK   pgvector $out is installed in $APP_DB"
    else
        log_error "FAIL pgvector is not installed in $APP_DB"
        failed=1
    fi

    if [[ -f "$hp/pgdata/PG_VERSION" ]]; then
        log_info "OK   data files are on the dataset ($hp/pgdata)"
    else
        log_error "FAIL $hp/pgdata/PG_VERSION not found — the database is not writing to $DATASET"
        failed=1
    fi

    if timeout 3 bash -c "exec 3<>/dev/tcp/$CT_IP/$DB_PORT" 2>/dev/null; then
        log_info "OK   $CT_IP:$DB_PORT accepts TCP connections from this host"
    else
        log_warn "$CT_IP:$DB_PORT is not reachable from this host. Other unused ports on"
        log_warn "$CT_IP are filtered today — check the firewall in front of CT $CT_ID."
    fi

    if [[ $failed -ne 0 ]]; then
        log_error "Verification failed"
        exit 1
    fi

    echo ""
    echo "=========================================================="
    echo "  character-profile-db is up"
    echo "=========================================================="
    echo ""
    echo "Connection settings for the character services:"
    echo "  CHARACTER_DB_HOST=$CT_IP"
    echo "  CHARACTER_DB_PORT=$DB_PORT"
    echo "  CHARACTER_DB_NAME=$APP_DB"
    echo "  CHARACTER_DB_USER=$APP_USER"
    echo "  CHARACTER_DB_PASSWORD=<the value set in Portainer>"
    echo ""
    echo "Useful commands (on mafober):"
    echo "  pct exec $CT_ID -- docker logs $DB_CONTAINER"
    echo "  pct exec $CT_ID -- docker exec -it $DB_CONTAINER psql -U $APP_USER -d $APP_DB"
    echo "  zfs list -o name,used,avail $DATASET"
    echo ""
}

if [[ "$MODE" == "verify" ]]; then
    do_verify
else
    do_install
fi
