"""Purpose: Tests for when the backend's services start and stop.

Description: Importing `backend.main_backend` must build nothing. The
services start in the app's lifespan, when the server starts, and stop when
it ends. The lifespan test replaces the real work with mocks.
"""

import asyncio
import logging
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import threading
import time
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
    monkeypatch.setattr(startup, "warm_depth_goals", lambda: calls.append("warm_depth") or ["M 31"])
    monkeypatch.setattr(
        startup, "warm_target_reviews", lambda target_id: calls.append(f"warm_reviews {target_id}")
    )
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
    # The depth goals, then the target reviews, must come before the night
    # reviews.
    warm_calls = [call for call in calls if call.startswith("warm")]
    order = ["warm", "warm_depth", "warm_reviews M 31", "warm_nights"]
    # Leaving the app cancels the warm-up, so it may have run only part way.
    assert warm_calls in [order[:count] for count in range(len(order) + 1)]
    stopping = [call for call in calls if call in ("detach", "shutdown")]
    assert stopping == ["detach", "shutdown"]


def test_night_review_warm_up_builds_the_newest_nights_and_survives_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The newest nights are reviewed, newest first, despite one failure."""
    asked: list[str] = []

    def get(night: str, required: bool = False) -> MagicMock:
        """Record a night review (one night fails).

        Returns
        -------
        answer : `MagicMock`
            Stands in for a `NightQuality`.

        Raises
        ------
        RuntimeError
            For the night ``"2026-10-02"``.
        """
        asked.append(night)
        if night == "2026-10-02":
            raise RuntimeError("no data")
        return MagicMock()

    fake_container = MagicMock()
    nights = [MagicMock(night_id=night) for night in ("2026-10-01", "2026-10-02", "2026-10-03")]
    fake_container.wayfinder.review.nights.query.return_value = nights
    fake_container.wayfinder.review.nights.get.side_effect = get
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
    targets = [
        MagicMock(
            id=name,
            stacking=MagicMock(processed_image="a.jpg" if name in with_picture else "", stacked_image=""),
        )
        for name in imaged
    ]

    def get_targets(target_id: str | None = None, detail: str = "full") -> object:
        """Answer like the target service.

        Returns
        -------
        found : `object`
            One target (or `None`) for an id, else all targets.
        """
        if target_id is None:
            return targets
        return next((target for target in targets if target.id == target_id), None)

    fake_container.target_service.get_targets.side_effect = get_targets
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


def test_depth_goal_warm_up_skips_a_pointed_name_that_is_not_a_library_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A telescope target name with no library target is skipped, not built."""
    fake_container = _depth_goal_container("Unknown field", {"A": 1.0}, {"A"})
    monkeypatch.setattr(startup, "container", fake_container)

    startup.warm_depth_goals()

    asked = [call.args[0] for call in fake_container.advisory_service.get_depth_goal.call_args_list]
    assert asked == ["A"]


def test_depth_goal_warm_up_returns_the_targets_it_chose(monkeypatch: pytest.MonkeyPatch) -> None:
    """The chosen targets come back, so the reviews warm the same ones."""
    fake_container = _depth_goal_container(None, {"M 31": 3.0, "M 81": 2.0}, {"M 31", "M 81"})
    monkeypatch.setattr(startup, "container", fake_container)

    assert startup.warm_depth_goals() == ["M 31", "M 81"]

    fake_container.target_service.get_camera_index.side_effect = RuntimeError("library closed")
    assert startup.warm_depth_goals() == []


def test_target_review_warm_up_makes_the_image_processing_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """The target's star shapes, frame states and grouped frames are read."""
    fake_container = MagicMock()
    monkeypatch.setattr(startup, "container", fake_container)

    startup.warm_target_reviews("M 31")

    fake_container.advisory_service.get_star_shape_review.assert_called_once_with("M 31")
    fake_container.target_service.get_frame_statuses.assert_called_once_with("M 31")
    fake_container.target_service.get_frame_stats_grouped.assert_called_once_with("M 31")


