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
    monkeypatch.setattr(startup, "warm_night_reviews", lambda: calls.append("warm_nights"))
    # The lifespan also touches the shared settings, the job database and the
    # storage mount. Those depend on what earlier tests left behind (settings
    # paths, locked or removed folders), so they are stood in for here.
    monkeypatch.setattr(main_backend, "close_interrupted_jobs", lambda configuration: calls.append("jobs"))
    monkeypatch.setattr(main_backend, "require_mounted_storage", lambda *arguments: calls.append("storage"))
    monkeypatch.setattr(main_backend.app_configuration, "watch_for_changes", lambda: calls.append("watch"))

    with TestClient(main_backend.app):
        assert calls[:2] == ["init", "attach"]
        assert "shutdown" not in calls

    assert calls[-2:] == ["detach", "shutdown"]


def test_night_review_warm_up_builds_the_newest_nights_and_survives_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The newest nights are reviewed, newest first, despite one failure."""
    asked: list[str] = []

    def query(kind: str, session_id: str | None = None, **_: object) -> MagicMock:
        """Answer the nights list, or record a night review (one night fails).

        Returns
        -------
        answer : `MagicMock`
            Stands in for a `ReviewQueryResult`.

        Raises
        ------
        RuntimeError
            For the night ``"2026-10-02"``.
        """
        if kind == "nights":
            nights = [MagicMock(night_id=night) for night in ("2026-10-01", "2026-10-02", "2026-10-03")]
            return MagicMock(night_list=nights)
        asked.append(session_id or "")
        if session_id == "2026-10-02":
            raise RuntimeError("no data")
        return MagicMock()

    fake_container = MagicMock()
    fake_container.wayfinder.review.query.side_effect = query
    monkeypatch.setattr(startup, "container", fake_container)

    startup.warm_night_reviews(night_count=3)

    assert asked == ["2026-10-03", "2026-10-02", "2026-10-01"]
