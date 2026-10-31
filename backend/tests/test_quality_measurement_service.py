"""Purpose: Test the frame measurement RPC, its service and the event.

Description: The service starts the library's frame measurement job on a
thread and sends the ``quality:measured`` event when the job stored rows. The
library guards the work (one run per target, one run at a time), so the
service keeps no guard of its own. The sync service hands it the summaries of
the runs the sync started, and the event comes from those. The tests use
stand-ins for the science library and the socket manager.
"""

import asyncio
import threading
from collections.abc import Iterable, Mapping
from types import SimpleNamespace
from typing import Any

import pytest

from backend.container import container
from backend.public_interface import RPC_METHODS
from backend.routers.rpc_router import rpc_registry
from backend.services.infrastructure import thread_management
from backend.services.infrastructure.sync_service import SyncService
from backend.services.processing.quality_measurement_service import QualityMeasurementService


class _Sockets:
    """A stand-in socket manager that records the events it is given."""

    def __init__(self) -> None:
        """Start with no events."""
        self.events: list[tuple[str, dict]] = []
        self.sent = threading.Event()

    def broadcast_ui_event_sync(self, action: str, payload: dict | None = None) -> None:
        """Record one event and wake a waiting test."""
        self.events.append((action, payload or {}))
        self.sent.set()


class _Processing:
    """A stand-in processing API whose measurement job can be held."""

    def __init__(
        self, rows_written: int = 12, frames_measured: int = 3, already_running: bool = False
    ) -> None:
        """Set what the job reports.

        Parameters
        ----------
        rows_written : `int`, optional
            Rows the job reports it stored.
        frames_measured : `int`, optional
            Frames the job reports it measured.
        already_running : `bool`, optional
            Whether the library says another run has the target.
        """
        self.rows_written = rows_written
        self.frames_measured = frames_measured
        self.already_running = already_running
        self.calls: list[tuple[str, int | None]] = []
        self.release = threading.Event()
        self.release.set()

    def record_frame_measurements(self, target_id: str, limit: int | None = None) -> SimpleNamespace:
        """Wait to be released, then report the configured counts.

        Returns
        -------
        summary : `SimpleNamespace`
            The fields of `FrameMeasurementSummary` the service reads.
        """
        self.calls.append((target_id, limit))
        self.release.wait(timeout=5.0)
        return SimpleNamespace(
            target_id=target_id,
            frames_measured=self.frames_measured,
            rows_written=self.rows_written,
            observing_night_ids=["2026-10-13"],
            already_running=self.already_running,
            notes=["a measurement run for M 13 is already going"] if self.already_running else [],
        )


def _service(processing: _Processing, sockets: _Sockets) -> QualityMeasurementService:
    """Build the service over stand-in libraries.

    Returns
    -------
    service : `QualityMeasurementService`
        A service over the stand-in processing API and socket manager.
    """
    return QualityMeasurementService(SimpleNamespace(quality=processing), sockets)


def _wait_until_idle() -> None:
    """Wait for the service's background thread to finish."""
    for thread in threading.enumerate():
        if thread.name.startswith("quality-"):
            thread.join(timeout=5.0)


def test_the_method_is_declared_and_served_by_the_service() -> None:
    """The RPC is in the public interface and maps to the service's method."""
    assert "quality:record_frame_measurements" in RPC_METHODS
    assert rpc_registry._handlers["quality:record_frame_measurements"] == (
        "quality_measurement_service",
        "start_frame_measurements",
    )