def test_target_review_warm_up_swallows_failures_quietly(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failing call is skipped, the others run, and nothing warns."""
    fake_container = MagicMock()
    fake_container.advisory_service.get_star_shape_review.side_effect = RuntimeError("no frames")
    fake_container.target_service.get_frame_statuses.side_effect = RuntimeError("drive not mounted")
    monkeypatch.setattr(startup, "container", fake_container)

    with caplog.at_level("DEBUG"):
        startup.warm_target_reviews("M 31")

    fake_container.target_service.get_frame_stats_grouped.assert_called_once_with("M 31")
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_start_up_warm_up_loads_the_quality_metric_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """The start-up warm-up builds the quality metric list (public API)."""
    fake_container = MagicMock()
    monkeypatch.setattr(startup, "container", fake_container)

    startup.warm_start_up_caches()

    fake_container.astrometrics.quality.records.get_metrics.assert_called_once_with()
    assert startup.sky_catalog_warmup_finished.is_set()


def test_the_cache_warm_ups_log_a_failure_as_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed background warm-up is a warning, not an error."""
    fake_container = MagicMock()
    fake_container.wayfinder.planning.catalogs.query_sources.side_effect = RuntimeError("no catalog")
    fake_container.wayfinder.review.nights.query.side_effect = RuntimeError("no nights")
    monkeypatch.setattr(startup, "container", fake_container)
    fake_container.stellar_service.warm_catalog_summary_cache.side_effect = RuntimeError("cache")

    with caplog.at_level("DEBUG"):
        startup.warm_start_up_caches()
        startup.warm_night_reviews()

    levels = {record.levelname for record in caplog.records if "warm-up failed" in record.getMessage()}
    assert levels == {"WARNING"}
    assert startup.sky_catalog_warmup_finished.is_set()


def test_the_lifespan_cancels_the_warm_up_task_and_runs_it_in_the_heavy_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Warm-up steps go through `run_heavy`, and shutdown cancels the task."""
    lane_calls: list[str] = []

    async def fake_run_heavy(function: object, *args: object, **kwargs: object) -> object:
        """Note the step and, for the last one, wait until cancelled.

        Returns
        -------
        answer : `object`
            Two chosen targets for the depth goal step, else `None`.
        """
        lane_calls.append(" ".join([function.__name__, *map(str, args)]))
        if function.__name__ == "warm_night_reviews":
            await asyncio.sleep(60)
        if function.__name__ == "warm_depth_goals":
            return ["M 31", "M 81"]
        return None

    monkeypatch.setattr(main_backend, "run_heavy", fake_run_heavy)
    fake_container = MagicMock()

    async def idle_loop() -> None:
        """Stand in for the telemetry loop."""

    monkeypatch.setattr(main_backend, "container", fake_container)
    monkeypatch.setattr(main_backend, "attach_log_handlers", lambda: None)
    monkeypatch.setattr(main_backend, "detach_log_handlers", lambda: None)
    monkeypatch.setattr(startup, "periodic_telemetry_loop", idle_loop)
    monkeypatch.setattr(main_backend, "close_interrupted_jobs", lambda configuration: None)
    monkeypatch.setattr(main_backend, "require_mounted_storage", lambda *arguments: None)
    monkeypatch.setattr(main_backend.app_configuration, "watch_for_changes", lambda: None)

    with TestClient(main_backend.app):
        time.sleep(0.2)
        task = main_backend.app.state.sky_warmup_task
        assert not task.done()

    assert task.cancelled()
    # Each target's reviews are their own heavy-lane step, after the depth
    # goals and before the night reviews.
    assert lane_calls == [
        "warm_start_up_caches",
        "warm_depth_goals",
        "warm_target_reviews M 31",
        "warm_target_reviews M 81",
        "warm_night_reviews",
    ]


def test_the_lifespan_starts_the_review_re_reads_and_cancels_them_at_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The re-read loop runs while the app runs and is cancelled after."""
    started = threading.Event()

    async def endless_re_reads() -> None:
        """Stand in for the re-read loop: note the start, then wait."""
        started.set()
        await asyncio.sleep(60)

    async def idle_loop() -> None:
        """Stand in for the telemetry loop and the warm-up."""

    monkeypatch.setattr(main_backend, "container", MagicMock())
    monkeypatch.setattr(main_backend, "attach_log_handlers", lambda: None)
    monkeypatch.setattr(main_backend, "detach_log_handlers", lambda: None)
    monkeypatch.setattr(main_backend, "_warm_up_in_order", idle_loop)
    monkeypatch.setattr(startup, "periodic_telemetry_loop", idle_loop)
    monkeypatch.setattr(startup, "keep_target_reviews_warm", endless_re_reads)
    monkeypatch.setattr(main_backend, "close_interrupted_jobs", lambda configuration: None)
    monkeypatch.setattr(main_backend, "require_mounted_storage", lambda *arguments: None)
    monkeypatch.setattr(main_backend.app_configuration, "watch_for_changes", lambda: None)

    with TestClient(main_backend.app):
        time.sleep(0.2)
        task = main_backend.app.state.review_rewarm_task
        assert started.is_set()
        assert not task.done()

    assert task.cancelled()


def test_the_review_re_read_loop_waits_for_the_start_up_warm_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """The loop gets the quiet target warm-up and the start-up flag."""
    fake_container = MagicMock()
    calls: list[tuple[object, object]] = []

    async def fake_run(warm_target: object, ready: object) -> None:
        """Record what the loop was given."""
        calls.append((warm_target, ready))
        await asyncio.sleep(0)

    fake_container.target_review_rewarmer.run = fake_run
    monkeypatch.setattr(startup, "container", fake_container)

    asyncio.run(startup.keep_target_reviews_warm())

    [(warm_target, ready)] = calls
    assert ready is startup.sky_catalog_warmup_finished
    assert warm_target.func is startup.warm_target_reviews
    assert warm_target.keywords == {"log_level": logging.DEBUG}


def test_the_review_re_read_loop_does_nothing_without_a_built_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A container that was never built has no rewarmer: the loop returns."""
    fake_container = MagicMock()
    fake_container.target_review_rewarmer = None
    monkeypatch.setattr(startup, "container", fake_container)

    asyncio.run(startup.keep_target_reviews_warm())


def test_binding_helper_programs_does_nothing_off_windows() -> None:
    """Off Windows the job-object step is skipped and reports `False`."""
    if sys.platform == "win32":
        pytest.skip("this is the non-Windows case")
    assert startup.bind_helper_programs_to_backend_lifetime() is False


@pytest.mark.skipif(sys.platform != "win32", reason="job objects exist on Windows only")
def test_binding_helper_programs_joins_a_job_on_windows() -> None:
    """On Windows the backend joins a kill-on-close job once."""
    # A launcher's job may forbid nesting; then this logs and returns False.
    first = startup.bind_helper_programs_to_backend_lifetime()
    assert startup.bind_helper_programs_to_backend_lifetime() is first
