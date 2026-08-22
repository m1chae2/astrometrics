---
description: Independent verification for astrometricslib, wayfindinglib, backend, and ui
---

# Verify Workflow

Use this workflow to verify linting and test suites across repo components independently or all at once.

## 1. Verify Domain Libraries (Python)

### Astrometrics Library
```bash
.venv/bin/python -m ruff check astrometricslib/
.venv/bin/python -m pytest astrometricslib/ -q
```

### Wayfinding Library
```bash
.venv/bin/python -m ruff check wayfindinglib/
.venv/bin/python -m pytest wayfindinglib/ -q
```

## 2. Verify Application Backend (Python)

### Backend Services & Routers
```bash
.venv/bin/python -m ruff check backend/
.venv/bin/python -m pytest backend/ -q
```

## 3. Verify UI / Frontend (Node / TypeScript)

### React & Electron UI
```bash
npm run lint --prefix ui
npm run test --prefix ui
```

## 4. Full Repo Verification (All Components)
```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest astrometricslib/ wayfindinglib/ backend/ -q
npm run lint --prefix ui
```
