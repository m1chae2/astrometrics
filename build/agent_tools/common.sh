# Shared setup for the agent tools: choose the worktree and share the heavy-work slots.
#
# Sourced by check.sh, realrun.sh and suite.sh as `source common.sh <worktree>`, where <worktree> is
# the folder name of a checkout beside this one under the same parent folder (for example
# `astrometrics` or `astrometrics-s4`). Heavy work (pytest, real-data scripts) runs in one of a
# fixed number of slots shared by every agent on the machine, so several agents cannot run out of
# memory together.
#
# Settings (environment variables, all optional):
#   ASTROMETRICS_AGENT_LOCK_DIR  folder holding the slot lock files (default /tmp/astrometrics-agent-locks)
#   ASTROMETRICS_AGENT_SLOTS     how many heavy runs may run at once (default 2)

TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCK_DIR="${ASTROMETRICS_AGENT_LOCK_DIR:-/tmp/astrometrics-agent-locks}"
SLOTS="${ASTROMETRICS_AGENT_SLOTS:-2}"
mkdir -p "$LOCK_DIR"

case "$1" in
  astrometrics | astrometrics-*) ;;
  *) echo "first argument must be a checkout folder name such as astrometrics or astrometrics-s4" >&2; exit 2 ;;
esac
REPOS="$(cd "$TOOLS/../../.." && pwd)"
WT="$REPOS/$1"
[ -e "$WT/.git" ] || { echo "no checkout at $WT" >&2; exit 2; }
cd "$WT" || exit 2
PY="$WT/.venv/bin"

# Run "$@" in the first free heavy-work slot, waiting while all are busy.
heavy() {
  echo "[waiting for a heavy-work slot]" >&2
  while true; do
    for slot in $(seq 1 "$SLOTS"); do
      flock -n -E 75 "$LOCK_DIR/slot.$slot.lock" "$@"
      rc=$?
      [ $rc -ne 75 ] && return $rc
    done
    sleep 5
  done
}

# Run "$@" holding every slot (a full suite).
heavy_all() {
  echo "[waiting for every heavy-work slot]" >&2
  local command=("$@")
  for slot in $(seq "$SLOTS" -1 1); do
    command=(flock "$LOCK_DIR/slot.$slot.lock" "${command[@]}")
  done
  "${command[@]}"
}
