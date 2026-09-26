# Astrometrics — Claude Code Instructions

Guidelines and operational rules for Claude Code operating on the `astrometrics` repository.

## 1. Developer Workflow & Quality Assurance
- **Python Virtualenv**: ALWAYS use `.venv/bin/python` (Linux) or `.venv\Scripts\python` (Windows).
- **Linting**: ALWAYS run `.venv/bin/ruff check` (and `ruff format` if needed) after modifying Python code.
- **Testing**: ALWAYS run `.venv/bin/pytest` on affected test suites after Python changes.
- **Frontend Checks**: Run `npm run type-check` and `npm test` after modifying files under `ui/`.
- **Git Safety**: You MAY run `git commit` when the user asks. NEVER run `git push`, amend, rebase, reset, force-anything, or otherwise modify existing git history. Stage files explicitly and review `git status` first.
- **Docstrings & Clean Code**:
  - Every file must have a description block at the top defining its purpose.
  - Every class, function, method, and test block must have a docstring describing its purpose.
  - When editing functions with `# ruff: ignore[...]` / `# noqa` annotations, satisfy the underlying lint rule (e.g. add type hints) and remove the suppression.

## 2. Architecture & Unidirectional Layering Rules
- **Domain Library Layer (`astrometricslib/`, `wayfindinglib/`)**: Pure algorithms, spherical trig, and FITS processing. NEVER import from `backend.services`, `backend.container`, or `backend.routers`.
- **Application Service Layer (`backend/services/`)**: Stateful orchestration, DB, hardware drivers. NEVER import from `backend.container` or `backend.routers`.
- **Delivery Layer (`backend/routers/`)**: HTTP and WebSocket serialization (`/api/rpc`). Thin delivery layer; delegates business logic to services.
- **Frontend Layer (`ui/`)**: React/Vite/Electron desktop client. Communicates with backend exclusively via `callBackend(method, params)` -> `/api/rpc`.

## 3. Data & Resource Safety
- **FITS Access**: ALWAYS use `memmap=False` (or `AstrometricsImage`) to prevent file handle and memory leaks.
- **Type Serialization**: Always cast NumPy / Astropy types (`int64`, `float64`, `ndarray`) using `.item()` or `.tolist()` before returning in API models.
- **Astropy IERS**: Configured with `iers.conf.auto_download = False` and `iers.conf.auto_max_age = None` to ensure headless/offline safety.

## 4. Script Usage (MANDATORY)
Prefer executing pre-existing lifecycle scripts under `build/linux/` instead of ad-hoc bash commands:
- **Backend Management**: `build/linux/run_backend.sh [start|stop|restart|status]`
- **Full Application / Electron UI**: `build/linux/run_astrometrics.sh [start|stop|restart|status]`
- **Builds & Packaging**: `build/linux/build_astrometrics.sh`
- **Environment Setup**: `build/linux/setup_venv.sh`

## 5. Model Context Protocol (MCP) Tool Integration
Four MCP servers provide direct tooling for AI agents. Claude Code has access via the MCP server configuration:

### Server Hierarchy
1. **`astrometricslib-core`**: Pure domain library functions (catalog targets, FITS image processing, stacking, astrometry/plate-solving, photometry, spectroscopy, and star catalogs).
2. **`wayfindinglib-core`**: Observatory control, telescope status, tracking, slewing, focusing, guiding, and observation planning.
3. **`astrometrics-backend`**: Live backend diagnostics, `/api/rpc` probing, health checks, and supervised Python execution.
4. **`astrometrics-ui`**: Frontend TypeScript diagnostics, Vitest runners, build checks, and accessibility audits.

### Bug Triage Decision Matrix (Frontend vs. Backend vs. Domain)
- **Diagnose API & UI Stalls**: Call `backend_call_rpc(method, params)`.
  - If `backend_call_rpc` succeeds with low latency (<200ms), the backend is healthy; the bug is in frontend React state, hooks, or networking.
  - If `backend_call_rpc` errors, hangs, or returns 500, the bug is in the backend route or container service.
- **Diagnose Backend Health**: Call `backend_health_check()`. Confirms whether the FastAPI server and container are responding.
- **Diagnose Domain Calculations**: Call `astrometricslib-core` or `wayfindinglib-core` tools directly on disk data.
- **Diagnose Frontend Code & Builds**: Call `ui_diagnose_code`, `ui_run_tests`, or `ui_build_check`.