def test_the_rpc_starts_the_job_and_the_event_follows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Called through the RPC registry, the job runs and the event is sent."""
    processing, sockets = _Processing(), _Sockets()
    service = _service(processing, sockets)
    monkeypatch.setattr(container, "quality_measurement_service", service, raising=False)

    reply = asyncio.run(
        rpc_registry.execute("quality:record_frame_measurements", {"target_id": "M 13", "limit": 5})
    )

    assert reply == {"started": True, "target_id": "M 13"}
    assert sockets.sent.wait(timeout=5.0)
    _wait_until_idle()
    assert processing.calls == [("M 13", 5)]
    assert sockets.events == [
        (
            "quality:measured",
            {"targetId": "M 13", "framesMeasured": 3, "rowsWritten": 12, "observingNightIds": ["2026-10-13"]},
        )
    ]


def test_a_run_that_stored_nothing_sends_no_event() -> None:
    """Zero new rows means nothing for the screens to read again."""
    processing, sockets = _Processing(rows_written=0, frames_measured=0), _Sockets()
    service = _service(processing, sockets)

    service.start_frame_measurements("M 13")
    _wait_until_idle()

    assert processing.calls == [("M 13", None)]
    assert sockets.events == []


def test_the_service_keeps_no_guard_of_its_own_because_the_library_guards() -> None:
    """Two starts both start a thread; the library picks who measures."""
    processing, sockets = _Processing(), _Sockets()
    processing.release.clear()
    service = _service(processing, sockets)

    first = service.start_frame_measurements("M 13")
    second = service.start_frame_measurements("M 13")
    processing.release.set()
    _wait_until_idle()

    assert first == {"started": True, "target_id": "M 13"}
    assert second == {"started": True, "target_id": "M 13"}
    assert len(processing.calls) == 2


def test_a_run_the_library_skipped_sends_no_event() -> None:
    """When another run has the target, nothing is measured or sent."""
    processing, sockets = _Processing(rows_written=0, frames_measured=0, already_running=True), _Sockets()
    service = _service(processing, sockets)

    service.start_frame_measurements("M 13")
    _wait_until_idle()

    assert processing.calls == [("M 13", None)]
    assert sockets.events == []


def test_a_failed_run_is_logged_and_the_target_can_be_started_again() -> None:
    """A library error does not stop a later start."""
    from astrometricslib import StorageError

    processing, sockets = _Processing(), _Sockets()

    def fail(target_id: str, limit: int | None = None) -> None:
        """Raise a storage error.

        Raises
        ------
        StorageError
            Always.
        """
        raise StorageError("database is locked")

    processing.record_frame_measurements = fail
    service = _service(processing, sockets)

    service.start_frame_measurements("M 13")
    _wait_until_idle()

    assert sockets.events == []
    assert service.start_frame_measurements("M 13")["started"] is True
    _wait_until_idle()


def _summary(target_id: str = "M 13", rows_written: int = 12, frames_measured: int = 3) -> dict[str, Any]:
    """Build a measurement summary as the frame sync returns it.

    Returns
    -------
    summary : `dict` [`str`, `Any`]
        The plain dict form of a `FrameMeasurementSummary`.
    """
    return {
        "target_id": target_id,
        "frames_measured": frames_measured,
        "rows_written": rows_written,
        "observing_night_ids": ["2026-10-12", "2026-10-13"],
    }


def test_announcing_summaries_sends_one_event_per_summary_that_stored_rows() -> None:
    """The event carries the run's own counts; an empty run sends nothing."""
    sockets = _Sockets()
    service = _service(_Processing(), sockets)

    service.announce_summaries([_summary(), _summary("M 57", rows_written=0, frames_measured=0)])

    assert sockets.events == [
        (
            "quality:measured",
            {
                "targetId": "M 13",
                "framesMeasured": 3,
                "rowsWritten": 12,
                "observingNightIds": ["2026-10-12", "2026-10-13"],
            },
        )
    ]


def test_announcing_nothing_sends_nothing() -> None:
    """A calibration folder sync returns no summaries and announces nothing."""
    sockets = _Sockets()
    _service(_Processing(), sockets).announce_summaries([])
    assert sockets.events == []


class _Remote:
    """A stand-in `control.remote` whose frame sync can fail."""

    def __init__(self, fail: bool = False, measurements: list | None = None) -> None:
        """Choose whether the sync raises, and what it returns."""
        self.fail = fail
        self.measurements = measurements if measurements is not None else []

    def sync_frames(self, target: str) -> dict[str, Any]:
        """Finish, or fail.

        Returns
        -------
        result : `dict` [`str`, `Any`]
            A successful sync with the measurement summaries.

        Raises
        ------
        OSError
            When the stand-in was told to fail.
        """
        if self.fail:
            raise OSError("link down")
        return {"success": True, "measurements": self.measurements}


