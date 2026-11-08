"""Purpose: Tests for the container's start-up and shut-down handling.

Description: Each test uses its own `Container`, never the shared one. They
check that a failed start-up cleans up after itself, that shutdown stops what
was started, and that shutdown is safe to repeat.
"""

from unittest.mock import MagicMock

import pytest

from backend.container import Container


def test_shutdown_stops_the_maintenance_thread_and_the_indi_worker() -> None:
    """Shutdown stops both background pieces and marks the container down.

    The INDI worker process belongs to the Wayfinder, so `control.close()`
    stops it.
    """
    container = Container()
    container.maintenance_service = MagicMock()
    container.wayfinder = MagicMock()
    container.initialized = True

    container.shutdown_resources()

    container.maintenance_service.stop.assert_called_once()
    container.wayfinder.control.close.assert_called_once_with()
    assert container.initialized is False


def test_shutdown_stops_the_stacking_programs_through_the_public_name() -> None:
    """Shutdown stops Siril directly, not through an `atexit` hook."""
    container = Container()
    container.astrometrics = MagicMock()
    container.wayfinder = MagicMock()

    container.shutdown_resources()

    container.astrometrics.processing.stop_stacking_programs.assert_called_once_with()
    container.wayfinder.control.close.assert_called_once_with()


def test_a_failure_stopping_siril_does_not_stop_the_rest_of_shutdown() -> None:
    """The observatory control is still closed when Siril cannot be stopped."""
    container = Container()
    container.astrometrics = MagicMock()
    container.astrometrics.processing.stop_stacking_programs.side_effect = RuntimeError("boom")
    container.wayfinder = MagicMock()

    container.shutdown_resources()

    container.wayfinder.control.close.assert_called_once_with()


def test_shutdown_closes_observatory_control() -> None:
    """Shutdown stops observatory control's background readers too."""
    container = Container()
    container.wayfinder = MagicMock()

    container.shutdown_resources()

    container.wayfinder.control.close.assert_called_once_with()


def test_shutdown_is_safe_on_a_container_that_never_started() -> None:
    """Nothing was built, so there is nothing to stop and no error."""
    container = Container()

    container.shutdown_resources()
    container.shutdown_resources()

    assert container.initialized is False


def test_a_failed_start_up_stops_what_was_started_and_resets(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure after the maintenance thread started leaves no thread."""
    container = Container()
    maintenance = MagicMock()

    def failing_build() -> None:
        container.maintenance_service = maintenance
        raise RuntimeError("step failed")

    monkeypatch.setattr(container, "_build_resources", failing_build)

    with pytest.raises(RuntimeError, match="step failed"):
        container.init_resources()

    maintenance.stop.assert_called_once()
    assert container.maintenance_service is None
    assert container.initialized is False


def test_init_does_nothing_when_already_initialized(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second call must not build the services again."""
    container = Container()
    container.initialized = True
    build = MagicMock()
    monkeypatch.setattr(container, "_build_resources", build)

    container.init_resources()

    build.assert_not_called()


def test_running_work_is_empty_when_everything_is_idle() -> None:
    """Idle services report no work."""
    container = Container()
    container.job_service = MagicMock(get_active_jobs=MagicMock(return_value=[]))
    container.sync_service = MagicMock(
        get_all_active_syncs=MagicMock(return_value=[]), is_log_sync_running=MagicMock(return_value=False)
    )
    container.target_imaging_executor = MagicMock(is_running=MagicMock(return_value=False))
    container.auto_download_service = MagicMock(pending_timer_count=MagicMock(return_value=0))

    assert container._running_work() == []


def test_a_failed_job_check_counts_as_busy() -> None:
    """When the job list cannot be read, a restart is not allowed."""
    container = Container()
    container.job_service = MagicMock(get_active_jobs=MagicMock(side_effect=RuntimeError("db locked")))

    assert container._running_work() == ["could not check the running jobs"]


def test_running_work_reports_imaging_log_copy_and_waiting_downloads() -> None:
    """Each kind of work that starts no job still blocks a restart."""
    container = Container()
    container.job_service = MagicMock(get_active_jobs=MagicMock(return_value=[]))
    container.sync_service = MagicMock(
        get_all_active_syncs=MagicMock(return_value=[]), is_log_sync_running=MagicMock(return_value=True)
    )
    container.target_imaging_executor = MagicMock(is_running=MagicMock(return_value=True))
    container.auto_download_service = MagicMock(pending_timer_count=MagicMock(return_value=2))

    work = container._running_work()

    assert "guide log copy running" in work
    assert "target imaging session running" in work
    assert "2 automatic frame download(s) waiting to start" in work
