# Astrometrics Agent Guidelines

Guidelines and rules for AI agents operating on the `astrometrics` repository.

## 1. Developer Workflow & Quality Assurance
- **Linting**: ALWAYS run `ruff check` (and `ruff format` if needed) after modifying Python code.
- **Testing**: ALWAYS run `pytest` to verify modifications.
- **Documentation**: Follow the conventions in [`documentation-style`](skills/documentation-style/SKILL.md) when editing files under `documentation/`. Everywhere else, follow [`code-documentation-style`](skills/code-documentation-style/SKILL.md) for README.md and similar prose docs.
- **Reading level (prose docs and code comments/docstrings alike)**: high school level for anything in the UI tier (`ui/`, `backend/`, `electron/`), first-year college engineering/science level for everything else. Per-language structural conventions (numpydoc sections, JSDoc tags, naming) are unaffected — this is about vocabulary/complexity, not format.
- **Environment**: ALWAYS use `.venv/bin/python` (Linux) or `.venv\Scripts\python` (Windows).
- **`# ruff: ignore[...]` / `# noqa` suppressions are debt markers, not permanent exemptions.** They were bulk-added (mainly `ANN001`/`ANN201`/`ANN202`/`ANN204`) to grandfather in the pre-existing codebase when those rules were enabled, not to bless the pattern going forward. If you edit a function or class that carries one of these suppressions, remove the suppression and satisfy the rule (e.g. add the missing type annotation) as part of that edit — don't leave a function you just touched still exempted. Do not add new blanket suppressions for code you write; only pre-existing violations you didn't touch should keep theirs.
- **Deprecation Markers**: Any deprecated code, parameter, fallback, or legacy compatibility shim MUST be marked with `# TODO: DEPRECATED - <description of deprecation and what to migrate to>` so that a single repository-wide search (`grep -rn "TODO: DEPRECATED"`) identifies all deprecated code and technical debt.

## 2. Architecture & Layering Rules
Maintain clean unidirectional dependency boundaries across layers:

### `astrometrics/` (Domain Library Layer)
- Pure math, algorithms, coordinate parsing, data transforms, and FITS processing.
- MAY import: stdlib, `astropy`, `numpy`, `scipy`, `Pillow`.
- ❌ NEVER import from `backend.services`, `backend.container`, `backend.routers`, or `backend.backend_schemas`.

### `backend/services/` (Application Service Layer)
- Stateful resource management, persistence, pipeline orchestration, job tracking.
- Receives dependencies via constructor injection.
- ❌ NEVER import from `backend.container` or `backend.routers`.

### `backend/routers/` (Delivery Layer)
- HTTP/WebSocket endpoints and serialization.
- Thin delivery layer delegating business logic to services.
- ❌ NEVER contain business logic or import directly from `astrometrics/`.

### `backend/mcp/` & Running API Layer
- Hosts the backend MCP server, the capability gap server, and the tool manifests that limit what an AI client may use (see section 5).
- ❌ NEVER recreate synthetic or duplicate domain library reflection wrappers (`backend_targets_*`). Domain math belongs in `astrometricslib-core` and `wayfindinglib-core`.

## 3. Data & Resource Safety
- **FITS Access**: ALWAYS use `memmap=False` (or `AstrometricsImage`) to prevent file handle / memory leaks.
- **Scientific Type Serialization**: Cast `numpy` / `astropy` types (`int64`, `float64`, `ndarray`) using `.item()` or `.tolist()` before binding to Pydantic models or JSON responses.
- **Target Multi-Modal Frame Support**: A single `Target` entity represents the celestial object and holds all light frame types (`L`, `R`, `G`, `B`, `Ha`, `SPEC`). Frame differentiation is handled at processing/stacking via `filter_type` and separate master properties (`stacking.stacked_image` and `spectral_stacking.stacked_image`), rather than creating artificial target entities.

## 4. Script Usage (MANDATORY)
ALWAYS prefer executing pre-existing lifecycle scripts under `build/linux/` instead of ad-hoc bash commands:
- **Backend Management**: `build/linux/run_backend.sh [start|stop|restart|status]`
- **Full Application / Electron UI**: `build/linux/run_astrometrics.sh [start|stop|restart|status]`
- **Builds & Packaging**: `build/linux/build_astrometrics.sh`
- **Environment Setup**: `build/linux/setup_venv.sh`

