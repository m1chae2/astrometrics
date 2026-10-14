"""Purpose: Tests for the container's start-up and shut-down handling.

Description: Each test uses its own `Container`, never the shared one. They
check that a failed start-up cleans up after itself, that shutdown stops what
was started, and that shutdown is safe to repeat.
"""

from unittest.mock import MagicMock

import pytest

from backend.container import Container


def test_shutdown_stops_the_maintenance_thread_and_the_indi_client() -> None:
    """Shutdown stops both background pieces and marks the container down."""
    container = Container()
    container.maintenance_service = MagicMock()
    container.indi_worker_client = MagicMock()
    container.initialized = True

    container.shutdown_resources()

    container.maintenance_service.stop.assert_called_once()
    container.indi_worker_client.stop.assert_called_once()
    assert container.initialized is False


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
