# Astrometrics Agent Guidelines

Guidelines and rules for AI agents operating on the `astrometrics` repository.

## 1. Developer Workflow & Quality Assurance
- **Linting**: ALWAYS run `ruff check` (and `ruff format` if needed) after modifying Python code.
- **Testing**: ALWAYS run `pytest` to verify modifications.
- **Documentation**: Follow the conventions in [`documentation-style`](skills/documentation-style/SKILL.md) when editing files under `documentation/`.
- **Environment**: ALWAYS use `.venv/bin/python` (Linux) or `.venv\Scripts\python` (Windows).
- **`# ruff: ignore[...]` / `# noqa` suppressions are debt markers, not permanent exemptions.** They were bulk-added (mainly `ANN001`/`ANN201`/`ANN202`/`ANN204`) to grandfather in the pre-existing codebase when those rules were enabled, not to bless the pattern going forward. If you edit a function or class that carries one of these suppressions, remove the suppression and satisfy the rule (e.g. add the missing type annotation) as part of that edit — don't leave a function you just touched still exempted. Do not add new blanket suppressions for code you write; only pre-existing violations you didn't touch should keep theirs.

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

### `backend/mcp/` & Dynamic Reflection
- All MCP tools reflect directly from the dual-branch `Astrometrics` astrometrics signatures (`imaging_and_processing` and `control_and_planning`) to ensure parity across UI, Python CLI, and AI Agent personas.
- ❌ NEVER retain dead, overridden, or redundant static MCP tool definitions.

## 3. Data & Resource Safety
- **FITS Access**: ALWAYS use `memmap=False` (or `AstrometricsImage`) to prevent file handle / memory leaks.
- **Scientific Type Serialization**: Cast `numpy` / `astropy` types (`int64`, `float64`, `ndarray`) using `.item()` or `.tolist()` before binding to Pydantic models or JSON responses.
- **Target Namespacing**: Keep standard imaging targets (`L`, `R`, `G`, `B`, `Ha`) separate from spectroscopy targets (`SPEC` suffix, e.g. `Vega Spectroscopy`).

## 4. Script Usage (MANDATORY)
ALWAYS prefer executing pre-existing lifecycle scripts under `scripts/` instead of ad-hoc bash commands:
- **Backend Management**: `scripts/linux/run_backend.sh [start|stop|restart|status]`
- **Full Application / Electron UI**: `scripts/linux/run_astrometrics.sh [start|stop|restart|status]`
- **Builds & Packaging**: `scripts/linux/build_astrometrics.sh`
- **Environment Setup**: `scripts/linux/setup_venv.sh`

## 5. MCP Tool Usage Guidelines (MANDATORY)
ALWAYS prefer invoking reflected domain MCP tools (`astrometricslib-core`, `wayfindinglib-core`, `astrometrics-backend`) for image calibration, plate-solving, stacking, target management, visibility calculations, and observatory control before falling back to ad-hoc Python scripts or bash command lines.
