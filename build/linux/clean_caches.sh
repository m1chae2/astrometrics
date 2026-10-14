#!/usr/bin/env bash
# clean_caches.sh
#
# Purpose: delete the disposable working files under ~/.cache/astrometrics.
#
# Usage: clean_caches.sh [--dry-run] [--all] [--include-master-cache] [--include-catalogs] [--include-rclone]
#
# Always kept: backup/ (database snapshots not yet uploaded), integrity/
# (Drive manifests), locks/ (used by running programs).
# Default: removes everything else in the cache folder, plus the old
# ~/.cache/siril folder if it exists. siril/MasterCache (calibration masters,
# slow to rebuild) is kept unless you pass --include-master-cache. catalogs/
# (downloaded Gaia star data, slow to fetch again) is kept unless you pass
# --include-catalogs.
# --include-rclone: also clears the rclone cache, but only when nothing is
# waiting to upload and the Drive mount is stopped.
# --all: --include-master-cache, --include-catalogs and --include-rclone.
# --dry-run: only print what would be removed.
# Env overrides (mostly for testing): XDG_CACHE_HOME, RCLONE_CACHE_DIR, DRIVE_MOUNT, BACKEND_PID_FILE.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT_DIR="$ROOT_DIR/build/linux"
CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"
CACHE_ROOT="$CACHE_HOME/astrometrics"
LEGACY_SIRIL="$CACHE_HOME/siril"
RCLONE_CACHE="${RCLONE_CACHE_DIR:-$HOME/.cache/rclone-vfs}"
DRIVE_MOUNT="${DRIVE_MOUNT:-$HOME/drive}"
PID_FILE="${BACKEND_PID_FILE:-$ROOT_DIR/.run_pids/backend.pid}"

dry_run=0
include_master=0
include_catalogs=0
include_rclone=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=1 ;;
    --include-master-cache) include_master=1 ;;
    --include-catalogs) include_catalogs=1 ;;
    --include-rclone) include_rclone=1 ;;
    --all) include_master=1; include_catalogs=1; include_rclone=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 64 ;;
  esac
done

total_kb=0

# remove_path <path> <allowed root>: delete one file/folder after safety checks.
remove_path() {
  local target="$1" allowed_root="$2" resolved resolved_root size_kb size_human
  [ -n "$target" ] && [ -n "$allowed_root" ] || { echo "Refusing: empty path." >&2; exit 1; }
  [ -e "$target" ] || [ -L "$target" ] || return 0
  # Resolve the parent so a symlink entry is removed itself, never followed.
  resolved="$(cd "$(dirname "$target")" && pwd -P)/$(basename "$target")"
  resolved_root="$(cd "$allowed_root" && pwd -P)"
  case "$resolved_root" in ""|"/"|"$HOME") echo "Refusing unsafe root: $resolved_root" >&2; exit 1 ;; esac
  case "$resolved" in
    "$resolved_root"/?*) ;;
    *) echo "Refusing: $resolved is outside $resolved_root" >&2; exit 1 ;;
  esac
  size_human="$(du -sh -- "$resolved" 2>/dev/null | cut -f1 || echo '?')"
  size_kb="$(du -sk -- "$resolved" 2>/dev/null | cut -f1 || echo 0)"
  total_kb=$((total_kb + ${size_kb:-0}))
  if [ "$dry_run" -eq 1 ]; then
    echo "would remove $size_human  $resolved"
  else
    rm -rf -- "$resolved"
    echo "removed      $size_human  $resolved"
  fi
}

# --- Safety checks (skipped for a dry run, which changes nothing) ---------
if [ "$dry_run" -eq 0 ]; then
  if pgrep -x 'siril|siril-cli' >/dev/null 2>&1; then
    echo "Refusing: a Siril process is running. Close it first." >&2; exit 1
  fi
  if [ -f "$PID_FILE" ]; then
    backend_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$backend_pid" ] && kill -0 "$backend_pid" 2>/dev/null; then
      echo "Refusing: the backend is running (pid $backend_pid). Run build/linux/run_backend.sh stop." >&2; exit 1
    fi
  fi
fi

# The rclone checks apply even to a dry run, so the preview tells the truth.
if [ "$include_rclone" -eq 1 ]; then
  if mountpoint -q "$DRIVE_MOUNT" 2>/dev/null; then
    echo "Refusing --include-rclone: $DRIVE_MOUNT is mounted. Run: systemctl --user stop rclone-drive.service" >&2; exit 1
  fi
  pending_status=0
  RCLONE_CACHE_DIR="$RCLONE_CACHE" bash "$SCRIPT_DIR/rclone_pending_uploads.sh" || pending_status=$?
  if [ "$pending_status" -ne 0 ]; then
    echo "Refusing --include-rclone: files are still waiting to upload (or the check failed)." >&2; exit 1
  fi
fi

# --- Main cache folder ----------------------------------------------------
if [ -d "$CACHE_ROOT" ]; then
  for entry in "$CACHE_ROOT"/* "$CACHE_ROOT"/.[!.]*; do
    [ -e "$entry" ] || [ -L "$entry" ] || continue
    name="$(basename "$entry")"
    case "$name" in
      backup|integrity|locks) continue ;;
      catalogs) [ "$include_catalogs" -eq 1 ] || continue ;;
      siril)
        if [ -d "$entry" ] && [ ! -L "$entry" ]; then
          for sub in "$entry"/* "$entry"/.[!.]*; do
            [ -e "$sub" ] || [ -L "$sub" ] || continue
            if [ "$(basename "$sub")" = "MasterCache" ] && [ "$include_master" -eq 0 ]; then continue; fi
            remove_path "$sub" "$CACHE_ROOT"
          done
          continue
        fi ;;
    esac
    remove_path "$entry" "$CACHE_ROOT"
  done
else
  echo "No cache folder at $CACHE_ROOT."
fi

# Old Siril location from before the consolidation. Its MasterCache gets the
# same protection as the new one.
if [ -d "$LEGACY_SIRIL" ] && [ ! -L "$LEGACY_SIRIL" ]; then
  for sub in "$LEGACY_SIRIL"/* "$LEGACY_SIRIL"/.[!.]*; do
    [ -e "$sub" ] || [ -L "$sub" ] || continue
    if [ "$(basename "$sub")" = "MasterCache" ] && [ "$include_master" -eq 0 ]; then continue; fi
    remove_path "$sub" "$LEGACY_SIRIL"
  done
fi

# rclone cache: remove its contents, keep the folder itself.
if [ "$include_rclone" -eq 1 ] && [ -d "$RCLONE_CACHE" ]; then
  rclone_root="$(cd "$RCLONE_CACHE" && pwd -P)"
  for entry in "$rclone_root"/* "$rclone_root"/.[!.]*; do
    [ -e "$entry" ] || [ -L "$entry" ] || continue
    remove_path "$entry" "$rclone_root"
  done
fi

echo "Total: $((total_kb / 1024)) MiB ($total_kb KiB)$([ "$dry_run" -eq 1 ] && echo ' (dry run, nothing deleted)')"
