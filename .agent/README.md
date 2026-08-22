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

#### 1. Astrometrics Core MCP Server (`astrometrics-core`)
- **Location**: `scripts/mcp/astrometrics_core_mcp_server.py`
- **Purpose**: Pure offline library tools (e.g. FITS image analysis, offline plate-solving, offline spectroscopy wave calibration).
- **Invocation**: `.venv/bin/python scripts/mcp/astrometrics_core_mcp_server.py`
- **Primary Tools**: `core_fits_metadata_inspector`, `core_run_offline_image_analysis`, `core_tune_spectroscopy_calibration`, `core_extract_wavelength_profile`.

#### 2. Astrometrics Backend MCP Server (`astrometrics-backend`)
- **Location**: `scripts/mcp/astrometrics_mcp_server.py`
- **Purpose**: Service, state, and active hardware management (e.g., active task queues, backend logs, health diagnostics, dynamic reflected control/planning RPCs).
- **Invocation**: `.venv/bin/python scripts/mcp/astrometrics_mcp_server.py`
- **Primary Tools**: `backend_read_logs`, `backend_diagnose_system`, `backend_restart_backend`, `backend_inspect_device_reservations`, `backend_run_backend_tests`.

#### 3. Astrometrics UI MCP Server (`astrometrics-ui`)
- **Location**: `ui/mcp/src/index.ts` (launcher `scripts/mcp/astrometrics_ui_mcp_server.sh`)
- **Purpose**: Native Node.js/TypeScript toolset for GUI test suites, type checking, lints, and automated ARIA accessibility audits.
- **Invocation**: `scripts/mcp/astrometrics_ui_mcp_server.sh`
- **Primary Tools**: `ui_run_tests`, `ui_diagnose_code`, `ui_audit_accessibility`.

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
