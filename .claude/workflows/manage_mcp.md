---
description: Stop and start the Astrometrics MCP servers
---

# Manage MCP Servers

Use this workflow to stop and start the Python MCP servers in `mcp_servers/` and the UI server in `ui/mcp/`. Restart them after you change a server's code or a library method it offers. A client such as Claude Code normally starts the servers itself from `.mcp.json`, so this is only needed for servers started by hand.

## 1. Stop Existing Processes

### Ubuntu (Bash)
// turbo
```bash
# Stop all Python and Node MCP servers
pkill -f "mcp_servers\.(astrometrics_core|wayfinding_core|backend|gaps)"
pkill -f "ui/mcp/dist/index.js"
```

## 2. Start Processes

### Ubuntu (Bash)
// turbo
```bash
cd "$(git rev-parse --show-toplevel)"

# 1. Start Core MCP in background
.venv/bin/python -m mcp_servers.astrometrics_core &

# 2. Start the other Python servers in background
.venv/bin/python -m mcp_servers.wayfinding_core &
.venv/bin/python -m mcp_servers.backend &
.venv/bin/python -m mcp_servers.gaps &
```

## 3. Verify

### Ubuntu (Bash)
// turbo
```bash
sleep 2
# Verify all processes are active
ps aux | grep -E "mcp_servers\.|ui/mcp/dist/index.js" | grep -v grep
```
