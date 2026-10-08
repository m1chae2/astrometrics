# Astrometrics AI Agent Configuration

This directory contains all AI agent workflows, skills, and configuration documentation.

## Critical Environment & Tool Configuration

### Python Environment
**All Python scripts must be executed using the project's virtual environment:**
```bash
.venv/bin/python <script_name>.py
```

This includes:
- Backend scripts
- Testing scripts
- Utility scripts (e.g., `scripts/audit_requirements.py`)
- MCP servers

### MCP Servers

The project runs five MCP servers. For an AI client they look things up, calculate and measure, and one tool (`observatory_remote_sync_frames`) brings frames from the telescope into the library. None commands a device. The servers are started from `.mcp.json` (a developer session) or `.claude/companion.mcp.json` (a read-only companion session).

| Server | Start command | What it offers an AI |
|---|---|---|
| `astrometricslib-core` | `.venv/bin/python -m mcp_servers.astrometrics_core` | Lookups and calculations on targets, stars, calibration, stacks and frames |
| `wayfindinglib-core` | `.venv/bin/python -m mcp_servers.wayfinding_core` | Observatory status, equipment state, past-night analysis, planning. No device commands |
| `astrometrics-backend` | `.venv/bin/python -m mcp_servers.backend` | Backend health, documentation, change the view, show a notification |
| `astrometrics-gaps` | `.venv/bin/python -m mcp_servers.gaps` | Report what the tools cannot do |
| `astrometrics-ui` | `node ui/mcp/dist/index.js` | Developer profile only: UI tests, type check, build, accessibility |

Each server reads a `tool_manifest.json` and offers only the tools it allows for the chosen profile (`ASTROMETRICS_MCP_PROFILE`, default `investigator`). See `mcp_servers/README.md`.

- `.venv/bin/python build/mcp/generate_client_configs.py` writes `.mcp.json`, `.claude/companion.mcp.json`, `.claude/agents/investigator.md` and `.gemini/settings.json` from one list of servers, and `ui/mcp/src/profileRules.ts` from `mcp_servers/common/profile.py`. Run it after a manifest or the profile rules change. Do not edit those files by hand.
- The Python servers need the optional `mcp` extra (`pip install -e ".[mcp]"`), which `build/linux/setup_venv.sh` installs.
- `build/linux/run_ai_companion.sh` starts Claude as the `investigator` agent.
- `.venv/bin/python -m mcp_servers.gaps.review list` shows the gaps an AI reported.

#### Chrome DevTools MCP Server
- **Location**: `mcp_servers.json`
- **Purpose**: Provides tools for interacting with the Electron frontend via Chrome DevTools Protocol
- **Configuration**:
  ```json
  {
    "mcpServers": {
      "chrome-devtools": {
        "command": "npx",
        "args": [
          "chrome-devtools-mcp@latest",
          "--browser-url=http://127.0.0.1:9222",
          "-y"
        ]
      }
    }
  }
  ```
- **Use Cases**:
  - Debugging running Electron app
  - Frontend UI interaction testing
  - Console log inspection

## Directory Structure

```
.agent/
├── README.md                    # This file
├── rules/                       # AI agent passive constraints
│   ├── standards-enforcement.md # Architectural standards enforcement
│   ├── mcp-troubleshooting.md   # MCP-first troubleshooting protocol
│   ├── backend-layering.md      # Strict backend architecture layering
│   └── sandbox-constraints.md   # Google Antigravity sandbox limitations
├── skills/                      # AI agent role-based skills
│   ├── architect/              # Design fit, best practices, architectural patterns
│   ├── devops/                 # Final release gate, commit preparation
│   ├── implementer/            # Surgical code changes and feature implementation
│   ├── mcp_tools/              # MCP server usage guidance
│   ├── planning/               # Strategy, research, implementation plans
│   ├── roadmap/                # Strategic planning, requirements evolution
│   └── verification/           # Testing, quality audit, requirement traceability
└── workflows/                   # User-defined workflows
    ├── manage_mcp.md           # MCP server lifecycle management
    └── troubleshoot.md         # System troubleshooting protocol
```

## Workflow Usage

When a workflow is referenced (e.g., `/manage_mcp`), always use `view_file` to read the complete workflow before executing its steps.

## Skills & Workflows

- **Skills** (`.agent/skills/`): Agent-triggered capabilities, loaded on-demand. Examples: documentation style, planning, verification.
- **Workflows** (`.agent/workflows/`): User-triggered sequences, manually invoked. Examples: `/manage_mcp`, `/troubleshoot`.

## Standards & Best Practices

All code contributions must adhere to:
- **React/TypeScript**: Google TypeScript Style Guide, BEM Methodology
- **Python**: PEP 8, Pydantic (camelCase aliases)
- **C++**: PascalCase classes, smart pointers, thread safety
- **Electron**: IPC isolation, context bridge security
- **Documentation**: Comprehensive docstrings and inline comments
- **Testing**: Unit tests required for all new functionality

See individual skill files for detailed role-specific guidance.