class _Announcer:
    """A stand-in announcer that records its calls and can fail."""

    def __init__(self, fail: bool = False) -> None:
        """Choose whether the announcement raises."""
        self.fail = fail
        self.calls: list[list[dict]] = []

    def announce_summaries(self, summaries: Iterable[Mapping[str, Any]]) -> None:
        """Record the call, or fail.

        Raises
        ------
        RuntimeError
            When the stand-in was told to fail.
        """
        self.calls.append(list(summaries))
        if self.fail:
            raise RuntimeError("socket gone")


def _run_sync(target_id: str, remote: _Remote, announcer: _Announcer) -> None:
    """Run a sync to its end through the service.

    Parameters
    ----------
    target_id : `str`
        The target to sync.
    remote : `_Remote`
        The stand-in remote.
    announcer : `_Announcer`
        The stand-in announcer.
    """
    service = SyncService(SimpleNamespace(remote=remote), measurement_announcer=announcer)
    service.start_sync(target_id)
    thread_management._syncing[target_id].join(timeout=5.0)


def test_a_finished_sync_announces_the_summaries_the_sync_returned() -> None:
    """The announcement comes from the sync's own measurement summaries."""
    announcer = _Announcer()
    summaries = [_summary()]

    _run_sync("M 13", _Remote(measurements=summaries), announcer)

    assert announcer.calls == [summaries]


def test_a_sync_that_measured_nothing_announces_nothing() -> None:
    """No summaries (a calibration folder, or no new frames) means no call."""
    announcer = _Announcer()

    _run_sync("Dark", _Remote(), announcer)

    assert announcer.calls == []


def test_a_failed_announcement_never_breaks_the_sync() -> None:
    """The announcement error is logged; the sync thread ends normally."""
    announcer = _Announcer(fail=True)

    _run_sync("M 14", _Remote(measurements=[_summary("M 14")]), announcer)

    assert len(announcer.calls) == 1


def test_a_failed_sync_announces_nothing() -> None:
    """If the copy failed there is no measurement to announce."""
    announcer = _Announcer()

    _run_sync("M 15", _Remote(fail=True), announcer)

    assert announcer.calls == []


class _SubsetProcessing:
    """A stand-in processing API for the subset-stack jobs."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def measure_noise(self, target_id: str, kind: str = "growth", **options: Any) -> SimpleNamespace:
        """Record the call and report what the chosen kind stores.

        Returns
        -------
        result : `SimpleNamespace`
            The fields of the kind's result that the service reads.
        """
        self.calls.append((target_id, kind, options))
        if kind == "odd_even_difference":
            return SimpleNamespace(rows_written=9)
        if kind == "night_contributions":
            return SimpleNamespace(
                rows_written=6,
                nights=[SimpleNamespace(night_id="2026-10-02"), SimpleNamespace(night_id="2026-10-01")],
            )
        sections = options.get("include", ["odd_even_difference"])
        nights = (
            SimpleNamespace(
                rows_written=4,
                nights=[SimpleNamespace(night_id="2026-10-02"), SimpleNamespace(night_id="2026-10-03")],
            )
            if "night_contributions" in sections
            else None
        )
        return SimpleNamespace(
            rows_written=10,
            subsets=[SimpleNamespace(observing_night_ids=["2026-10-01"])],
            night_contributions=nights,
            odd_even_difference=SimpleNamespace(rows_written=9)
            if "odd_even_difference" in sections
            else None,
        )


def test_the_noise_rpc_is_declared_and_served_by_the_service() -> None:
    """The one noise RPC is declared and maps to the service's method."""
    assert "processing:measure_noise" in RPC_METHODS
    assert rpc_registry._handlers["processing:measure_noise"] == (
        "quality_measurement_service",
        "start_noise_measurement",
    )
    for retired in (
        "measure_noise_growth",
        "measure_night_contributions",
        "measure_odd_even_difference_noise",
    ):
        assert f"processing:{retired}" not in RPC_METHODS


