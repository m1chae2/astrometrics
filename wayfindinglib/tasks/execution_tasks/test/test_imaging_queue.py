"""Purpose: Tests for running an imaging queue from `control`.

Description: `imaging_dependencies` builds the queue-cycle steps from
`control`, `capture_entry` captures an entry's exposure requests, and
`run_queue` keeps advancing a session until nothing is left to run. The
tests use a stand-in `control` that records what it is asked to do, and
check: a safe, authoritative cycle slews to the target and captures each
request with its filter and dithering; an unsafe assessment suspends the
session before anything moves; a failed capture is recorded on the entry;
and `ObservationExecution.advance_session` with a stop event works the
whole queue through and saves it.
"""

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import HardwareError, InvalidArgumentError
from wayfindinglib.api.execution import ObservationExecution
from wayfindinglib.models.planning.observation_package import DitherConfig, ExposureRequest, FrameType
from wayfindinglib.models.policy.delegation import (
    CapabilityDelegation,
    DelegationPolicy,
    DelegationState,
    ObservatoryCapability,
)
from wayfindinglib.models.policy.safety import SafetyAssessment, SafetyVerdict
from wayfindinglib.models.session.capture_result import CaptureResult
from wayfindinglib.models.session.observation_session import (
    ObservationSession,
    QueuedObservationPackage,
    QueueEntryStatus,
    SessionStatus,
    StartTimeMode,
)
from wayfindinglib.tasks.execution_tasks import imaging_queue, queue_runner


class _Control:
    """A stand-in `control` that records slews and captures."""

    def __init__(
        self, verdict: SafetyVerdict = SafetyVerdict.SAFE, capture_error: Exception | None = None
    ) -> None:
        """Choose the safety verdict and whether captures fail."""
        self.calls: list[tuple[str, Any]] = []
        policy = DelegationPolicy(
            id="default",
            capability_delegations=[
                CapabilityDelegation(
                    capability=ObservatoryCapability.MOUNT_CONTROL, state=DelegationState.AUTHORITATIVE
                )
            ],
        )
        assessment = SimpleNamespace(permits_observing=lambda: verdict == SafetyVerdict.SAFE, verdict=verdict)
        self.safety = SimpleNamespace(
            status=lambda include: SimpleNamespace(delegation_policy=policy, assessment=assessment)
        )
        self.mount = SimpleNamespace(slew=lambda target: self.calls.append(("slew", target)))

        def capture(exposure_seconds: float, **options: Any) -> CaptureResult:
            """Record a capture, or fail.

            Returns
            -------
            result : `CaptureResult`
                Every frame taken. With a capture error set, that error
                is raised instead.
            """
            if capture_error is not None:
                raise capture_error
            self.calls.append(("capture", (exposure_seconds, options)))
            return CaptureResult(frames_captured=options["count"], exposure_seconds=exposure_seconds)

        self.imaging = SimpleNamespace(capture_image=capture)


class _Butler:
    """Keeps saved sessions in memory."""

    def __init__(self) -> None:
        """Start empty."""
        self.saved: dict[str, ObservationSession] = {}

    def put(self, session: ObservationSession, kind: str, key: dict[str, str]) -> None:
        """Keep a session."""
        self.saved[key["session_id"]] = session.model_copy(deep=True)

    def get(self, kind: str, key: dict[str, str]) -> ObservationSession | None:
        """Return a kept session.

        Returns
        -------
        session : `ObservationSession` or `None`
            A copy of the saved session.
        """
        saved = self.saved.get(key["session_id"])
        return saved.model_copy(deep=True) if saved else None


def _entry(target_id: str = "M 81", start: datetime | None = None) -> QueuedObservationPackage:
    """Build a queue entry with two exposure requests.

    Returns
    -------
    entry : `QueuedObservationPackage`
        An L and an Ha request, dithering every 2 frames.
    """
    return QueuedObservationPackage(
        id=f"entry-{target_id}",
        observation_package_id="package",
        target_id=target_id,
        exposure_requests=[
            ExposureRequest(frame_type=FrameType.LIGHT, filter="Luminance", exposure_sec=60.0, count=3),
            ExposureRequest(frame_type=FrameType.LIGHT, filter="Ha", exposure_sec=120.0, count=2),
        ],
        dither_config=DitherConfig(enabled=True, every_n_frames=2),
        start_time_mode=StartTimeMode.SOONEST,
        computed_start_time=start or datetime.now(UTC) - timedelta(minutes=1),
    )


