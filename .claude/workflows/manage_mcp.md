---
description: Manage the Astrometrics Split MCP Servers lifecycle (Restart/Start)
---

# Manage MCP Servers

Use this workflow to restart or start the split MCP servers (Core, Backend, and UI). This is necessary when any server code or tool implementation has been modified.

## 1. Stop Existing Processes

### Ubuntu (Bash)
// turbo
```bash
# Stop all Python and Node MCP servers
pkill -f astrometrics_core_mcp_server.py
pkill -f astrometrics_mcp_server.py
pkill -f astrometrics_ui_mcp_server.sh
pkill -f "node dist/index.js"
```

## 2. Start Processes

### Ubuntu (Bash)
// turbo
```bash
cd "$(git rev-parse --show-toplevel)"

# 1. Start Core MCP in background
.venv/bin/python -m astrometricslib.mcp &

# 2. Start Backend MCP in background
.venv/bin/python -m backend.mcp &
```

## 3. Verify

### Ubuntu (Bash)
// turbo
```bash
sleep 2
# Verify all processes are active
ps aux | grep -E "astrometrics_core_mcp_server|astrometrics_mcp_server|astrometrics_ui" | grep -v grep
```
