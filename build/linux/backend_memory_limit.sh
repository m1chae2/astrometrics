#!/usr/bin/env bash
# backend_memory_limit.sh
# Purpose: helper functions, sourced by run_backend.sh, that run the backend
# inside a systemd user scope with a memory limit.
#
# Why: on 2026-09-25 the backend grew to 15-22 GB (whole-catalog reloads) and
# systemd-oomd killed the whole desktop session. A limit on the backend's own
# scope makes the kernel kill only the backend's process group instead.
# Siril and other child processes start inside the same scope, so the limit
# covers them too.
#
# Limits (this machine has 32 GB of RAM):
#   MemoryMax  = 90% of RAM (hard limit; the kernel kills a process above it)
#   MemoryHigh = 80% of RAM (soft limit; above it the kernel slows the scope
#                            and reclaims memory first, so a short peak
#                            does not kill anything)
# Why these fractions: the largest legitimate use on record is 26.7 GB plus
# 2.1 GB of swap, the whole-library restack of 2026-10-11 (peak seen in the
# Vega target; recorded in the batch performance plan notes, not in the repo).
# That is 82% of 32 GB.
# The 15-22 GB runaway of 2026-09-25 was the bug, not real work. 90% keeps the peak
# under the kill line, and still leaves about 3 GB for the desktop, the
# window manager and the kernel. 80% starts throttling just below that peak.
#
# Environment overrides (values are systemd sizes such as 24G, 24000M or bytes):
#   ASTROMETRICS_BACKEND_MEMORY_MAX   hard limit; "off" turns the limit off
#   ASTROMETRICS_BACKEND_MEMORY_HIGH  soft limit; "off" leaves it unset
#
# Without a usable systemd user session, or with the limit turned off, the
# backend starts normally and one line says why.

# Prints the total RAM in bytes, or nothing when it cannot be read.
backend_total_ram_bytes() {
  awk '/^MemTotal:/ { print $2 * 1024; exit }' /proc/meminfo 2>/dev/null
}

# Sets BACKEND_SCOPE_PREFIX (an array) to the command words that run a command
# in a limited scope, and BACKEND_SCOPE_UNIT to the scope's name. Both are
# empty when no limit applies. One line is printed in that case.
# Argument: a suffix that makes the unit name unique.
prepare_backend_scope() {
  BACKEND_SCOPE_PREFIX=()
  BACKEND_SCOPE_UNIT=""
  local max_limit="${ASTROMETRICS_BACKEND_MEMORY_MAX:-}"
  local high_limit="${ASTROMETRICS_BACKEND_MEMORY_HIGH:-}"

  if [ "$max_limit" = "off" ]; then
    echo "Backend memory limit: off (ASTROMETRICS_BACKEND_MEMORY_MAX=off)."
    return 0
  fi
  if ! command -v systemd-run >/dev/null 2>&1 \
      || ! systemd-run --user --scope --quiet -p MemoryMax=infinity true >/dev/null 2>&1; then
    echo "Backend memory limit: none (no systemd user session; starting without a limit)."
    return 0
  fi

  local total_bytes
  total_bytes="$(backend_total_ram_bytes)"
  if [ -z "$max_limit" ] || { [ -z "$high_limit" ] && [ "${ASTROMETRICS_BACKEND_MEMORY_HIGH:-}" != "off" ]; }; then
    if [ -z "$total_bytes" ]; then
      echo "Backend memory limit: none (could not read the RAM size; starting without a limit)."
      return 0
    fi
  fi
  [ -n "$max_limit" ] || max_limit="$((total_bytes * 90 / 100))"
  if [ "$high_limit" = "off" ]; then
    high_limit=""
  elif [ -z "$high_limit" ]; then
    high_limit="$((total_bytes * 80 / 100))"
  fi

  BACKEND_SCOPE_UNIT="astrometrics-backend-$1.scope"
  # --slice puts the scope in astrometrics.slice, the one group that also holds Vite and Electron
  # (see astrometrics_slice_prefix in run_astrometrics.sh).
  BACKEND_SCOPE_PREFIX=(systemd-run --user --scope --quiet "--slice=astrometrics.slice" "--unit=$BACKEND_SCOPE_UNIT" -p "MemoryMax=$max_limit")
  if [ -n "$high_limit" ]; then
    BACKEND_SCOPE_PREFIX+=(-p "MemoryHigh=$high_limit")
  fi
  echo "Backend memory limit: MemoryMax=$max_limit, MemoryHigh=${high_limit:-unset} (scope $BACKEND_SCOPE_UNIT)."
}

# Prints a line when the kernel's out-of-memory killer killed a process in the
# given scope. Looks in the user journal: `journalctl --user -u <unit>`.
# Argument: the scope name.
backend_scope_oom_report() {
  local unit="$1"
  [ -n "$unit" ] || return 0
  command -v journalctl >/dev/null 2>&1 || return 0
  if journalctl --user -u "$unit" --no-pager -q 2>/dev/null | grep -qi "oom"; then
    echo "The kernel's out-of-memory killer stopped a process of the backend ($unit)."
    echo "  Details: journalctl --user -u $unit"
  fi
}