def _session(*entries: QueuedObservationPackage) -> ObservationSession:
    """Build a planned session holding `entries`.

    Returns
    -------
    session : `ObservationSession`
        The session.
    """
    return ObservationSession(
        id="s1",
        night_date=datetime.now(UTC).date(),
        site_profile_id="site",
        telescope_id="t",
        camera_id="c",
        queue=list(entries),
    )


def test_a_safe_cycle_slews_and_captures_every_request() -> None:
    """The target is slewed to, then each request captured with its filter."""
    from wayfindinglib.tasks.execution_tasks.session_runner import advance_session

    control = _Control()
    butler = _Butler()
    session = advance_session(_session(_entry()), imaging_queue.imaging_dependencies(control, butler))

    entry = session.queue[0]
    assert entry.status == QueueEntryStatus.COMPLETED
    assert entry.frames_captured == 5
    assert control.calls[0] == ("slew", "M 81")
    captures = [arguments for name, arguments in control.calls if name == "capture"]
    assert [(seconds, options["filter_name"], options["count"]) for seconds, options in captures] == [
        (60.0, "Luminance", 3),
        (120.0, "Ha", 2),
    ]
    assert captures[0][1]["dither"].every_n_frames == 2
    assert butler.saved["s1"].status == SessionStatus.COMPLETED


def test_an_unsafe_assessment_suspends_before_anything_moves() -> None:
    """Unknown or unsafe weather suspends the session; nothing moves."""
    from wayfindinglib.tasks.execution_tasks.session_runner import advance_session

    control = _Control(verdict=SafetyVerdict.UNKNOWN)
    session = advance_session(_session(_entry()), imaging_queue.imaging_dependencies(control, _Butler()))

    assert session.status == SessionStatus.SUSPENDED
    assert control.calls == []


def test_a_failed_capture_is_recorded_on_the_entry() -> None:
    """A capture that fails ends the entry with the reason."""
    frames, detail = imaging_queue.capture_entry(
        _Control(capture_error=HardwareError("Camera offline.")), _entry()
    )

    assert frames == 0
    assert "Camera offline." in detail


def test_a_target_without_coordinates_is_not_slewed_to() -> None:
    """A slew the target cannot take is skipped, and the capture goes on."""
    from wayfindinglib.tasks.execution_tasks.session_runner import advance_session

    control = _Control()

    def refuse(target: str) -> None:
        """Refuse the slew.

        Raises
        ------
        InvalidArgumentError
            Always.
        """
        raise InvalidArgumentError("No coordinates yet.")

    control.mount = SimpleNamespace(slew=refuse)
    session = advance_session(_session(_entry()), imaging_queue.imaging_dependencies(control, _Butler()))

    assert session.queue[0].frames_captured == 5


def test_no_filter_leaves_the_wheel_alone() -> None:
    """A request with no filter turns no wheel."""
    from astrometricslib import FilterType

    assert imaging_queue.filter_name(FilterType.NONE) is None
    assert imaging_queue.filter_name(FilterType.OIII) == "OIII"


def test_advance_session_with_a_stop_event_works_the_queue_through() -> None:
    """Both entries run in turn, and the saved session is complete."""
    butler = _Butler()
    butler.put(_session(_entry("M 81"), _entry("M 13")), "observation_session", {"session_id": "s1"})
    execution = ObservationExecution(SimpleNamespace(), butler, control=_Control())

    session = execution.advance_session("s1", stop=threading.Event())

    assert [entry.status for entry in session.queue] == [QueueEntryStatus.COMPLETED] * 2
    assert butler.saved["s1"].status == SessionStatus.COMPLETED


def test_the_runner_waits_for_a_later_start_and_stops_when_asked() -> None:
    """An entry whose start is ahead is waited for until stop is set."""
    later = _session(_entry(start=datetime.now(UTC) + timedelta(hours=1)))
    stop = threading.Event()
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        """Record the wait and ask the runner to stop.

        Parameters
        ----------
        seconds : `float`
            The wait.
        """
        waits.append(seconds)
        stop.set()

    session = queue_runner.run_queue(lambda: later, stop, sleep=sleep)

    assert session is later
    assert waits == [queue_runner.POLL_SECONDS]


def test_advance_session_without_control_or_steps_is_refused() -> None:
    """Building the imaging steps needs `control`."""
    from astrometricslib import ConfigurationError

    execution = ObservationExecution(SimpleNamespace(), _Butler())
    with pytest.raises(ConfigurationError):
        execution.advance_session(_session(_entry()))


def test_safety_assessment_model_is_what_the_gate_reads() -> None:
    """The real assessment model answers the gate's question."""
    assessment = SafetyAssessment(verdict=SafetyVerdict.UNKNOWN, rule_set_id="r", reasons=[])
    assert assessment.permits_observing() is False
