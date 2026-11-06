"""Tests for measuring new dark and bias frames after an ingest or a sync.

The library's rescan does not measure darks and biases. The backend starts
the calibration measurement itself when frames of those kinds arrive, runs
one at a time and tells the app when it ends.
"""

import threading
from types import SimpleNamespace
from typing import Any

from backend.services.infrastructure import thread_management
from backend.services.infrastructure.sync_service import SyncService
from backend.services.processing.image_processing_service import ImageProcessingService
from backend.services.processing.ingestion_service import IngestionService


class _Calibration:
    """A calibration catalog that records measuring calls and can wait."""

    def __init__(self) -> None:
        """Start with no calls; a call waits for the release."""
        self.calls: list[dict[str, Any]] = []
        self.release = threading.Event()
        self.release.set()

    def measure_frames(
        self,
        kind: str | None = None,
        camera_id: str | None = None,
        force: bool = False,
        register_job: bool = True,
    ) -> dict[str, int]:
        """Record the call and wait for the release.

        Returns
        -------
        report : `dict` [`str`, `int`]
            A small report.
        """
        self.calls.append({"kind": kind, "force": force, "register_job": register_job})
        self.release.wait(timeout=5.0)
        return {"measuredCount": 1}


def _service(calibration: _Calibration, events: list[tuple[str, dict]]) -> ImageProcessingService:
    """Make a service that records its UI events.

    Returns
    -------
    service : `ImageProcessingService`
        The service over the stand-in catalog.
    """
    socket_manager = SimpleNamespace(
        broadcast_ui_event_sync=lambda action, payload: events.append((action, payload))
    )
    return ImageProcessingService(
        calibration=calibration,
        config_service=SimpleNamespace(get_logs_path=lambda: "/tmp/unused"),
        socket_manager=socket_manager,
    )


def _wait_until_idle(service: ImageProcessingService) -> None:
    """Wait until no measuring pass is running.

    Raises
    ------
    AssertionError
        If the measuring does not end in time.
    """
    for _ in range(200):
        if not service._calibration_measurement_running:
            return
        threading.Event().wait(0.02)
    raise AssertionError("the measuring did not end")


def test_new_darks_start_one_measurement_and_the_end_is_announced() -> None:
    """New darks start one job; its end sends the event."""
    calibration, events = _Calibration(), []
    service = _service(calibration, events)

    assert service.measure_new_calibration_frames(["dark"]) is True
    _wait_until_idle(service)

    assert calibration.calls == [{"kind": "dark", "force": False, "register_job": True}]
    assert events == [("calibration:measured", {})]


def test_darks_and_biases_together_run_one_job_for_both() -> None:
    """Both kinds are measured by one job with no kind filter."""
    calibration, events = _Calibration(), []
    service = _service(calibration, events)

    service.measure_new_calibration_frames(["dark", "bias", "flat"])
    _wait_until_idle(service)

    assert [call["kind"] for call in calibration.calls] == [None]


def test_flats_and_lights_start_nothing() -> None:
    """Only dark and bias frames are measured."""
    calibration, events = _Calibration(), []
    service = _service(calibration, events)

    assert service.measure_new_calibration_frames(["flat"]) is False
    assert service.measure_new_calibration_frames([]) is False
    assert calibration.calls == []
    assert events == []


def test_a_running_measurement_is_not_duplicated_but_one_more_pass_follows() -> None:
    """A request during a run waits and is covered by one more pass."""
    calibration, events = _Calibration(), []
    calibration.release.clear()
    service = _service(calibration, events)

    assert service.measure_new_calibration_frames(["dark"]) is True
    assert service.measure_new_calibration_frames(["bias"]) is False
    assert len(calibration.calls) <= 1

    calibration.release.set()
    _wait_until_idle(service)

    assert [call["kind"] for call in calibration.calls] == ["dark", "bias"]
    assert len(events) == 2


class _Recorder:
    """Records the kinds a trigger was called with."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.calls: list[list[str]] = []

    def __call__(self, kinds: Any) -> bool:
        """Record the kinds.

        Returns
        -------
        started : `bool`
            Always `True`.
        """
        self.calls.append(sorted(kinds))
        return True


def _sync(folder: str, added_by_folder: dict[str, int]) -> _Recorder:
    """Run one sync of `folder` and return the trigger it called.

    Returns
    -------
    trigger : `_Recorder`
        The recorder the sync called.
    """
    recorder = _Recorder()
    remote = SimpleNamespace(
        sync_frames=lambda target, on_copied=None: SimpleNamespace(
            success=True, measurements=None, added_by_folder=added_by_folder
        )
    )
    service = SyncService(observatory_api=SimpleNamespace(remote=remote), calibration_trigger=recorder)
    service.start_sync(folder)
    thread_management._syncing[folder].join(timeout=5.0)
    return recorder


def test_a_dark_folder_sync_that_added_darks_triggers_the_measurement() -> None:
    """The sync reports where it filed frames; darks and biases trigger."""
    recorder = _sync("Dark", {"darks/ASI533/100": 4, "biases/ASI533/100": 2, "flats/x": 1})
    assert recorder.calls == [["bias", "dark"]]


def test_a_flat_only_or_target_sync_triggers_nothing() -> None:
    """Flats, empty syncs and target syncs start no measurement."""
    assert _sync("Flat", {"flats/ASI533/100": 4}).calls == []
    assert _sync("Dark", {}).calls == []
    assert _sync("M 31", {"darks/x": 1}).calls == []


class _Processing:
    """Records the kinds the ingestion asked to measure."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.calls: list[list[str]] = []

    def measure_new_calibration_frames(self, kinds: Any) -> bool:
        """Record the kinds.

        Returns
        -------
        started : `bool`
            Always `True`.
        """
        self.calls.append(list(kinds))
        return True


def test_an_ingest_that_added_darks_asks_for_the_measurement() -> None:
    """Only dark or bias additions reach the image processing service."""
    processing = _Processing()
    service = IngestionService(image_processing_service=processing)  # type: ignore[arg-type]

    service._measure_new_calibration_frames("job", ["flat"])
    service._measure_new_calibration_frames("job", [])
    assert processing.calls == []

    service._measure_new_calibration_frames("job", ["dark", "flat"])
    assert processing.calls == [["dark", "flat"]]