## 5. MCP Tool Usage Guidelines (MANDATORY)
The MCP servers give an AI tools that look things up, calculate and measure. An AI never changes configuration, commands a device (mount, cameras, focuser, filter wheel, enclosure), or runs code through MCP. It has three writes: `processing_stack` stacks a target's frames as the app's Stack button does (choose `kind` imaging or spectral, a filter, a file or time range; `plan_only` lists the frames first; it keeps one previous stack and sets bad frames aside without deleting them; when only a preview setting changed, use `processing_remake_preview` to remake the picture without restacking), `observatory_remote_sync_frames` brings a target's new frames, and `observatory_remote_sync_logs` brings the guide and Ekos logs, from the telescope computer into the library. Those tools add files and records and never delete. Use it with `dry_run` true first, then false, and follow the job with `jobs_query`. Measure the frames with `diagnostics_frame_quality`, look at one with `visualization_render_fits`, and check whether guiding spoiled a frame with `observatory_history_frame_guiding`. Plan a night with `planning_get_visibility_over_time`. Read the live telescope, guiding and INDI devices with `app_status`, list or describe targets with `target_query`, read a star's analysis with `star_query` and `detail` set to `analysis`, and judge raw spectrum frames with `diagnostics_spectral_frame_check`. A slow tool returns a job id: follow it with `jobs_query`. Each server starts with a profile (the `ASTROMETRICS_MCP_PROFILE` environment variable). The default, `investigator`, offers tools that look things up, calculate or measure, plus the frame ingest tool and `processing_stack`. The `developer` profile adds the tools that run the project's own tests and builds. A tool that is not in a server's `tool_manifest.json` is not offered.

### When your tools cannot do something
Stop. Do not look for a workaround: do not chain tools to imitate a missing one, do not use `curl`, `sqlite3` or `python -c` against the live backend or databases, and do not write a script that touches the live databases or the telescope. Call `report_capability_gap` on the `astrometrics-gaps` server (call `list_capability_gaps` first so you do not report the same gap twice). Say what you tried and what tool would help. Then tell the person you cannot do it with the current tools. The person decides what to build, and reviews the reports with `.venv/bin/python -m backend.mcp.gaps.review list`.

### Two ways to run
- **Companion**: `build/linux/run_ai_companion.sh` starts Claude as the `investigator` agent. It has only MCP tools, no shell and no file access. For Gemini, `.gemini/settings.json` lists the same tools in `includeTools`.
- **Developer**: a normal session that edits the repo. It uses `.mcp.json`. It still cannot write data or command devices through MCP. Test code against a scratch configuration (`ASTROMETRICS_CONFIG_PATH`), never the live library.

Run `.venv/bin/python build/mcp/generate_client_configs.py` after a tool manifest changes. It writes `.mcp.json`, `.claude/companion.mcp.json`, `.claude/agents/investigator.md` and `.gemini/settings.json`. Do not edit those files by hand. `.venv/bin/python -m backend.mcp.tool_inventory --write-runtime-manifests` writes the manifests from the reviewed decisions.

### Server Selection Hierarchy
- `astrometricslib-core`: Read-only lookups and calculations on targets, stars, calibration, stacks and frames.
- `wayfindinglib-core`: Read-only observatory status, equipment state, past-night analysis, remote listings and observation planning. No device commands.
- `astrometrics-backend`: Backend health, documentation, and changing the app's view or showing a notification.
- `astrometrics-gaps`: Reporting what the tools cannot do.
- `astrometrics-ui` (developer profile only): UI tests, type check, build and accessibility checks.

### Common Invocations Cheat Sheet
Tool names change as the proposed merged tools are built. The manifests are the source of truth.
- **List targets**: `call_mcp_tool(ServerName="astrometricslib-core", ToolName="target_list", Arguments={})`
- **Get a target**: `call_mcp_tool(ServerName="astrometricslib-core", ToolName="target_get", Arguments={"target_id": "<id>"})`
- **Check backend health**: `call_mcp_tool(ServerName="astrometrics-backend", ToolName="backend_health_check", Arguments={})`
- **Report a gap**: `call_mcp_tool(ServerName="astrometrics-gaps", ToolName="report_capability_gap", Arguments={...})`
- **Run UI tests (developer profile)**: `call_mcp_tool(ServerName="astrometrics-ui", ToolName="ui_run_tests", Arguments={})`

## 6. Scripts and Analysis
- **NEVER** spawn ad-hoc scripts (`python -c "..."`, shell scripts, SQL) against the live databases, the live backend or the telescope. `electron_run_python` and `backend_call_rpc` are not offered to an AI.
- When an analysis needs something the tools do not offer, file a gap report and discuss the tool with the person. Do not write a one-off script to get the number.
- Tests and scripts that you write while changing code must use a scratch library and configuration, as the test suite does.
