"""Purpose: Time spent between exposures (download, dither settle, autofocus).

Description: Gains are counted per hour of the night, not per hour of
exposure. For 60 frames of 30 s with a dither every 3 frames, exposure time
alone understates the hour by about 10 to 30 percent (quality-metrics design,
issue #93).
This module finds the overheads from the stored Ekos session records
(`EkosSessionContext`, readable through the Butler) and turns a frame count
into wall-clock time.

The records give the time each exposure finished and its length, and the
time each autofocus run ended. The gap before a frame is
``finish(frame) - finish(previous frame) - exposure(frame)``:

* The smallest gaps are plain download (the 10th percentile of the gaps
  that hold no autofocus run).
* A gap that exceeds the download by more than `DITHER_DETECTION_SECONDS`,
  holds no autofocus run, and is shorter than `PAUSE_SECONDS` is a dither
  settle. Its extra time is the settle time.
* A gap that holds an autofocus run, and is shorter than `PAUSE_SECONDS`,
  gives the autofocus time (the gap minus the download).

Fewer than `MINIMUM_GAPS` gaps, or no gap of a kind, falls back on the
design-estimate default for that part, and the result says so. All functions
are pure.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import pairwise

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from astrometricslib.parameters import (
    EquipmentKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    resolve_parameter,
)
from wayfindinglib.models.session.ekos_session import EkosSessionContext

_MODEL_CONFIG = ConfigDict(populate_by_name=True, alias_generator=to_camel)

DEFAULT_DOWNLOAD_SECONDS = TunedParameter(
    name="wayfinding.default_download_seconds",
    default=5.0,
    unit="s per frame (the camera download time)",
    description="Download time per frame when none is recorded, in seconds.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.MOUNT, EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="WAY-D33",
)
"""Download time per frame when none is recorded, in seconds. A USB 3 astronomy
camera reads out in 1 to 2 s and the file is saved in about as long; a DSLR
takes 5 to 10 s. Design estimate, register row WAY-D33."""

DEFAULT_DITHER_SETTLE_SECONDS = TunedParameter(
    name="wayfinding.default_dither_settle_seconds",
    default=20.0,
    unit="s per dither (the guider settle time)",
    description="Dither settle time when none is recorded, in seconds.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.MOUNT, EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="WAY-D33",
)
"""Dither settle time when none is recorded, in seconds. The guider must move
the
star and settle within its tolerance; 15 to 30 s is typical. Design estimate,
register row WAY-D33."""

DEFAULT_AUTOFOCUS_SECONDS = TunedParameter(
    name="wayfinding.default_autofocus_seconds",
    default=120.0,
    unit="s per autofocus run (the focuser)",
    description="Autofocus run time when none is recorded, in seconds.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.MOUNT, EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="WAY-D33",
)
"""Autofocus run time when none is recorded, in seconds. A V-curve of about 9
points of 3 to 5 s frames and moves takes 1.5 to 3 minutes. Design estimate,
register row WAY-D33."""

DITHER_EVERY_FRAMES = TunedParameter(
    name="wayfinding.dither_every_frames",
    default=3,
    unit="frames between dithers",
    description="Frames between dithers, when a run of frames is priced and the sequence says nothing else.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.PROCESSING_SETTING,
    register_entry="WAY-D71",
    config_key=("Wayfinding.Planning", "dither_every_frames"),
)
"""Frames between dithers, when a run of frames is priced and the sequence says
nothing else. It is the cadence of the wall-clock example (60 frames of
30 s with a dither every 3 frames). The target report and tonight's ranking
both use it, so they price the same hour. Design estimate, register row
WAY-D71."""

AUTOFOCUS_RUNS_PER_HOUR = TunedParameter(
    name="wayfinding.autofocus_runs_per_hour",
    default=1,
    unit="runs per hour of imaging",
    description=(
        "Autofocus runs per hour of imaging, when a run of frames is priced and the plan says nothing else."
    ),
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.PROCESSING_SETTING,
    register_entry="WAY-D71",
    config_key=("Wayfinding.Planning", "autofocus_runs_per_hour"),
)
"""Autofocus runs per hour of imaging, when a run of frames is priced and the
plan says nothing else. Ekos refocuses on a filter change or a temperature
change, about once an hour on a cooling night. The target report and tonight's
ranking both use it. Design estimate, register row WAY-D71."""

DOWNLOAD_PERCENTILE = TunedParameter(
    name="wayfinding.download_percentile",
    default=10.0,
    unit="percentile of the recorded gaps",
    description="The download time is this percentile of the plain gaps: the quickest gaps hold no dither.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.MOUNT, EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="WAY-D33",
)
"""The download time is this percentile of the plain gaps: the quickest gaps
hold
no dither. Design estimate, register row WAY-D33."""

DITHER_DETECTION_SECONDS = TunedParameter(
    name="wayfinding.dither_detection_seconds",
    default=3.0,
    unit="s beyond the download time",
    description="A gap this much longer than the download counts as holding a dither.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.MOUNT, EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="WAY-D33",
)
"""A gap this much longer than the download counts as holding a dither. The
download time varies by about a second, so 3 s separates a dither from that
scatter. Design estimate, register row WAY-D33."""

PAUSE_SECONDS = TunedParameter(
    name="wayfinding.pause_seconds",
    default=600.0,
    unit="s (scales with the sequence overheads)",
    description=(
        "A gap longer than this (10 minutes) is a pause, a meridian flip or a stop, not an overhead of "
        "the sequence."
    ),
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.MOUNT, EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="WAY-D33",
)
"""A gap longer than this (10 minutes) is a pause, a meridian flip or a stop,
not
an overhead of the sequence. It is left out. Design estimate, register row
WAY-D33."""

MINIMUM_GAPS = TunedParameter(
    name="wayfinding.minimum_gaps",
    default=10,
    unit="recorded gaps",
    description="Fewest gaps that make the download estimate.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.MOUNT, EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="WAY-D33",
)
"""Fewest gaps that make the download estimate. With fewer, the 10th percentile
is
just the smallest gap. Design estimate, register row WAY-D33."""

EXPOSURE_MATCH_SECONDS = TunedParameter(
    name="wayfinding.exposure_match_seconds",
    default=0.5,
    unit="s (scales with the exposure display precision)",
    description="Exposures within this many seconds count as the same setting.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.MOUNT, EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="WAY-D33",
)
"""Exposures within this many seconds count as the same setting. Ekos rounds
exposures to the displayed precision. Design choice, register row WAY-D33."""

CAPTURE_OVERHEADS_ENTRY = "WAY-D33"
"""Register row behind the defaults and the thresholds."""


class CaptureOverheads(BaseModel):
    """Time spent between exposures for one capture setting.

    Parameters
    ----------
    download_seconds : `float`
        Download and save time per frame, in seconds.
    dither_settle_seconds : `float`
        Extra time for one dither, in seconds.
    autofocus_seconds : `float`
        Time for one autofocus run, in seconds.
    download_from_records : `bool`
        True when `download_seconds` came from the records, false for the
        design-estimate default.
    dither_from_records : `bool`
        The same for the dither settle time.
    autofocus_from_records : `bool`
        The same for the autofocus time.
    gaps_used : `int`
        How many frame gaps the estimates rest on.
    register_entry : `str`
        The register row behind the defaults.
    """

    model_config = _MODEL_CONFIG

    download_seconds: float = Field(ge=0.0)
    dither_settle_seconds: float = Field(ge=0.0)
    autofocus_seconds: float = Field(ge=0.0)
    download_from_records: bool
    dither_from_records: bool
    autofocus_from_records: bool
    gaps_used: int = Field(ge=0)
    register_entry: str = CAPTURE_OVERHEADS_ENTRY


class WallClockEstimate(BaseModel):
    """The wall-clock time of a run of frames.

    Parameters
    ----------
    exposure_s : `float`
        Total exposure time, in seconds.
    download_seconds : `float`
        Total download time.
    dither_seconds : `float`
        Total dither settle time.
    autofocus_seconds : `float`
        Total autofocus time.
    total_seconds : `float`
        The sum of the four.
    overhead_fraction : `float`
        Overhead over exposure time (0.2 means the hour is 20 percent
        longer than the exposure alone).
    """

    model_config = _MODEL_CONFIG

    exposure_s: float
    download_seconds: float
    dither_seconds: float
    autofocus_seconds: float
    total_seconds: float
    overhead_fraction: float


def estimate_capture_overheads(
    contexts: Sequence[EkosSessionContext],
    exposure_s: float | None = None,
    filter_name: str | None = None,
) -> CaptureOverheads:
    """Find the overheads of a capture setting from Ekos session records.

    Parameters
    ----------
    contexts : `Sequence` [`EkosSessionContext`]
        The stored session records.
    exposure_s : `float` or `None`, optional
        Use only captures of this exposure length (within half a second).
        ``None`` uses all.
    filter_name : `str` or `None`, optional
        Use only captures through this filter. ``None`` uses all.

    Returns
    -------
    overheads : `CaptureOverheads`
        Each part from the records when enough were seen, else the default.
    """
    plain_gaps: list[float] = []
    dither_gaps: list[float] = []
    focus_gaps: list[float] = []
    all_gaps: list[tuple[float, bool]] = []
    for context in contexts:
        captures = sorted(
            (
                capture
                for capture in context.captures
                if (
                    exposure_s is None
                    or abs(capture.exposure_s - exposure_s) <= resolve_parameter(EXPOSURE_MATCH_SECONDS).value
                )
                and (filter_name is None or capture.filter_name == filter_name)
            ),
            key=lambda capture: capture.completed_at,
        )
        focus_times = [run.timestamp for run in context.autofocus_runs]
        for previous, current in pairwise(captures):
            gap = current.completed_at - previous.completed_at - current.exposure_s
            if gap < 0.0 or gap > resolve_parameter(PAUSE_SECONDS).value:
                continue
            has_focus = any(previous.completed_at <= time <= current.completed_at for time in focus_times)
            all_gaps.append((gap, has_focus))
    plain_gaps = [gap for gap, has_focus in all_gaps if not has_focus]
    download_from_records = len(plain_gaps) >= resolve_parameter(MINIMUM_GAPS).value
    download = (
        float(np.percentile(plain_gaps, resolve_parameter(DOWNLOAD_PERCENTILE).value))
        if download_from_records
        else resolve_parameter(DEFAULT_DOWNLOAD_SECONDS).value
    )
    dither_gaps = [
        gap - download
        for gap in plain_gaps
        if gap - download > resolve_parameter(DITHER_DETECTION_SECONDS).value
    ]
    focus_gaps = [gap - download for gap, has_focus in all_gaps if has_focus and gap - download > 0.0]
    dither_from_records = download_from_records and bool(dither_gaps)
    autofocus_from_records = download_from_records and bool(focus_gaps)
    return CaptureOverheads(
        download_seconds=download,
        dither_settle_seconds=float(np.mean(dither_gaps))
        if dither_from_records
        else resolve_parameter(DEFAULT_DITHER_SETTLE_SECONDS).value,
        autofocus_seconds=float(np.mean(focus_gaps))
        if autofocus_from_records
        else resolve_parameter(DEFAULT_AUTOFOCUS_SECONDS).value,
        download_from_records=download_from_records,
        dither_from_records=dither_from_records,
        autofocus_from_records=autofocus_from_records,
        gaps_used=len(all_gaps),
    )


def wall_clock_estimate(
    frame_count: int,
    exposure_s: float,
    overheads: CaptureOverheads,
    *,
    dither_every_frames: int | None = None,
    autofocus_runs: int = 0,
) -> WallClockEstimate:
    """Give the wall-clock time of a run of frames.

    Parameters
    ----------
    frame_count : `int`
        Number of frames. At least 0.
    exposure_s : `float`
        Exposure time of one frame, in seconds.
    overheads : `CaptureOverheads`
        The overheads (`estimate_capture_overheads`).
    dither_every_frames : `int` or `None`, optional
        Dither after every this many frames. ``None`` or 0 means no
        dithering.
    autofocus_runs : `int`, optional
        Autofocus runs during the run.

    Returns
    -------
    estimate : `WallClockEstimate`
        The parts and the total.
    """
    exposure_total = frame_count * exposure_s
    download_total = frame_count * overheads.download_seconds
    dither_count = math.floor(frame_count / dither_every_frames) if dither_every_frames else 0
    dither_total = dither_count * overheads.dither_settle_seconds
    focus_total = autofocus_runs * overheads.autofocus_seconds
    total = exposure_total + download_total + dither_total + focus_total
    fraction = (total - exposure_total) / exposure_total if exposure_total > 0.0 else 0.0
    return WallClockEstimate(
        exposure_s=exposure_total,
        download_seconds=download_total,
        dither_seconds=dither_total,
        autofocus_seconds=focus_total,
        total_seconds=total,
        overhead_fraction=fraction,
    )
