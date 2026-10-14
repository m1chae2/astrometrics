"""Purpose: The steps an imaging queue entry takes, built from `control`.

Description: `advance_session` runs one cycle of an observing session's
queue, but every step that touches hardware is handed to it as
`SessionRunnerDependencies`. `imaging_dependencies` builds those steps
for an imaging queue from the `control` sub-API:

- the safety gate reads a fresh safety assessment
  (`control.safety.status`); "unknown" counts as unsafe;
- the delegation policy decides, per capability, whether a step is sent
  to the hardware, only recorded, or left to another program;
- the one action per entry is a slew to its target (`MOUNT_CONTROL`);
  a target without coordinates yet is captured where the mount points;
- the outcome is a capture of each exposure request
  (`control.imaging.capture_image`), with the package's dithering;
- each cycle is saved through the `DiskButler`.

Device fault states and meridian flips are not tracked for this queue
yet: no device counts as faulted and no flip is triggered.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from astrometricslib import AstrometricsError, ConflictError, InvalidArgumentError, NotFoundError
from wayfindinglib.models.planning.observation_package import FrameType
from wayfindinglib.models.policy.delegation import ObservatoryCapability
from wayfindinglib.tasks.execution_tasks.session_runner import ActionDisposition, SessionRunnerDependencies

if TYPE_CHECKING:
    from astrometricslib import FilterType
    from wayfindinglib.api.control import ObservatoryControl
    from wayfindinglib.drivers.butler import DiskButler
    from wayfindinglib.models.session.meridian_flip import MeridianFlipOutcome
    from wayfindinglib.models.session.observation_session import ObservationSession, QueuedObservationPackage

__all__ = ["capture_entry", "filter_name", "imaging_dependencies"]

logger = logging.getLogger(__name__)


def filter_name(filter_type: FilterType) -> str | None:
    """Return the name to turn the filter wheel to, or `None` for no filter.

    Parameters
    ----------
    filter_type : `FilterType`
        The requested filter.

    Returns
    -------
    name : `str` or `None`
        The filter's name, which the wheel matches to a slot.
    """
    return None if filter_type.name == "NONE" else filter_type.value


def capture_entry(control: ObservatoryControl, entry: QueuedObservationPackage) -> tuple[int, str]:
    """Capture every exposure request of one queue entry.

    Parameters
    ----------
    control : `ObservatoryControl`
        Captures the frames.
    entry : `QueuedObservationPackage`
        The entry, with its exposure requests and dithering.

    Returns
    -------
    frames_captured, detail : `int`, `str`
        How many frames were taken, and what happened. A capture that
        fails stops the entry; the frames taken so far are kept.
    """
    frames = 0
    for request in entry.exposure_requests:
        if request.frame_type != FrameType.LIGHT:
            logger.info("Capturing %s frames: the camera sets the frame type itself.", request.frame_type)
        try:
            result = control.imaging.capture_image(
                request.exposure_sec,
                count=request.count,
                filter_name=filter_name(request.filter),
                dither=entry.dither_config or False,
                delay_seconds=request.delay_sec,
            )
        except AstrometricsError as error:
            logger.warning("Capture for %s stopped: %s", entry.target_id, error.message)
            return frames, f"Capture stopped after {frames} frame(s): {error.message}"
        frames += result.frames_captured
    return frames, f"Captured {frames} frame(s)."


def _slew_to(control: ObservatoryControl, target_id: str) -> None:
    """Slew to a queue entry's target, if it has coordinates yet.

    Parameters
    ----------
    control : `ObservatoryControl`
        Slews the mount.
    target_id : `str`
        The library target.
    """
    try:
        control.mount.slew(target_id)
    except (InvalidArgumentError, NotFoundError) as error:
        logger.warning("Not slewing to %s: %s", target_id, error.message)


def _no_flip(entry: QueuedObservationPackage) -> MeridianFlipOutcome:
    """Refuse a meridian flip; the imaging queue never asks for one.

    Parameters
    ----------
    entry : `QueuedObservationPackage`
        The entry.

    Raises
    ------
    ConflictError
        Always.
    """
    raise ConflictError(f"Meridian flips are not run for the imaging queue (entry {entry.id}).")


def imaging_dependencies(control: ObservatoryControl, butler: DiskButler) -> SessionRunnerDependencies:
    """Build the queue-cycle steps for an imaging queue.

    Parameters
    ----------
    control : `ObservatoryControl`
        Reads safety and the policy, slews and captures.
    butler : `DiskButler`
        Saves the session after each cycle.

    Returns
    -------
    dependencies : `SessionRunnerDependencies`
        The steps `advance_session` needs.
    """

    def checkpoint(session: ObservationSession) -> None:
        """Save the session."""
        butler.put(session, "observation_session", {"session_id": session.id})

    def actions(entry: QueuedObservationPackage) -> list[ActionDisposition]:
        """Return the one action of an entry: slew to its target.

        Returns
        -------
        actions : `list` [`ActionDisposition`]
            The slew.
        """
        return [
            ActionDisposition(
                ObservatoryCapability.MOUNT_CONTROL, issue=lambda: _slew_to(control, entry.target_id)
            )
        ]

    def assessment() -> Any:
        """Read a fresh safety assessment.

        Returns
        -------
        assessment : `SafetyAssessment`
            The verdict.
        """
        return control.safety.status(include=["assessment"]).assessment

    return SessionRunnerDependencies(
        delegation_policy=control.safety.status(include=["delegation_policy"]).delegation_policy,
        get_safety_assessment=assessment,
        required_device_states=lambda entry: [],
        check_meridian_crossing=lambda entry, now: False,
        perform_meridian_flip=_no_flip,
        compute_actions=actions,
        capture_outcome=lambda entry: capture_entry(control, entry),
        checkpoint=checkpoint,
        now=lambda: datetime.now(UTC),
        record_id_factory=lambda: str(uuid.uuid4()),
    )
