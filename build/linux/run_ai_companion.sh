#!/usr/bin/env bash
set -euo pipefail

# run_ai_companion.sh
# Start a read-only Claude session for the Astrometrics app.
#
# The session runs as the "investigator" agent: it can use only the MCP tools
# the app offers to the read-only profile, and it has no shell, no file access
# and no code runner. When its tools cannot do something, it files a report
# with the gap server instead of looking for a workaround. Read the reports
# with:  .venv/bin/python -m mcp_servers.gaps.review list
#
# The MCP config comes from .claude/companion.mcp.json and the agent from
# .claude/agents/investigator.md. Both are written by
# build/mcp/generate_client_configs.py; regenerate them after a tool manifest
# changes.
#
# Usage:
#   ./run_ai_companion.sh            - start an interactive session
#   ./run_ai_companion.sh -p "..."   - run one prompt and print the answer
#   ./run_ai_companion.sh --check    - report whether the generated files are current

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" == "--check" ]]; then
    exec "$ROOT_DIR/.venv/bin/python" build/mcp/generate_client_configs.py --check
fi

exec claude --agent investigator \
    --strict-mcp-config \
    --mcp-config .claude/companion.mcp.json \
    "$@"
