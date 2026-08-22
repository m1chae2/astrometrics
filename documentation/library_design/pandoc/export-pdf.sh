#!/usr/bin/env bash
# Exports Astrometrics_Library_Architecture.md and Wayfinding_Library_Architecture.md
# to PDF via Pandoc + xelatex.
#
# Requires: pandoc, xelatex (e.g. `sudo apt-get install texlive-xetex
# texlive-latex-recommended texlive-fonts-recommended texlive-latex-extra`
# — texlive-latex-extra provides the seqsplit package used by
# pdf-header-includes.tex).
#
# Usage:
#   ./export-pdf.sh              # export both docs into this directory's parent
#   ./export-pdf.sh -o DIR       # export both docs into DIR instead

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOC_DIR="$(dirname "$SCRIPT_DIR")"
OUT_DIR="$DOC_DIR"

while getopts "o:h" opt; do
  case "$opt" in
    o) OUT_DIR="$OPTARG" ;;
    h) echo "Usage: $0 [-o output_dir]"; exit 0 ;;
    *) echo "Usage: $0 [-o output_dir]" >&2; exit 1 ;;
  esac
done

for cmd in pandoc xelatex; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "error: $cmd not found on PATH" >&2
    exit 1
  fi
done

mkdir -p "$OUT_DIR"

LUA_FILTER="$SCRIPT_DIR/pdf-export-filters.lua"
HEADER_INCLUDE="$SCRIPT_DIR/pdf-header-includes.tex"

export_doc() {
  local src="$1"
  local name
  name="$(basename "${src%.md}")"
  local out="$OUT_DIR/${name}.pdf"

  echo "Exporting ${name}.md -> ${out}"
  pandoc "$src" \
    -o "$out" \
    --pdf-engine=xelatex \
    --lua-filter="$LUA_FILTER" \
    --include-in-header="$HEADER_INCLUDE" \
    -V geometry:margin=1in \
    --toc
}

export_doc "$DOC_DIR/Astrometrics_Library_Architecture.md"
export_doc "$DOC_DIR/Wayfinding_Library_Architecture.md"

echo "Done."
