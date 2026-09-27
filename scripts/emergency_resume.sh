#!/usr/bin/env bash
# emergency_resume.sh — undo scripts/emergency_stop.sh: remove the local kill
# file from worker container(s) so they return to Redis on/off control.
#
# Usage:
#   scripts/emergency_resume.sh            # all running worker-* containers
#   scripts/emergency_resume.sh coder gm   # specific workers
#
# Removing the kill file does NOT force a worker on: it streams again only if
# its Redis flag (worker:<id>:enabled) isn't "0" — or Redis is unreachable,
# in which case the normal fail-open rule applies. Idempotent.
exec "$(dirname "$0")/emergency_stop.sh" --resume "$@"
