"""Purpose: Tests for when the backend's services start and stop.

Description: Importing `backend.main_backend` must build nothing. The
services start in the app's lifespan, when the server starts, and stop when
it ends. The lifespan test replaces the real work with mocks.
"""

import os
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend import main_backend, startup


def test_importing_the_backend_module_builds_nothing() -> None:
    """A fresh interpreter that imports the module has an unbuilt container.

    This runs in a subprocess because the test session has already built the
    shared container. It also covers the INDI worker, which re-imports the
    main module when it starts.
    """
    code = (
        "import backend.main_backend\n"
        "from backend.container import container\n"
        "print(container.initialized, container.maintenance_service)"
    )
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "ASTROMETRICS_TESTING": "1"},
        check=True,
    )

    assert result.stdout.strip().splitlines()[-1] == "False None"


def test_the_lifespan_starts_the_services_and_stops_them(monkeypatch: pytest.MonkeyPatch) -> None:
    """Entering the app starts everything in order; leaving stops it."""
    calls: list[str] = []
    fake_container = MagicMock()
    fake_container.init_resources.side_effect = lambda: calls.append("init")
    fake_container.shutdown_resources.side_effect = lambda: calls.append("shutdown")

    async def idle_loop() -> None:
        """Stand in for the telemetry loop."""

    monkeypatch.setattr(main_backend, "container", fake_container)
    monkeypatch.setattr(main_backend, "attach_log_handlers", lambda: calls.append("attach"))
    monkeypatch.setattr(main_backend, "detach_log_handlers", lambda: calls.append("detach"))
    monkeypatch.setattr(startup, "periodic_telemetry_loop", idle_loop)
    monkeypatch.setattr(startup, "warm_start_up_caches", lambda: calls.append("warm"))

    with TestClient(main_backend.app):
        assert calls[:2] == ["init", "attach"]
        assert "shutdown" not in calls

    assert calls[-2:] == ["detach", "shutdown"]
