#!/bin/bash
# Off-host test of deploy/character-profile-db/scripts/{install,uninstall}.sh
# against mocked pct/zfs/id/stat/chown/timeout (no root or Proxmox needed).
# Run from anywhere: bash deploy/character-profile-db/tests/run_tests.sh
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../scripts" && pwd)"
# Git on Windows doesn't record the executable bit, so set it here: a mock
# that isn't executable fails with "Permission denied" instead of running.
chmod +x "$HERE"/mockbin/*
export PATH="$HERE/mockbin:$PATH"
DS="tank_0/utilities/character-profile-db"

PASS=0
FAIL=0
check() {  # check <description> <command...>
    local desc="$1"; shift
    if "$@"; then PASS=$((PASS + 1)); echo "  ok   $desc"
    else FAIL=$((FAIL + 1)); echo "  FAIL $desc"; fi
}

fresh_state() {
    export MOCK_STATE
    MOCK_STATE="$(mktemp -d)"
    : > "$MOCK_STATE/calls"
    : > "$MOCK_STATE/containers"
    # mafober's real CT 101 already uses mp0..mp6
    printf 'mp%d: /tank_0/x%d,mp=/tank_0/x%d\n' 0 0 0 1 1 1 2 2 2 3 3 3 4 4 4 5 5 5 6 6 6 > "$MOCK_STATE/ctconfig"
    echo "tank_0/utilities" > "$MOCK_STATE/datasets"
}

run_script() {  # run_script <script> <stdin> [args...] ; sets RC and OUT
    local script="$1" input="$2"; shift 2
    OUT="$(printf '%s\n' "$input" | bash "$REPO/$script" "$@" 2>&1)"
    RC=$?
}

mutations() { grep -E '^(zfs (create|destroy)|pct (set|reboot)|chown)' "$MOCK_STATE/calls" || true; }
has_line()  { grep -q -- "$2" <<<"$1"; }

echo "== syntax =="
check "install.sh parses"   bash -n "$REPO/install.sh"
check "uninstall.sh parses" bash -n "$REPO/uninstall.sh"

echo "== 1. fresh install, mount not live yet, no --reboot-ct =="
fresh_state
run_script install.sh ""
check "exits 3 (stop before deploy)"        test "$RC" -eq 3
check "dataset created with 16K records"    grep -q "zfs create -o recordsize=16K -o atime=off $DS" "$MOCK_STATE/calls"
check "owner set to 999:999"                grep -qx "999:999" "$MOCK_STATE/owner"
check "mount added at next free index mp7"  grep -q "^mp7: $MOCK_STATE/fs/$DS,mp=/$DS$" "$MOCK_STATE/ctconfig"
check "warns not to deploy yet"             has_line "$OUT" "Do NOT deploy the stack"
check "no reboot without the flag"          bash -c "! grep -q 'pct reboot' '$MOCK_STATE/calls'"

echo "== 2. re-run is idempotent =="
: > "$MOCK_STATE/calls"
run_script install.sh ""
check "still exits 3"                       test "$RC" -eq 3
check "no mutations on re-run"              test -z "$(mutations)"
check "reports existing dataset"            has_line "$OUT" "already exists"
check "reports existing mount key"          has_line "$OUT" "already has mp7"
check "only one mp line for the dataset"    test "$(grep -c "character-profile-db" "$MOCK_STATE/ctconfig")" -eq 1

echo "== 3. --reboot-ct, wrong confirmation =="
run_script install.sh "yes" --reboot-ct
check "exits 3"                             test "$RC" -eq 3
check "did not reboot"                      bash -c "! grep -q 'pct reboot' '$MOCK_STATE/calls'"

echo "== 4. --reboot-ct, typed confirmation =="
run_script install.sh "reboot 101" --reboot-ct
check "exits 0"                             test "$RC" -eq 0
check "rebooted CT 101"                     grep -q "pct reboot 101" "$MOCK_STATE/calls"
check "prints Portainer next steps"         has_line "$OUT" "Stacks -> Add stack"

echo "== 5. --dry-run on a fresh host changes nothing =="
fresh_state
run_script install.sh "" --dry-run
check "exits 0"                             test "$RC" -eq 0
check "no mutations"                        test -z "$(mutations)"
check "shows the zfs create it would run"   has_line "$OUT" "\[dry-run\] zfs create"
check "shows the pct set it would run"      has_line "$OUT" "\[dry-run\] pct set 101 -mp7"

echo "== 6. CT stopped =="
fresh_state
touch "$MOCK_STATE/ct_stopped"
run_script install.sh ""
check "exits 1"                             test "$RC" -eq 1
check "no mutations"                        test -z "$(mutations)"

echo "== 7. --verify, everything healthy =="
fresh_state
run_script install.sh "reboot 101" --reboot-ct >/dev/null
printf '%s\n' character-profile-db character-profile-db-init > "$MOCK_STATE/containers"
mkdir -p "$MOCK_STATE/fs/$DS/pgdata" && echo 16 > "$MOCK_STATE/fs/$DS/pgdata/PG_VERSION"
run_script install.sh "" --verify
check "exits 0"                             test "$RC" -eq 0
check "prints connection settings"          has_line "$OUT" "CHARACTER_DB_PORT=5433"
check "warns about the filtered port"       has_line "$OUT" "not reachable from this host"

echo "== 8. --verify, init failed and data not on dataset =="
echo "exited 1" > "$MOCK_STATE/initstate"
rm -f "$MOCK_STATE/fs/$DS/pgdata/PG_VERSION"
run_script install.sh "" --verify
check "exits 1"                             test "$RC" -eq 1
check "reports the init failure"            has_line "$OUT" "FAIL character-profile-db-init"
check "reports data not on dataset"         has_line "$OUT" "is not writing to"

echo "== 9. uninstall while the stack still exists =="
: > "$MOCK_STATE/calls"
run_script uninstall.sh ""
check "exits 1"                             test "$RC" -eq 1
check "no mutations"                        test -z "$(mutations)"
check "tells you to delete the stack"       has_line "$OUT" "Delete the character-profile-db stack"

echo "== 10. uninstall (stack deleted): removes mount, keeps data =="
: > "$MOCK_STATE/containers"
run_script uninstall.sh ""
check "exits 0"                             test "$RC" -eq 0
check "mount point removed"                 bash -c "! grep -q character-profile-db '$MOCK_STATE/ctconfig'"
check "other mount points untouched"        test "$(grep -c '^mp' "$MOCK_STATE/ctconfig")" -eq 7
check "dataset kept"                        grep -qx "$DS" "$MOCK_STATE/datasets"
check "no destroy"                          bash -c "! grep -q 'zfs destroy' '$MOCK_STATE/calls'"

echo "== 11. --purge-data while the old mount is still live in the CT =="
run_script uninstall.sh "$DS" --purge-data
check "exits 1"                             test "$RC" -eq 1
check "dataset kept"                        grep -qx "$DS" "$MOCK_STATE/datasets"
check "asks for a CT reboot first"          has_line "$OUT" "still mounted inside CT"

echo "== 12. --purge-data after the reboot, wrong confirmation =="
rm -f "$MOCK_STATE/mounted"
run_script uninstall.sh "nope" --purge-data
check "exits 1"                             test "$RC" -eq 1
check "dataset kept"                        grep -qx "$DS" "$MOCK_STATE/datasets"

echo "== 13. --purge-data --dry-run =="
: > "$MOCK_STATE/calls"
run_script uninstall.sh "" --purge-data --dry-run
check "exits 0"                             test "$RC" -eq 0
check "no mutations"                        test -z "$(mutations)"
check "shows the destroy it would run"      has_line "$OUT" "\[dry-run\] zfs destroy -r $DS"

echo "== 14. --purge-data, typed dataset name =="
run_script uninstall.sh "$DS" --purge-data
check "exits 0"                             test "$RC" -eq 0
check "dataset destroyed"                   bash -c "! grep -qx '$DS' '$MOCK_STATE/datasets'"
check "parent dataset untouched"            grep -qx "tank_0/utilities" "$MOCK_STATE/datasets"

echo "== 15. bad option =="
run_script install.sh "" --bogus
check "install.sh rejects unknown option"   test "$RC" -eq 1
run_script uninstall.sh "" --bogus
check "uninstall.sh rejects unknown option" test "$RC" -eq 1

echo ""
echo "passed: $PASS  failed: $FAIL"
[[ $FAIL -eq 0 ]]
