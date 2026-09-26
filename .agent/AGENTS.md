# Astrometrics Agent Guidelines

Guidelines and rules for AI agents operating on the `astrometrics` repository.

## 1. Developer Workflow & Quality Assurance
- **Linting**: ALWAYS run `ruff check` (and `ruff format` if needed) after modifying Python code.
- **Testing**: ALWAYS run `pytest` to verify modifications.
- **Documentation**: Follow the conventions in [`documentation-style`](skills/documentation-style/SKILL.md) when editing files under `documentation/`.
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
- Exposes direct live backend diagnostics (`backend_call_rpc`, `backend_health_check`) and desktop/pipeline automation controls (`electron_run_python`, `ui_show_notification`, etc.).
- ❌ NEVER recreate synthetic or duplicate domain library reflection wrappers (`backend_targets_*`). Domain math belongs in `astrometricslib-core` and `wayfindinglib-core`.

## 3. Data & Resource Safety
- **FITS Access**: ALWAYS use `memmap=False` (or `AstrometricsImage`) to prevent file handle / memory leaks.
- **Scientific Type Serialization**: Cast `numpy` / `astropy` types (`int64`, `float64`, `ndarray`) using `.item()` or `.tolist()` before binding to Pydantic models or JSON responses.
- **Target Multi-Modal Frame Support**: A single `Target` entity represents the celestial object and holds all light frame types (`L`, `R`, `G`, `B`, `Ha`, `SPEC`). Frame differentiation is handled at processing/stacking via `filter_type` and separate master properties (`stacked_image` and `stacked_spectral_target`), rather than creating artificial target entities.

## 4. Script Usage (MANDATORY)
ALWAYS prefer executing pre-existing lifecycle scripts under `build/linux/` instead of ad-hoc bash commands:
- **Backend Management**: `build/linux/run_backend.sh [start|stop|restart|status]`
- **Full Application / Electron UI**: `build/linux/run_astrometrics.sh [start|stop|restart|status]`
- **Builds & Packaging**: `build/linux/build_astrometrics.sh`
- **Environment Setup**: `build/linux/setup_venv.sh`

## 5. MCP Tool Usage Guidelines (MANDATORY)
Domain queries and operations MUST use the reflected MCP tools first. Do NOT fall back to ad-hoc `curl`, exploratory python snippets, or filesystem inspection unless the MCP call fails or returns an explicit connection error.

### Server Selection Hierarchy
- `astrometricslib-core`: Domain library functions (catalog targets, image processing, stacking, astrometry, photometry, spectroscopy, and star catalogs).
- `wayfindinglib-core`: Observatory control, telescope status, tracking, slewing, focusing, guiding, and observation planning.
- `astrometrics-backend`: Backend session/persistence operations, direct `/api/rpc` probing via `backend_call_rpc`, health checks via `backend_health_check`, and desktop pipeline controls.
- `astrometrics-ui`: UI diagnostic, build, test, and accessibility verification suites.

### Common Invocations Cheat Sheet
- **List targets in catalog**:
  `call_mcp_tool(ServerName="astrometricslib-core", ToolName="target_list", Arguments={})`
- **Get specific target**:
  `call_mcp_tool(ServerName="astrometricslib-core", ToolName="target_get", Arguments={"target_name": "<name>"})`
- **Probe backend RPC method**:
  `call_mcp_tool(ServerName="astrometrics-backend", ToolName="backend_call_rpc", Arguments={"method": "astronomy:visible", "params": {}})`
- **Check backend API health**:
  `call_mcp_tool(ServerName="astrometrics-backend", ToolName="backend_health_check", Arguments={})`
- **Add target frame**:
  `call_mcp_tool(ServerName="astrometricslib-core", ToolName="target_add_frame", Arguments={"target_name": "<name>", "frame_path": "<path>"})`
- **Run astrometry / plate-solving**:
  `call_mcp_tool(ServerName="astrometricslib-core", ToolName="processing_run_astrometry", Arguments={"target_name": "<name>"})`
- **Run stacking**:
  `call_mcp_tool(ServerName="astrometricslib-core", ToolName="processing_run_stacking", Arguments={"target_name": "<name>"})`
- **Run spectroscopy**:
  `call_mcp_tool(ServerName="astrometricslib-core", ToolName="processing_run_spectroscopy", Arguments={"target_name": "<name>"})`
- **Check telescope status**:
  `call_mcp_tool(ServerName="wayfindinglib-core", ToolName="observatory_get_telescope_status", Arguments={})`
- **Slew to target**:
  `call_mcp_tool(ServerName="wayfindinglib-core", ToolName="observatory_slew_to_target", Arguments={"target_name": "<name>"})`
- **Run UI tests**:
  `call_mcp_tool(ServerName="astrometrics-ui", ToolName="ui_run_tests", Arguments={})`

## 6. Supervised Python Scripting & Terminal Environment (MANDATORY)
When executing custom calculations, testing algorithms, exploring datasets, or interacting with `astrometricslib` and `wayfindinglib` public APIs directly:
- **ALWAYS** route execution through the supervised terminal engine using the `electron_run_python` MCP tool (or UI terminal execution).
- **NEVER** spawn ad-hoc bash subprocesses (`python -c "..."` or shell scripts) to interact with the domain libraries.
- The supervised environment provides:
  - **Live Shared State**: Direct access to `astrometrics` and `wayfinder` instances connected to active telescope hardware and catalogs.
  - **Resource & Power Guardian**: Enforces thread quotas (75% cores) and automatically freezes background compute (`SIGSTOP`) on battery/screen lock.
  - **MATLAB-style Workspace**: Inspect active variables and array dimensions using `terminal_get_workspace`.
  - **Introspection**: Query signatures and parameter docs using `terminal_inspect_api` or `inspect_api(...)`.
  - **Visual Feedback**: Automatically intercepts Matplotlib figures into saved PNG artifacts returned in the result envelope.
