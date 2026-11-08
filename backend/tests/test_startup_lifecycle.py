"""Purpose: Tests for when the backend's services start and stop.

Description: Importing `backend.main_backend` must build nothing. The
services start in the app's lifespan, when the server starts, and stop when
it ends. The lifespan test replaces the real work with mocks.
"""

import logging
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
    monkeypatch.setattr(startup, "warm_depth_goals", lambda: calls.append("warm_depth"))
    # The lifespan also touches the shared settings, the job database and the
    # storage mount. Those depend on what earlier tests left behind (settings
    # paths, locked or removed folders), so they are stood in for here.
    monkeypatch.setattr(main_backend, "close_interrupted_jobs", lambda configuration: calls.append("jobs"))
    monkeypatch.setattr(main_backend, "require_mounted_storage", lambda *arguments: calls.append("storage"))
    monkeypatch.setattr(main_backend.app_configuration, "watch_for_changes", lambda: calls.append("watch"))

    with TestClient(main_backend.app):
        assert calls[:2] == ["init", "attach"]
        assert "shutdown" not in calls

    # The warm-up steps run on worker threads and may finish after shutdown.
    # The depth goals must come before the night reviews.
    warm_calls = [call for call in calls if call.startswith("warm")]
    assert warm_calls in (["warm"], ["warm", "warm_depth"], ["warm", "warm_depth", "warm_nights"])
    stopping = [call for call in calls if call in ("detach", "shutdown")]
    assert stopping == ["detach", "shutdown"]


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


def _depth_goal_container(
    pointed_at: str | None, imaged: dict[str, float | None], with_picture: set[str]
) -> MagicMock:
    """Make a stand-in container for the depth goal warm-up tests.

    Parameters
    ----------
    pointed_at : `str` or `None`
        The target name in the telescope state.
    imaged : `dict` [`str`, `float` or `None`]
        Each target's newest light-frame time.
    with_picture : `set` [`str`]
        The targets that have a stacked picture.

    Returns
    -------
    fake_container : `MagicMock`
        The stand-in container.
    """
    fake_container = MagicMock()
    fake_container.astrometrics_service.get_state.return_value = {"telescope": {"targetName": pointed_at}}
    fake_container.target_service.get_camera_index.return_value = {
        "targets": {name: {"lastFrameTime": time} for name, time in imaged.items()}
    }
    fake_container.target_service.get_targets.return_value = [
        MagicMock(
            id=name,
            stacking=MagicMock(processed_image="a.jpg" if name in with_picture else "", stacked_image=""),
        )
        for name in imaged
    ]
    return fake_container


def test_depth_goal_warm_up_builds_the_pointed_target_then_the_newest_three_at_most(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The telescope target comes first, then the newest, at most three."""
    fake_container = _depth_goal_container(
        "M 57",
        {"M 13": 10.0, "M 57": 30.0, "M 81": 20.0, "NGC 7331": 5.0, "M 27": 40.0, "Dark": 50.0},
        {"M 13", "M 57", "M 81", "NGC 7331", "M 27"},
    )
    monkeypatch.setattr(startup, "container", fake_container)

    startup.warm_depth_goals()

    asked = [call.args[0] for call in fake_container.advisory_service.get_depth_goal.call_args_list]
    assert asked == ["M 57", "M 27", "M 81"]


def test_depth_goal_warm_up_without_a_pointed_target_uses_the_newest_imaged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no pointed target, the three newest imaged are built."""
    fake_container = _depth_goal_container(
        None, {"A": 1.0, "B": 3.0, "C": 2.0, "D": None}, {"A", "B", "C", "D"}
    )
    monkeypatch.setattr(startup, "container", fake_container)

    startup.warm_depth_goals()

    asked = [call.args[0] for call in fake_container.advisory_service.get_depth_goal.call_args_list]
    assert asked == ["B", "C", "A"]


def test_depth_goal_warm_up_swallows_failures_quietly(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failing target is skipped and nothing logs a warning."""
    fake_container = _depth_goal_container(None, {"A": 3.0, "B": 2.0, "C": 1.0}, {"A", "B", "C"})
    fake_container.advisory_service.get_depth_goal.side_effect = [
        RuntimeError("no data"),
        MagicMock(),
        MagicMock(),
    ]
    monkeypatch.setattr(startup, "container", fake_container)

    with caplog.at_level("DEBUG"):
        startup.warm_depth_goals()

    assert fake_container.advisory_service.get_depth_goal.call_count == 3
    assert not [record for record in caplog.records if record.levelname in ("ERROR", "CRITICAL", "WARNING")]


def test_depth_goal_warm_up_survives_an_empty_library(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An empty or unreadable library builds nothing, quietly."""
    fake_container = _depth_goal_container(None, {}, set())
    monkeypatch.setattr(startup, "container", fake_container)
    startup.warm_depth_goals()
    fake_container.advisory_service.get_depth_goal.assert_not_called()

    fake_container.target_service.get_camera_index.side_effect = RuntimeError("library closed")
    with caplog.at_level("DEBUG"):
        startup.warm_depth_goals()
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
