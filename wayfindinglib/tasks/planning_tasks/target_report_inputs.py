"""Purpose: Read what the target dataset report needs about one target.

Description: `build_target_dataset_report` (in ``target_report_tasks.py``)
works on plain data. This module fetches the part that belongs to one target
and shapes it:

* the target's stored measurements and the capture settings they point at
  (``astrometrics.quality``);
* which capture setting each stack belongs to;
* the frame records' altitudes and sensor temperatures;
* the calibration library's rows;
* the stored night plans of the target.

What the whole library knows (the site, the efficiency references, the sky
models, the frame observations and which frames were set aside) is read by
the shared builder, ``analytics/result_quality/library_inputs.py``, which the
target report, tonight's ranking and the night review all use.

Only Planning-legal sources are read: the science library through its public
root, the Foundation analytics and models, and the butler. Nothing here
imports Control and nothing is written.

How a stack is tied to a capture setting
----------------------------------------
Stack rows carry no capture setting id. The rows of a stack run include one
``stacking.frame_weight`` row for each member frame, and the frame rows carry
the setting. A stack therefore belongs to the setting that most of its member
frames belong to (a tie goes to the lowest setting id). A stack of several
settings is noted. The stacks of an exposure group (``groups/<name>`` or
``<stack>#group-<n>-<seconds>s``) belong to the setting whose exposure
matches the group's exposure to within half a second, when only one does.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from astrometricslib import (
    EARLIER_NIGHTS_VARIANT,
    AstrometricsError,
    CaptureSetting,
    QualityMeasurement,
    SubjectLevel,
    Target,
    frame_subject_id,
)
from wayfindinglib.analytics.result_quality.capture_overheads import EXPOSURE_MATCH_SECONDS
from wayfindinglib.models.planning.target_report import ForecastGate
from wayfindinglib.models.session.observation_session import ObservationSession

logger = logging.getLogger(__name__)

GROUP_STACK_PATTERN = re.compile(r"(?:/groups/|#group-)")
"""Marks the subject id of an exposure-group stack."""

GROUP_EXPOSURE_PATTERN = re.compile(r"#group-\d+-([0-9.]+)s$")
"""Reads the exposure from the subject id of a group with no stack file."""

QUARANTINE_METRICS = (
    "frame.quarantine_roundness",
    "frame.quarantine_sky_median_adu",
    "frame.detected_region_count",
)
"""The frame rows that the stacker's quarantine step stores."""

FRAME_WEIGHT_METRIC = "stacking.frame_weight"
GROUP_EXPOSURE_METRIC = "stacking.group_exposure_s"


@dataclass
class TargetRows:
    """The stored measurements of a target and what they point at.

    Attributes
    ----------
    rows : `list` [`QualityMeasurement`]
        The newest row for each subject, metric and channel, library stacks
        only (rows of subset stacks are left out).
    settings : `dict` [`int`, `CaptureSetting`]
        The capture settings the rows point at, by id.
    notes : `list` [`str`]
        Remarks made while reading, such as a stack of several settings.
    """

    rows: list[QualityMeasurement]
    settings: dict[int, CaptureSetting]
    notes: list[str] = field(default_factory=list)


def read_target_rows(astrometrics: Any, target_id: str) -> TargetRows:
    """Read a target's stored measurements and the settings they use.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library handle.
    target_id : `str`
        The target.

    Returns
    -------
    target_rows : `TargetRows`
        The rows and their capture settings. A library with no measurements
        gives empty lists.
    """
    rows = list(astrometrics.quality.query(target_id=target_id, library_only=True))
    ids = sorted({
        row.subject.capture_setting_id for row in rows if row.subject.capture_setting_id is not None
    })
    settings = dict(astrometrics.quality.capture_settings(ids=ids)) if ids else {}
    return TargetRows(rows=rows, settings=settings)


def read_earlier_nights_rows(astrometrics: Any, target_id: str) -> list[QualityMeasurement]:
    """Read the stored rows of a target's earlier-nights subset stacks.

    The subset stack (all observing nights but the last) keeps its rows apart
    from the library stack's: they carry the variant `EARLIER_NIGHTS_VARIANT`,
    which a read of the library's own rows leaves out. Every run is kept.
    Only the stack-level rows and the per-frame weights are read, which is
    what the forecast check needs.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library handle.
    target_id : `str`
        The target.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        The rows; empty when the target has no subset stack.
    """
    return [
        *astrometrics.quality.query(
            target_id=target_id, level="stack", variant=EARLIER_NIGHTS_VARIANT, latest_only=False
        ),
        *astrometrics.quality.query(
            target_id=target_id,
            level="frame",
            metric=FRAME_WEIGHT_METRIC,
            variant=EARLIER_NIGHTS_VARIANT,
            latest_only=False,
        ),
    ]