def test_the_noise_growth_rpc_starts_the_job_and_announces_its_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """The series runs with the given steps and the event lists every night."""
    processing, sockets = _SubsetProcessing(), _Sockets()
    service = QualityMeasurementService(SimpleNamespace(processing=processing), sockets)
    monkeypatch.setattr(container, "quality_measurement_service", service, raising=False)

    reply = asyncio.run(
        rpc_registry.execute(
            "processing:measure_noise",
            {
                "target_id": "M 13",
                "kind": "growth",
                "maximum_steps": 2,
                "include": ["night_contributions"],
            },
        )
    )

    assert reply == {"started": True, "target_id": "M 13"}
    assert sockets.sent.wait(timeout=5.0)
    _wait_until_idle()
    assert processing.calls == [("M 13", "growth", {"maximum_steps": 2, "include": ["night_contributions"]})]
    assert sockets.events == [
        (
            "quality:measured",
            {
                "targetId": "M 13",
                "framesMeasured": 0,
                "rowsWritten": 14,
                "observingNightIds": ["2026-10-01", "2026-10-02", "2026-10-03"],
            },
        )
    ]


def test_the_night_contribution_rpc_starts_the_job_and_announces_its_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The nights job runs on its own thread and announces what it stored."""
    processing, sockets = _SubsetProcessing(), _Sockets()
    service = QualityMeasurementService(SimpleNamespace(processing=processing), sockets)
    monkeypatch.setattr(container, "quality_measurement_service", service, raising=False)

    reply = asyncio.run(
        rpc_registry.execute("processing:measure_noise", {"target_id": "M 13", "kind": "night_contributions"})
    )

    assert reply == {"started": True, "target_id": "M 13"}
    assert sockets.sent.wait(timeout=5.0)
    _wait_until_idle()
    assert processing.calls == [("M 13", "night_contributions", {})]
    assert sockets.events[0][1]["observingNightIds"] == ["2026-10-01", "2026-10-02"]
    assert sockets.events[0][1]["rowsWritten"] == 6


def test_the_odd_even_difference_rpc_starts_the_job_and_announces_its_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The odd–even difference job runs on its own thread, announcing rows."""
    processing, sockets = _SubsetProcessing(), _Sockets()
    service = QualityMeasurementService(SimpleNamespace(processing=processing), sockets)
    monkeypatch.setattr(container, "quality_measurement_service", service, raising=False)

    reply = asyncio.run(
        rpc_registry.execute("processing:measure_noise", {"target_id": "M 57", "kind": "odd_even_difference"})
    )

    assert reply == {"started": True, "target_id": "M 57"}
    assert sockets.sent.wait(timeout=5.0)
    _wait_until_idle()
    assert processing.calls == [("M 57", "odd_even_difference", {})]
    assert sockets.events[0][1]["rowsWritten"] == 9


def test_the_series_counts_the_odd_even_difference_rows_by_default() -> None:
    """By default the series' announcement counts the odd–even rows."""
    processing, sockets = _SubsetProcessing(), _Sockets()
    service = QualityMeasurementService(SimpleNamespace(processing=processing), sockets)

    assert service.start_noise_measurement("M 57")["started"] is True
    assert sockets.sent.wait(timeout=5.0)
    _wait_until_idle()

    assert processing.calls == [("M 57", "growth", {})]
    assert sockets.events[0][1]["rowsWritten"] == 19


def test_a_failed_subset_job_is_logged_and_sends_no_event() -> None:
    """A library error on the thread is caught and nothing is announced."""
    from astrometricslib import NotFoundError

    processing, sockets = _SubsetProcessing(), _Sockets()

    def fail(target_id: str, **_keywords: Any) -> None:
        """Raise a not-found error.

        Raises
        ------
        NotFoundError
            Always.
        """
        raise NotFoundError("no stack")

    processing.measure_noise = fail
    service = QualityMeasurementService(SimpleNamespace(processing=processing), sockets)

    assert service.start_noise_measurement("M 13")["started"] is True
    _wait_until_idle()

    assert sockets.events == []
