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
- **Prose Documentation (README.md and similar)**: Under `documentation/`, follow the `documentation-style` skill. Everywhere else, follow the `code-documentation-style` skill — including updating the relevant README when you touch the code it describes, even if that wasn't the primary task.
- **Reading level (prose docs and code comments/docstrings alike)**: high school level for the UI tier (`ui/`, `backend/`, `electron/`), first-year college engineering/science level for everything else (`astrometricslib/`, `wayfindinglib/`, and other non-UI-tier code). Plain vocabulary, short sentences, spell out unfamiliar terms on first use. Per-language structural conventions (numpydoc sections, JSDoc tags, naming) are unaffected — this is about vocabulary/complexity, not format.

## 2. Architecture & Unidirectional Layering Rules
- **Domain Library Layer (`astrometricslib/`, `wayfindinglib/`)**: Pure algorithms, spherical trig, and FITS processing. NEVER import `backend` or `mcp_servers`. `astrometricslib` never imports `wayfindinglib`.
- **Application Service Layer (`backend/services/`)**: Stateful orchestration, DB, hardware drivers. NEVER import from `backend.container` or `backend.routers`.
- **Delivery Layer (`backend/routers/`)**: HTTP and WebSocket serialization (`/api/rpc`). Registration only; business logic lives in services. Start-up work lives in `backend/startup.py`.
- **MCP Layer (`mcp_servers/`)**: The AI-agent adapters. They use only the libraries' public API; the backend server calls the running backend over `/api/rpc` and imports only the backend's guard functions. The backend never imports `mcp_servers`.
- **Frontend Layer (`ui/`, `electron/`)**: React/Vite/Electron desktop client. Uses only the backend's public interface, declared in `backend/public_interface.py`: the RPC methods (`callBackend(method, params)` -> `/api/rpc`) and the routes listed there (the `/ws/events` and `/ws/terminal` WebSockets, the `/static` image and FITS files, the `/figure` Matplotlib pages, and `/api/ready`, `/api/session-token`, `/api/pairing-info`). Only `ui/common/services/backendApi.ts` and `ui/common/utils/socketClient.ts` may call `fetch` or open a WebSocket (ESLint enforces it). After changing the declaration, run `build/codegen/generate-types.sh`.

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
The MCP servers live in `mcp_servers/` (the UI server in `ui/mcp/`). Claude Code has access via the MCP server configuration; each server offers only the tools its `tool_manifest.json` allows for the active profile:

### Server Hierarchy
1. **`astrometricslib-core`**: Pure domain library functions (catalog targets, FITS image processing, stacking, astrometry/plate-solving, photometry, spectroscopy, and star catalogs).
2. **`wayfindinglib-core`**: Observatory control, telescope status, tracking, slewing, focusing, guiding, and observation planning.
3. **`astrometrics-backend`**: Live backend status (`app_status`), view switching and notifications (`app_controls`), and documentation (`docs_get`). `backend_call_rpc` and `electron_run_python` are withheld from every profile.
4. **`astrometrics-ui`**: Frontend TypeScript diagnostics, Vitest runners, build checks, and accessibility audits.
5. **`astrometrics-gaps`**: Where an agent reports what its tools cannot do.

### Bug Triage Decision Matrix (Frontend vs. Backend vs. Domain)
- **Diagnose API & UI Stalls**: Call `app_status()`.
  - If it answers quickly and every section is healthy, the backend is fine; the bug is in frontend React state, hooks, or networking.
  - If it errors, hangs, or a section reports an error, the bug is in the backend route or container service.
- **Diagnose Backend Health**: Call `app_status()`. Confirms whether the FastAPI server and container are responding.
- **Diagnose Domain Calculations**: Call `astrometricslib-core` or `wayfindinglib-core` tools directly on disk data.
- **Diagnose Frontend Code & Builds**: Call `ui_diagnose_code`, `ui_run_tests`, or `ui_build_check`.