def read_forecast_runs(
    astrometrics: Any, target_id: str, library_rows: Iterable[QualityMeasurement]
) -> list[Any]:
    """Build a target's stack runs, with the subset runs and calibration.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library handle.
    target_id : `str`
        The target.
    library_rows : `Iterable` [`QualityMeasurement`]
        The target's library stack rows and frame weights with every run kept
        (``latest_only=False``, ``library_only=True``).

    Returns
    -------
    runs : `list` [`StackRunRecord`]
        The library runs and the earlier-nights subset runs, oldest first,
        each with its calibration record where one is stored. A library
        without ``calibration_identities`` (an old one) gives runs with none,
        and the forecast check then counts them as confounded.
    """
    from wayfindinglib.analytics.result_quality.forecast import (
        attach_calibration_identities,
        stack_runs_from_measurements,
    )

    reader = getattr(astrometrics.quality, "calibration_identities", None)
    identities = list(reader(target_id=target_id)) if callable(reader) else []
    runs = stack_runs_from_measurements(
        [*library_rows, *read_earlier_nights_rows(astrometrics, target_id)], include_earlier_nights=True
    )
    return attach_calibration_identities(runs, identities)


def forecast_gate_of_target(
    astrometrics: Any,
    target_id: str,
    library_rows: Iterable[QualityMeasurement],
    observations: Sequence[Any],
    *,
    configured_stack_weight: str | None = None,
    electrons_per_adu: float | None = None,
) -> ForecastGate:
    """Work out whether "take N more" is open for a target.

    The target report and tonight's ranking both use this, so they agree.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library handle.
    target_id : `str`
        The target.
    library_rows : `Iterable` [`QualityMeasurement`]
        The target's library stack rows and frame weights, every run kept.
    observations : `Sequence` [`FrameObservation`]
        The target's frames.
    configured_stack_weight : `str` or `None`, optional
        The configured stacker weight mode.
    electrons_per_adu : `float`, optional
        The camera gain, for the frame noise of a group the new frames start.

    Returns
    -------
    gate : `ForecastGate`
        ``closed``, ``exploratory`` or ``confirmed`` (`forecast_gate_state`).
    """
    from wayfindinglib.analytics.result_quality.forecast import forecast_check, forecast_gate_state

    runs = read_forecast_runs(astrometrics, target_id, library_rows)
    if not runs:
        return ForecastGate.CLOSED
    return forecast_gate_state(
        forecast_check(
            runs,
            observations,
            configured_stack_weight=configured_stack_weight,
            electrons_per_adu=electrons_per_adu,
        )
    )


def frame_altitudes(target: Target) -> dict[str, float]:
    """Read the altitude each light frame was taken at, by frame subject id.

    Parameters
    ----------
    target : `Target`
        The target, with its frame records.

    Returns
    -------
    altitudes : `dict` [`str`, `float`]
        The altitude of the pointing, in degrees, from the frame record. A
        frame with none is left out.
    """
    return {
        frame_subject_id(frame.path, target.id): float(frame.altitude_degrees)
        for frame in target.frames
        if frame.altitude_degrees is not None and str(frame.role).upper().endswith("LIGHT")
    }


def frame_sensor_temperatures(target: Target) -> dict[str, float]:
    """Read the sensor temperature of each light frame, by frame subject id.

    Parameters
    ----------
    target : `Target`
        The target, with its frame records.

    Returns
    -------
    temperatures : `dict` [`str`, `float`]
        The temperature in degrees Celsius. A frame with none is left out.
    """
    return {
        frame_subject_id(frame.path, target.id): float(frame.sensor_temperature_c)
        for frame in target.frames
        if frame.sensor_temperature_c is not None and str(frame.role).upper().endswith("LIGHT")
    }


# -----------------------------------------------------------------------------
# Stacks and settings
# -----------------------------------------------------------------------------


def _group_exposure(
    stack_id: str, rows_by_subject: Mapping[str, Sequence[QualityMeasurement]]
) -> float | None:
    """Find the exposure of an exposure-group stack.

    Parameters
    ----------
    stack_id : `str`
        The stack's subject id.
    rows_by_subject : `Mapping` [`str`, `Sequence` [`QualityMeasurement`]]
        The target's stack-level rows, by subject id.

    Returns
    -------
    exposure_seconds : `float` or `None`
        The stored group exposure, else the one written in the id, else
        ``None``.
    """
    for row in rows_by_subject.get(stack_id, ()):
        if row.metric == GROUP_EXPOSURE_METRIC and row.value is not None:
            return float(row.value)
    match = GROUP_EXPOSURE_PATTERN.search(stack_id)
    return float(match.group(1)) if match else None


