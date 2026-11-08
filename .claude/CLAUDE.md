# Astrometrics — Claude Code Instructions

Guidelines and operational rules for Claude Code operating on the `astrometrics` repository.

## 1. Developer Workflow & Quality Assurance
- **Python Virtualenv**: ALWAYS use `.venv/bin/python` (Linux) or `.venv\Scripts\python` (Windows).
- **Linting**: ALWAYS run `.venv/bin/ruff check` (and `ruff format` if needed) after modifying Python code.
- **Testing**: ALWAYS run `.venv/bin/pytest` on affected test suites after Python changes.
- **Frontend Checks**: Run `npm run type-check` and `npm test` after modifying files under `ui/`.
- **UI controls**: Build every screen from the components in `ui/common/components/` (`Button`, `Badge`, `StatusChip`, `TextInput`, `NumberInput`, `Checkbox`, `Slider`, `FileInput`, `Tabs`, `TabView`, `PopoverSelect`, `SessionPicker`, `InfoButton`, `SelectableList`). Never restyle them per screen: a change of look is a prop or variant of the component, made in its own stylesheet. A raw `<button>`, `<input>`, `<select>` or `<textarea>`, a hand-built `btn` class and an inline color are ESLint errors, and a stylesheet that targets a shared control's classes is a Stylelint error (`build/stylelint/selector-ownership.mjs`). New colors, sizes and radii are tokens in `ui/common/styles/theme.css`.
- **Errors and notifications**: A telescope, PHD2 or observatory computer that is offline or not reachable is a status, not an error. Show it in a status chip. Do not show an error message, send a desktop notification or log it as `ERROR`. Show an error message only when an action the user asked for fails, or when something that was working breaks during a night (guiding lost, a download failing). Send a desktop notification only for something the user must act on, and never at startup. The app must produce no browser console errors and no failed requests; the TPM harness (`build/tpm/`) fails a run on any of these.
- **Git Safety**: You MAY run `git commit` when the user asks. NEVER run `git push`, amend, rebase, reset, force-anything, or otherwise modify existing git history. Stage files explicitly and review `git status` first.
- **Docstrings & Clean Code**:
  - Every file must have a description block at the top defining its purpose.
  - Every class, function, method, and test block must have a docstring describing its purpose.
  - When editing functions with `# ruff: ignore[...]` / `# noqa` annotations, satisfy the underlying lint rule (e.g. add type hints) and remove the suppression.
- **Docs first, code to confirm**: For any question about how, why or where the software does something, follow the `answer-from-docs` skill. Start from `llms.txt` (the topic map) or `documentation/About_These_Docs.md`, read the paper section, review guide or README it names, then confirm the claim in the code. Cite the document section and the code path. When a document and the code disagree, the code is the current behavior: report the disagreement and offer to fix the document instead of silently answering from one of them. Keep `llms.txt` current when you add, move or rename a document or paper section (`test_documentation_site.py` checks its links).
- **Prose Documentation (README.md and similar)**: Under `documentation/`, follow the `documentation-style` skill. Everywhere else, follow the `code-documentation-style` skill — including updating the relevant README when you touch the code it describes, even if that wasn't the primary task.
- **Where documentation content lives (enforced by tests)**: theory goes in `documentation/library_design/Astrometrics_Library_Architecture.md` (no code names, no tuned values), except the theory of the quality measurements (noise, star shape, rotation, clipping, zero point), which goes in the companion `Astrometrics_Quality_Measurements.md` under the same rules; code names and tuned values go in `Astrometrics_Library_Implementation.md`; evidence (data sets, dates, results, the library calls to repeat a check) goes in the validation register, `documentation/library_design/validation/`. READMEs and the review guides cite a register entry with a status label (Measured, Partly measured, Design estimate) instead of repeating results. Changing a tuned value or its method means updating its register entry. The review guides and `Pipeline_Audit_Report.md` are generated: edit `build/documentation/` and rerun. Check with `.venv/bin/pytest astrometricslib/test/test_documentation_rules.py astrometricslib/test/test_documentation_style.py astrometricslib/test/test_documentation_notation.py astrometricslib/test/test_documentation_site.py wayfindinglib/test/test_documentation_papers.py astrometricslib/test/test_architecture_doc_claims.py astrometricslib/test/test_validation_register.py astrometricslib/pipelines/test/test_pipeline_doc_parity.py -q` and lint prose with `build/documentation/lint_docs.sh` (Vale and markdownlint). `build/linux/build_docs.sh` builds the site and then checks that MathJax can draw every equation on it (`build/documentation/math_check/`). The full rules are in `documentation/Documentation_Conventions.md`; use US spelling.
- **Reading level (prose docs and code comments/docstrings alike)**: high school level for the UI tier (`ui/`, `backend/`, `electron/`), first-year college engineering/science level for everything else (`astrometricslib/`, `wayfindinglib/`, and other non-UI-tier code). Plain vocabulary, short sentences, spell out unfamiliar terms on first use. Per-language structural conventions (numpydoc sections, JSDoc tags, naming) are unaffected — this is about vocabulary/complexity, not format.

