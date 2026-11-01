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