def assign_stacks_to_settings(
    rows: Iterable[QualityMeasurement],
    settings: Mapping[int, CaptureSetting],
    member_frames_by_stack: Mapping[str, Sequence[str]],
) -> tuple[dict[str, int], list[str]]:
    """Tie each stack to the capture setting its frames belong to.

    Parameters
    ----------
    rows : `Iterable` [`QualityMeasurement`]
        The target's newest rows.
    settings : `Mapping` [`int`, `CaptureSetting`]
        The target's capture settings.
    member_frames_by_stack : `Mapping` [`str`, `Sequence` [`str`]]
        The member frames (their subject ids) of the newest run of each
        stack, from the ``stacking.frame_weight`` rows.

    Returns
    -------
    assignment, notes : `tuple`
        The setting id of each stack subject id that could be placed, and
        one sentence for each stack that holds more than one setting or that
        could not be placed.
    """
    frame_setting: dict[str, int] = {}
    stack_rows: dict[str, list[QualityMeasurement]] = defaultdict(list)
    for row in rows:
        if row.subject.level == SubjectLevel.FRAME and row.subject.capture_setting_id is not None:
            frame_setting[row.subject.subject_id] = row.subject.capture_setting_id
        elif row.subject.level == SubjectLevel.STACK and row.subject.variant is None:
            stack_rows[row.subject.subject_id].append(row)
    assignment: dict[str, int] = {}
    notes: list[str] = []
    for stack_id in sorted(stack_rows):
        if GROUP_STACK_PATTERN.search(stack_id):
            exposure = _group_exposure(stack_id, stack_rows)
            matches = sorted(
                setting_id
                for setting_id, setting in settings.items()
                if exposure is not None
                and setting.exposure_seconds is not None
                and abs(setting.exposure_seconds - exposure) <= EXPOSURE_MATCH_SECONDS
            )
            if len(matches) == 1:
                assignment[stack_id] = matches[0]
            else:
                notes.append(
                    f"The exposure-group stack {stack_id} could not be tied to one capture setting "
                    f"({len(matches)} settings match its exposure)."
                )
            continue
        members = member_frames_by_stack.get(stack_id, ())
        counts = Counter(frame_setting[frame_id] for frame_id in members if frame_id in frame_setting)
        if not counts:
            notes.append(
                f"The stack {stack_id} stored no member frames that carry a capture setting, so its rows "
                "belong to no setting."
            )
            continue
        best = max(sorted(counts), key=lambda setting_id: counts[setting_id])
        assignment[stack_id] = best
        if len(counts) > 1:
            notes.append(
                f"The stack {stack_id} holds frames of {len(counts)} capture settings; its rows are "
                f"assigned to the setting with most frames (id {best})."
            )
    return assignment, notes


# -----------------------------------------------------------------------------
# Calibration, sessions
# -----------------------------------------------------------------------------


def read_calibration_rows(astrometrics: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Read the calibration library's dark and flat rows.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library handle.

    Returns
    -------
    darks, flats, problem : `tuple`
        The rows of ``processing.calibration.query(detail="counts")`` and a
        sentence saying why they could not be read (``None`` when they could).
    """
    try:
        answer = astrometrics.processing.calibration.query(detail="counts")
    except (AstrometricsError, OSError) as error:
        return [], [], f"The calibration library could not be read: {error}"
    return list(answer.darks or []), list(answer.flats or []), None


def sessions_of_target(butler: Any, target_id: str) -> list[ObservationSession]:
    """Find the stored night plans that have a queue entry for a target.

    Parameters
    ----------
    butler : `DiskButler`
        The storage layer.
    target_id : `str`
        The target.

    Returns
    -------
    sessions : `list` [`ObservationSession`]
        The stored plans, in storage order.
    """
    return [
        session
        for session in butler.get_all("observation_session")
        if any(entry.target_id == target_id for entry in session.queue)
    ]


def median_or_none(values: Iterable[float | None]) -> float | None:
    """Take the median of the values that are not ``None``.

    Parameters
    ----------
    values : `Iterable` [`float` or `None`]
        The values.

    Returns
    -------
    median : `float` or `None`
        The median, or ``None`` when no value is given.
    """
    numbers = [float(value) for value in values if value is not None]
    return float(np.median(numbers)) if numbers else None
