#!/usr/bin/env bash
# rclone_pending_uploads.sh
#
# Purpose: tell you whether rclone still has files waiting to upload to
# Google Drive. rclone's cache keeps one small JSON file per cached file under
# <cache dir>/vfsMeta/<remote>/... . A file that has not finished uploading has
# "Dirty": true in its JSON. Deleting the cache while any file is dirty would
# lose that file, so clean_caches.sh calls this script first.
#
# Usage:   rclone_pending_uploads.sh
# Env:     RCLONE_CACHE_DIR  rclone cache folder (default: $HOME/.cache/rclone-vfs)
# Output:  a count, then one path per line (relative to the remote).
# Exit:    0 = nothing pending, 3 = something pending, 2 = cache folder problem.
set -euo pipefail

CACHE_DIR="${RCLONE_CACHE_DIR:-$HOME/.cache/rclone-vfs}"
META_DIR="$CACHE_DIR/vfsMeta"

# No metadata folder means rclone has cached nothing, so nothing can be pending.
if [ ! -d "$META_DIR" ]; then
  echo "0 files waiting to upload (no $META_DIR)."
  exit 0
fi

# Find every metadata file marked dirty. grep exits 1 when nothing matches,
# which is fine here, so we guard it with "|| true".
dirty_files="$(grep -rlE '"Dirty":[[:space:]]*true' "$META_DIR" 2>/dev/null | sort || true)"

count=0
if [ -n "$dirty_files" ]; then
  count="$(printf '%s\n' "$dirty_files" | wc -l)"
fi

echo "$count file(s) waiting to upload."
if [ "$count" -gt 0 ]; then
  # Strip "<meta dir>/<remote>/" so the path matches what you see on the remote.
  while IFS= read -r meta_file; do
    relative="${meta_file#"$META_DIR"/}"
    echo "  ${relative#*/}"
  done <<<"$dirty_files"
fi

# Optional extra information: only available if the mount was started with --rc.
if command -v rclone >/dev/null 2>&1; then
  if stats="$(timeout 5 rclone rc vfs/stats 2>/dev/null)"; then
    queued="$(printf '%s' "$stats" | grep -oE '"uploadsQueued":[[:space:]]*[0-9]+' | grep -oE '[0-9]+$' | head -n1 || true)"
    in_progress="$(printf '%s' "$stats" | grep -oE '"uploadsInProgress":[[:space:]]*[0-9]+' | grep -oE '[0-9]+$' | head -n1 || true)"
    echo "rclone rc: uploadsInProgress=${in_progress:-?} uploadsQueued=${queued:-?}"
  fi
fi

if [ "$count" -gt 0 ]; then
  exit 3
fi
exit 0
