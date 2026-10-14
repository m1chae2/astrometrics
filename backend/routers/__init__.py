"""HTTP and WebSocket routes of the backend (the delivery layer).

Each module registers routes only; the work they hand off lives in
`backend/services/`. This file makes the folder a regular package, so the
import-linter contracts in `pyproject.toml` can see it.
"""