## 2. Architecture & Unidirectional Layering Rules
- **Domain Library Layer (`astrometricslib/`, `wayfindinglib/`)**: Pure algorithms, spherical trig, and FITS processing. NEVER import `backend` or `mcp_servers`. `astrometricslib` never imports `wayfindinglib`.
  - **Public names and `_internal`**: each library offers its package root plus a few public modules (`astrometricslib`: `exceptions`, `reports`, `jobs`, `drivers`; `wayfindinglib`: `exceptions`, `reports`, `logs`, and `drivers`, which exports only the PHD2 client). Everything else is private. Only the owning library, and the tests of either library, may import its `_internal`; `wayfindinglib`, the backend and the MCP servers use public names only. Result models are private: read their fields, do not import the classes. The simulation layer lives in the top-level `simulator/` package, which the core (`astrometricslib`, `wayfindinglib`, `backend`, `mcp_servers`) never imports (an import-linter contract checks it). The simulator uses only the libraries' public names and `backend.public_interface`, which also exposes the launcher contract (the source choice, its file and the restart exit code, in `backend/launch_contract.py`). The backend only reports the launch (`[Launch]` section of the settings) and saves the observatory source choice; `simulator.launch` builds the world and runs the backend as its child.
- **Application Service Layer (`backend/services/`)**: Stateful orchestration, DB, hardware drivers. NEVER import from `backend.container` or `backend.routers`.
- **Delivery Layer (`backend/routers/`)**: HTTP and WebSocket serialization (`/api/rpc`). Registration only; business logic lives in services. Start-up work lives in `backend/startup.py`.
- **MCP Layer (`mcp_servers/`)**: The AI-agent adapters. They use only the libraries' public API; the backend server calls the running backend over `/api/rpc` and imports only the backend's guard functions. The backend never imports `mcp_servers`.
- **Frontend Layer (`ui/`, `electron/`)**: React/Vite/Electron desktop client. Uses only the backend's public interface, declared in `backend/public_interface.py`: the RPC methods (`callBackend(method, params)` -> `/api/rpc`) and the routes listed there (the `/ws/events` and `/ws/terminal` WebSockets, the `/static` image and FITS files, the `/figure` Matplotlib pages with their `/ws/figure/{id}` WebSocket and the `/mpl_static` and `/_images` files they load, the `/` status page, and `/api/ready`, `/api/session-token`, `/api/pairing-info`). Only `ui/common/services/backendApi.ts` and `ui/common/utils/socketClient.ts` may call `fetch` or open a WebSocket (ESLint enforces it). Electron's main process reaches the backend only through `electron/backend_client.js` (`callRpc` and `checkReady`, with the address taken from `ASTROMETRICS_PORT`); no other file under `electron/` may call `fetch` or open a WebSocket (ESLint enforces it). A figure window opens only what `electron/figure_target.js` allows: the backend's own `/figure/<number>` page or a PNG in the backend's scratch folder. Files under `ui/common/` must not import from any other top-level `ui/` folder (ESLint enforces it). After changing the declaration, run `build/codegen/generate-types.sh`.

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
- **Agent checks and real-data runs**: `build/agent_tools/check.sh`, `realrun.sh`, `dbq.sh` (see its README). Targeted tests, lint, docs and golden runs share a limited number of heavy-work slots, and real-data scripts reach only a copy of the library. Leave the full suites to continuous integration.

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

## 6. Commits, Issues, Branches and Pull Requests
A hook or a bot rejects each of these when it is wrong, so get them right the first time. `CONTRIBUTING.md` has the full text.
- **Commit messages**: the first line starts with `doc:`, `refactor:`, `new:`, `fix:` or `maintenance:`, a space, then a short description. Put the issue number at the end, for example `fix: reject frames with no pointing data (#150)`. The `commit-msg` hook (`build/hooks/check_commit_message.sh`) rejects anything else, and a rejected commit leaves your files staged.
- **Issue titles**: `type(scope): summary`, where `type` is one of the same five words (`new(ui): ...`, not `enhancement(ui): ...`). The `Issue Labels` workflow flags a wrong title with the `incomplete` label.
- **Issue labels**: every issue needs a subsystem (`astrometricslib`, `wayfindinglib`, `ui`), a component (`api`, `pipelines`, `drivers`, `infrastructure`) and a type (`enhancement`, `problem`). Use the form fields in `.github/ISSUE_TEMPLATE/issue.yml` for the body: `## Problem / Enhancement` and `## Acceptance Criteria`.
- **Analysis and Plan**: before a pull request that closes an issue can merge, write an `## Analysis and Plan` section into the issue (what you found, what the change will do). Close the issue from the pull request description with `Closes #123`.
- **Branch names**: `<issue number>-<short-slug>`, for example `150-ux-clean-up`.
- **Ask first**: ask the user before creating an issue, committing or pushing, unless they have just told you to.
