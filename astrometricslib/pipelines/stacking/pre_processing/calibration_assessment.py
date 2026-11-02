"""Judges whether a set of dark, bias or flat frames is good enough to use.

A set of calibration frames is built into one master frame, and the master is
subtracted from (or divided into) every light frame. A bad master harms every
light, and stacking does not average that harm away. This module judges a set
from the numbers stored for each of its frames (see
`pipelines/shared/quality/calibration_frame_measurement.py`) and from limits
that come from the equipment:

- The frame count: too few frames, a thin set, or copies of one file.
- The share of pixels at exactly zero (a frame cut off at zero has lost its
  noise).
- For a dark: light that reached the sensor (a large-scale pattern much
  stronger than the bias frames of the same setting show, or many saturated
  pixels), and a level far from the bias level of the same setting.
- For a bias: a level far from the one the camera profile stores for the
  setting.
- The noise against the read noise in the camera profile.

Flats keep their own check (`flat_calibration.assess_flats`); this module only
turns its answer into the same records.

Nothing judged is stored. The functions here are pure: they take the stored
numbers and the limits and return `GateResult` records, and the set's health
is worked out again whenever it is asked for, so a changed limit shows at
once. The limits that depend on the sensor are written in value steps (the
smallest step between two pixel values the sensor writes), so no camera needs
a number of its own. The limits that need a reference say where it comes from,
and a check with no reference is ``not_checked`` with the reason. It never
uses a default.
"""

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from astrometricslib.models.calibration_ingest import CalibrationSetHealth, FlatSetAssessment, FrameConcern
from astrometricslib.models.camera_profile import CameraProfile
from astrometricslib.models.quality.gate_result import (
    GateResult,
    GateStatus,
)
from astrometricslib.pipelines.shared.quality.metrics import (
    FRAME_BLOCK_RANGE,
    FRAME_CLIPPED_MEAN_LEVEL,
    FRAME_NEIGHBOR_NOISE,
    FRAME_PIXEL_CHECKSUM,
    FRAME_SATURATED_PIXEL_FRACTION,
    FRAME_VALUE_STEP,
    FRAME_ZERO_PIXEL_FRACTION,
)

__all__ = [
    "BIAS_LEVEL_GATE_NAME",
    "BIAS_NOISE_GATE_NAME",
    "BIAS_ZERO_PIXEL_GATE_NAME",
    "CALIBRATION_FRAME_COUNT_CHECK_NAME",
    "DARK_CEILING_SHARE_CHECK_NAME",
    "DARK_LEVEL_GATE_NAME",
    "DARK_LIGHT_LEAK_GATE_NAME",
    "DARK_NOISE_GATE_NAME",
    "DARK_ZERO_PIXEL_GATE_NAME",
    "BiasReference",
    "FrameFacts",
    "SetJudgement",
    "assess_dark_or_bias_frames",
    "build_bias_reference",
    "check_failed",
    "check_not_checked",
    "check_passed",
    "combine_status",
    "flat_set_checks",
    "frame_facts_from_rows",
    "health_of_judgement",
    "summarize_checks",
]

# The names of the checks. They are the names of the stack's gates (the
# constants ending in ``GATE_NAME`` are the ones the gate documentation and the
# gate conformance test find), so the set's health and the stack's gates use
# one vocabulary. The frame count check is the stack's existing
# ``calibration_frame_count`` gate, whose constant lives in
# `assess_input_quality.py`.
CALIBRATION_FRAME_COUNT_CHECK_NAME = "calibration_frame_count"
DARK_ZERO_PIXEL_GATE_NAME = "dark_zero_pixel_fraction"
BIAS_ZERO_PIXEL_GATE_NAME = "bias_zero_pixel_fraction"
DARK_LEVEL_GATE_NAME = "dark_level"
BIAS_LEVEL_GATE_NAME = "bias_level"
DARK_NOISE_GATE_NAME = "dark_noise"
BIAS_NOISE_GATE_NAME = "bias_noise"
DARK_LIGHT_LEAK_GATE_NAME = "dark_light_leak"
# Set health only; the stack has no gate for it (the light leak gate covers
# light reaching the sensor by its large-scale pattern).
DARK_CEILING_SHARE_CHECK_NAME = "dark_ceiling_share"

# A set of fewer unique frames than this is thin. A master of N frames adds
# the noise of one frame divided by the square root of N to every light, which
# in the worst case (read noise dominates) is a share 1/N of the noise
# variance: 10 percent at N = 10. A design estimate, not a measured limit
# (register entry STK-53, decision D9 of issue 134). The existing minimum of
# the configuration (default 3) stays the limit for "too few".
THIN_SET_FRAME_COUNT = 10

# A frame with this share of pixels at exactly zero (or more) is cut off at
# zero. The 2,367 frames of the validation library have either no zero pixel or
# at least 72 percent, so any limit in between separates them; 0.1 percent is
# the figure the offset test of 2026-10-02 used (register entry STK-47).
ZERO_PIXEL_FRACTION_LIMIT = 0.001

# A dark with this share of pixels at or above the camera's saturation level
# (or more) was struck by light. The one such dark in the library had 35
# percent; the largest share among the others was 8.3e-5 (register entry
# STK-52).
CEILING_SHARE_LIMIT = 0.001

# Level, light leak and bias limits are written in value steps. Two steps is
# the distance within which the validation library's clean frames all sat
# (largest clean difference: 3.15 ADU on the Nikon, 0.57 ADU on the ASI 533,
# against steps of 2 and 4 ADU; register entries STK-48 to STK-51).
VALUE_STEP_ALLOWANCE = 2.0

# The noise of a frame must lie within this band around the read noise in the
# camera profile. The observed ratio was 0.99 to 1.00 at ASI 533 offsets 10 to
# 50; the width is a design choice (register entry STK-49).
NOISE_RATIO_LOW = 0.9
NOISE_RATIO_HIGH = 1.1

# The dark current added to the allowed level difference, in ADU per second of
# exposure. Measured on the ASI 533 at -10 C as 0.0020 ADU per second (register
# entry STK-54), used for every camera: the term is below one value step for
# any exposure under about 2,000 s, so a wrong figure moves no verdict.
DARK_CURRENT_ALLOWANCE_ADU_PER_SECOND = 0.002

# The most frames of concern one set lists. A set of copies can hold hundreds.
MAXIMUM_FRAMES_OF_CONCERN = 25

_ZERO_SOURCE = (
    "0.1% of pixels at zero (validation register STK-47; separation measured, the exact figure is a "
    "design estimate)"
)
_CEILING_SOURCE = "0.1% of pixels at the saturation level (validation register STK-52, partly measured)"
_LEAK_SOURCE = (
    "bias block range at the same setting plus 2 value steps (validation register STK-50, partly measured)"
)
_DARK_LEVEL_SOURCE = (
    "bias level at the same setting plus or minus 2 value steps and the dark current (validation register "
    "STK-51, partly measured)"
)
_BIAS_LEVEL_SOURCE = (
    "camera profile bias level plus or minus 2 value steps (validation register STK-48, partly measured)"
)
_NOISE_SOURCE = "camera profile read noise, 0.9 to 1.1 times (validation register STK-49, partly measured)"
_DARK_NOISE_SOURCE = (
    "camera profile read noise, at least 0.9 times (validation register STK-49, partly measured)"
)
_COUNT_SOURCE = "configured minimum of frames; thin below 10 frames (validation register STK-53)"


def check_passed(
    name: str,
    measured_value: float | None = None,
    limit: float | None = None,
    limit_source: str | None = None,
) -> GateResult:
    """Build the record of a check that ran and passed.

    The record is built directly, not with ``passed_gate``: these are the
    checks of one set, and the stack's gates are made from them by name (see
    `stack_calibration_health.py`), so the name is a parameter here.

    Parameters
    ----------
    name : `str`
        The check's name.
    measured_value, limit : `float` or `None`, optional
        What was measured and the limit it was compared with.
    limit_source : `str` or `None`, optional
        Where the limit came from.

    Returns
    -------
    result : `GateResult`
        A result with status ``passed``.
    """
    return GateResult(
        name=name,
        status=GateStatus.PASSED,
        measuredValue=measured_value,
        limit=limit,
        limitSource=limit_source,
    )


def check_failed(
    name: str,
    detail: str,
    measured_value: float | None = None,
    limit: float | None = None,
    limit_source: str | None = None,
) -> GateResult:
    """Build the record of a check that ran and failed.

    Parameters
    ----------
    name : `str`
        The check's name.
    detail : `str`
        One sentence saying what is wrong.
    measured_value, limit : `float` or `None`, optional
        What was measured and the limit it was compared with.
    limit_source : `str` or `None`, optional
        Where the limit came from.

    Returns
    -------
    result : `GateResult`
        A result with status ``failed``.
    """
    return GateResult(
        name=name,
        status=GateStatus.FAILED,
        measuredValue=measured_value,
        limit=limit,
        limitSource=limit_source,
        detail=detail,
    )


def check_not_checked(name: str, reason: str, limit_source: str | None = None) -> GateResult:
    """Build the record of a check that could not run.

    Parameters
    ----------
    name : `str`
        The check's name.
    reason : `str`
        One sentence saying why the check could not run.
    limit_source : `str` or `None`, optional
        Where the limit comes from, kept so a reader sees what would have
        been checked.

    Returns
    -------
    result : `GateResult`
        A result with status ``not_checked``.
    """
    return GateResult(name=name, status=GateStatus.NOT_CHECKED, limitSource=limit_source, detail=reason)


@dataclass(frozen=True)
class FrameFacts:
    """The stored numbers of one calibration frame.

    Attributes
    ----------
    path : `str`
        The frame's file path.
    values : `Mapping` [`str`, `float`]
        The stored value of each calibration metric the frame has a value for.
        A metric the frame is ``not_measured`` for is absent.
    """

    path: str
    values: Mapping[str, float]

    @property
    def is_measured(self) -> bool:
        """Say whether any number is stored for the frame.

        Returns
        -------
        is_measured : `bool`
            `True` when the frame has a level (the first metric measured).
        """
        return FRAME_CLIPPED_MEAN_LEVEL in self.values


def frame_facts_from_rows(rows: Sequence, subject_paths: Mapping[str, str]) -> dict[str, FrameFacts]:
    """Gather stored measurement rows into one record per frame.

    Parameters
    ----------
    rows : `Sequence` [`QualityMeasurement`]
        Stored rows of calibration frames. Only the newest row of each frame
        and metric should be given (the store's latest view).
    subject_paths : `Mapping` [`str`, `str`]
        The file path of each subject id, for the frames wanted.

    Returns
    -------
    facts : `dict` [`str`, `FrameFacts`]
        By file path, for the frames in `subject_paths` that have any row. A
        row with no value is left out of its frame's values.
    """
    values_by_path: dict[str, dict[str, float]] = {}
    for row in rows:
        path = subject_paths.get(row.subject.subject_id)
        if path is None:
            continue
        slot = values_by_path.setdefault(path, {})
        if row.value is not None:
            slot[row.metric] = row.value
    return {path: FrameFacts(path, values) for path, values in values_by_path.items()}


@dataclass(frozen=True)
class BiasReference:
    """What the bias frames of one setting say about the sensor.

    Attributes
    ----------
    level_adu : `float`
        The median of the bias frames' mean levels.
    block_range_adu : `float` or `None`
        The median of their block ranges.
    value_step_adu : `float` or `None`
        The median of their value steps.
    frame_count : `int`
        The measured bias frames behind the numbers.
    """

    level_adu: float
    block_range_adu: float | None
    value_step_adu: float | None
    frame_count: int


def build_bias_reference(bias_facts: Sequence[FrameFacts]) -> BiasReference | None:
    """Summarize the measured bias frames of one setting.

    Parameters
    ----------
    bias_facts : `Sequence` [`FrameFacts`]
        The stored numbers of the bias frames at the setting.

    Returns
    -------
    reference : `BiasReference` or `None`
        The medians, or `None` when no bias frame has a stored level.
    """
    measured = [facts for facts in bias_facts if facts.is_measured]
    if not measured:
        return None

    def median_of(metric: str) -> float | None:
        """Take the median of a metric over the frames that have it.

        Returns
        -------
        median : `float` or `None`
            The median, or `None` if no frame has the metric.
        """
        values = [facts.values[metric] for facts in measured if metric in facts.values]
        return statistics.median(values) if values else None

    return BiasReference(
        level_adu=statistics.median(facts.values[FRAME_CLIPPED_MEAN_LEVEL] for facts in measured),
        block_range_adu=median_of(FRAME_BLOCK_RANGE),
        value_step_adu=median_of(FRAME_VALUE_STEP),
        frame_count=len(measured),
    )


@dataclass(frozen=True)
class SetJudgement:
    """What the checks found on one set of dark or bias frames.

    Attributes
    ----------
    checks : `list` [`GateResult`]
        One record per check.
    concerns : `list` [`FrameConcern`]
        The frames singled out, at most `MAXIMUM_FRAMES_OF_CONCERN`.
    measurements : `dict` [`str`, `float` or `None`]
        The numbers behind the checks.
    frame_count : `int`
        The files in the set.
    unique_frame_count : `int`
        The files with different pixels (files not measured count as unique).
    measured_frame_count : `int`
        The files with stored numbers.
    """

    checks: list[GateResult]
    concerns: list[FrameConcern]
    measurements: dict[str, float | None]
    frame_count: int
    unique_frame_count: int
    measured_frame_count: int


def combine_status(checks: Sequence[GateResult]) -> str:
    """Give the status of a set from its checks.

    Parameters
    ----------
    checks : `Sequence` [`GateResult`]
        The set's checks.

    Returns
    -------
    status : `str`
        ``"warning"`` when a check failed, ``"good"`` when at least one passed
        and none failed, else ``"not_checked"``.
    """
    if any(check.status is GateStatus.FAILED for check in checks):
        return "warning"
    if any(check.status is GateStatus.PASSED for check in checks):
        return "good"
    return "not_checked"


def summarize_checks(checks: Sequence[GateResult], measured_note: str = "") -> str:
    """Write the one sentence that says how a set is.

    Parameters
    ----------
    checks : `Sequence` [`GateResult`]
        The set's checks.
    measured_note : `str`, optional
        A clause to add, such as how many frames are still unmeasured.

    Returns
    -------
    summary : `str`
        The first failed check's sentence; else that no problem was found;
        else the reason of the first check that did not run.
    """
    failed = [check for check in checks if check.status is GateStatus.FAILED]
    if failed:
        text = failed[0].detail
        if len(failed) > 1:
            text += f" ({len(failed) - 1} more problem(s) found)"
    elif any(check.status is GateStatus.PASSED for check in checks):
        text = "No problem found."
    else:
        text = next((check.detail for check in checks if check.detail), "Nothing could be checked.")
    return f"{text} {measured_note}".strip()


def _concerns(items: Sequence[tuple[str, str]]) -> list[FrameConcern]:
    """Turn (path, reason) pairs into frame concerns, capped in length.

    Returns
    -------
    concerns : `list` [`FrameConcern`]
        At most `MAXIMUM_FRAMES_OF_CONCERN`, in the order given.
    """
    reasons_by_path: dict[str, list[str]] = {}
    for path, reason in items:
        reasons_by_path.setdefault(path, []).append(reason)
    return [
        FrameConcern(path=path, reason="; ".join(reasons))
        for path, reasons in list(reasons_by_path.items())[:MAXIMUM_FRAMES_OF_CONCERN]
    ]


def _count_check(
    paths: Sequence[str],
    facts_by_path: Mapping[str, FrameFacts],
    minimum_frames: int,
    concerns: list[tuple[str, str]],
) -> tuple[GateResult, int]:
    """Judge the frame count of a set, counting copies once.

    Parameters
    ----------
    paths : `Sequence` [`str`]
        The set's file paths.
    facts_by_path : `Mapping` [`str`, `FrameFacts`]
        The stored numbers, by path.
    minimum_frames : `int`
        The configured minimum number of frames for a master.
    concerns : `list`
        Receives a (path, reason) pair for each copy after the first.

    Returns
    -------
    check : `GateResult`
        Failed when there are fewer unique frames than `minimum_frames`, fewer
        than `THIN_SET_FRAME_COUNT`, or any copy; else passed.
    unique_count : `int`
        The frames with different pixels. A frame with no stored checksum
        counts as unique.
    """
    seen: dict[float, str] = {}
    unique = 0
    copies: list[str] = []
    for path in paths:
        checksum = facts_by_path[path].values.get(FRAME_PIXEL_CHECKSUM) if path in facts_by_path else None
        if checksum is None:
            unique += 1
        elif checksum in seen:
            copies.append(path)
        else:
            seen[checksum] = path
            unique += 1
    concerns.extend((path, "has the same pixels as another frame of the set") for path in copies)
    if unique < minimum_frames:
        detail = (
            f"too few frames: {unique} unique frame(s), fewer than the minimum of {minimum_frames}, so the "
            "master is built from almost no data"
        )
        failed = True
    elif unique < THIN_SET_FRAME_COUNT:
        detail = (
            f"thin set: {unique} unique frame(s), fewer than {THIN_SET_FRAME_COUNT}, so the master's own "
            f"noise adds up to {1 / unique:.0%} of the noise variance to every light"
        )
        failed = True
    elif copies:
        detail = (
            f"{len(copies)} of {len(paths)} files are copies of another file in the set, so the set holds "
            f"only {unique} different frames"
        )
        failed = True
    else:
        detail = ""
        failed = False
    if failed:
        return (
            check_failed(
                CALIBRATION_FRAME_COUNT_CHECK_NAME,
                detail,
                float(unique),
                float(THIN_SET_FRAME_COUNT),
                _COUNT_SOURCE,
            ),
            unique,
        )
    return (
        check_passed(
            CALIBRATION_FRAME_COUNT_CHECK_NAME, float(unique), float(THIN_SET_FRAME_COUNT), _COUNT_SOURCE
        ),
        unique,
    )


def _zero_check(
    name: str, kind: str, measured: Sequence[FrameFacts], concerns: list[tuple[str, str]]
) -> GateResult:
    """Judge the share of pixels at exactly zero.

    Parameters
    ----------
    name : `str`
        The check's name.
    kind : `str`
        ``"dark"`` or ``"bias"``, for the sentence.
    measured : `Sequence` [`FrameFacts`]
        The frames with stored numbers.
    concerns : `list`
        Receives a (path, reason) pair for each frame at or over the limit.

    Returns
    -------
    check : `GateResult`
        Failed when any frame has `ZERO_PIXEL_FRACTION_LIMIT` or more of its
        pixels at zero. The value is the worst frame's share.
    """
    shares = [
        (facts.values[FRAME_ZERO_PIXEL_FRACTION], facts.path)
        for facts in measured
        if FRAME_ZERO_PIXEL_FRACTION in facts.values
    ]
    if not shares:
        return check_not_checked(name, f"no {kind} frame has a stored zero share yet", _ZERO_SOURCE)
    worst = max(share for share, _ in shares)
    over = [
        (path, f"{share:.0%} of its pixels are exactly zero")
        for share, path in shares
        if share >= ZERO_PIXEL_FRACTION_LIMIT
    ]
    if over:
        concerns.extend(over)
        return check_failed(
            name,
            f"{kind} frames are cut off at zero: {len(over)} of {len(shares)} frame(s) have "
            f"up to {worst:.0%} of their pixels at exactly zero, so their noise is lost and the master "
            "is wrong; use a camera offset above 0",
            worst,
            ZERO_PIXEL_FRACTION_LIMIT,
            _ZERO_SOURCE,
        )
    return check_passed(name, worst, ZERO_PIXEL_FRACTION_LIMIT, _ZERO_SOURCE)


def _read_noise_adu(
    profile: CameraProfile | None, gain: object, offset: object, binning: object, clipped_at_zero: bool
) -> tuple[float | None, str]:
    """Find the read noise in ADU that the camera profile gives for a setting.

    Parameters
    ----------
    profile : `CameraProfile` or `None`
        The camera's profile.
    gain, offset, binning : `Any`
        The set's setting.
    clipped_at_zero : `bool`
        Whether the set's frames are cut off at zero. Only a set that is not
        may use the entry of another offset (the offset does not change the
        read noise).

    Returns
    -------
    read_noise_adu : `float` or `None`
        The read noise divided by the gain, or `None`.
    reason : `str`
        Why there is none, empty when there is one.
    """
    if profile is None:
        return None, "there is no camera profile for this camera"
    entry = profile.sensor_noise_for(
        gain, offset, binning, clipped_at_zero=False if not clipped_at_zero else None
    )
    if entry is None or entry.read_noise_e is None or entry.gain_e_per_adu is None:
        return None, f"the camera profile has no gain and read noise for gain {gain}, offset {offset}"
    return entry.read_noise_e.value / entry.gain_e_per_adu.value, ""


def _noise_check(
    name: str,
    measured: Sequence[FrameFacts],
    read_noise_adu: float | None,
    reason: str,
    upper_limit: bool,
) -> GateResult:
    """Compare the frames' noise with the camera profile's read noise.

    Parameters
    ----------
    name : `str`
        The check's name.
    measured : `Sequence` [`FrameFacts`]
        The frames with stored numbers.
    read_noise_adu : `float` or `None`
        The profile's read noise in ADU.
    reason : `str`
        Why there is none.
    upper_limit : `bool`
        `True` for a bias (noise above the band fails too). A dark's noise
        grows with hot pixels, so only a low noise fails.

    Returns
    -------
    check : `GateResult`
        The value is the median frame noise divided by the read noise.
    """
    source = _NOISE_SOURCE if upper_limit else _DARK_NOISE_SOURCE
    if read_noise_adu is None:
        return check_not_checked(name, reason, source)
    noises = [
        facts.values[FRAME_NEIGHBOR_NOISE] for facts in measured if FRAME_NEIGHBOR_NOISE in facts.values
    ]
    if not noises:
        return check_not_checked(name, "no frame has a stored noise yet", source)
    ratio = statistics.median(noises) / read_noise_adu
    if ratio < NOISE_RATIO_LOW:
        return check_failed(
            name,
            f"the noise is {ratio:.2f} times the camera's read noise, below {NOISE_RATIO_LOW}: "
            "the frames are "
            "probably cut off at zero or taken at another gain",
            ratio,
            NOISE_RATIO_LOW,
            source,
        )
    if upper_limit and ratio > NOISE_RATIO_HIGH:
        return check_failed(
            name,
            f"the noise is {ratio:.2f} times the camera's read noise, above {NOISE_RATIO_HIGH}: the frames "
            "may be taken at another gain, or the sensor has a fault",
            ratio,
            NOISE_RATIO_HIGH,
            source,
        )
    return check_passed(name, ratio, NOISE_RATIO_HIGH if upper_limit else NOISE_RATIO_LOW, source)


def _median_value_step(frames: Sequence[FrameFacts], reference: BiasReference | None) -> float | None:
    """Pick the value step to write limits in.

    Returns
    -------
    value_step_adu : `float` or `None`
        The bias frames' step if known, else the median of the given frames'.
    """
    if reference is not None and reference.value_step_adu:
        return reference.value_step_adu
    steps = [facts.values[FRAME_VALUE_STEP] for facts in frames if facts.values.get(FRAME_VALUE_STEP)]
    return statistics.median(steps) if steps else None


def _dark_checks(
    measured: Sequence[FrameFacts],
    exposure_seconds: float | None,
    reference: BiasReference | None,
    profile: CameraProfile | None,
    setting: tuple[object, object, object],
    concerns: list[tuple[str, str]],
    measurements: dict[str, float | None],
) -> list[GateResult]:
    """Judge the checks that only darks have.

    Parameters
    ----------
    measured : `Sequence` [`FrameFacts`]
        The dark frames with stored numbers.
    exposure_seconds : `float` or `None`
        The set's exposure, for the dark current allowance.
    reference : `BiasReference` or `None`
        The bias frames of the same setting.
    profile : `CameraProfile` or `None`
        The camera's profile.
    setting : `tuple`
        The gain, offset and binning of the set.
    concerns : `list`
        Receives a (path, reason) pair for each frame a check singled out.
    measurements : `dict`
        Receives the numbers behind the checks.

    Returns
    -------
    checks : `list` [`GateResult`]
        The zero, light leak, ceiling, level and noise checks.
    """
    checks = [_zero_check(DARK_ZERO_PIXEL_GATE_NAME, "dark", measured, concerns)]
    step = _median_value_step(measured, reference)
    no_bias = "there are no measured bias frames at this setting to compare with"

    # Light that reached the sensor: a large-scale pattern or saturated pixels.
    ranges = [(f.values[FRAME_BLOCK_RANGE], f.path) for f in measured if FRAME_BLOCK_RANGE in f.values]
    if reference is None or reference.block_range_adu is None:
        checks.append(check_not_checked(DARK_LIGHT_LEAK_GATE_NAME, no_bias, _LEAK_SOURCE))
    elif not step or not ranges:
        checks.append(
            check_not_checked(
                DARK_LIGHT_LEAK_GATE_NAME, "the value step of the sensor is not known yet", _LEAK_SOURCE
            )
        )
    else:
        excesses = [((value - reference.block_range_adu) / step, path, value) for value, path in ranges]
        worst = max(excess for excess, _, _ in excesses)
        measurements["block_range_adu"] = max(value for value, _ in ranges)
        measurements["bias_block_range_adu"] = reference.block_range_adu
        struck = [
            (
                path,
                f"large-scale pattern {value:.0f} ADU across the frame, bias frames show "
                f"{reference.block_range_adu:.0f} ADU",
            )
            for excess, path, value in excesses
            if excess > VALUE_STEP_ALLOWANCE
        ]
        if struck:
            concerns.extend(struck)
            checks.append(
                check_failed(
                    DARK_LIGHT_LEAK_GATE_NAME,
                    f"light probably reached the sensor in {len(struck)} of {len(ranges)} dark frame(s): the "
                    f"large-scale pattern is up to {worst:.1f} value steps above the bias frames' "
                    f"({VALUE_STEP_ALLOWANCE:g} steps allowed). The master subtracts that pattern from "
                    "every light",
                    worst,
                    VALUE_STEP_ALLOWANCE,
                    _LEAK_SOURCE,
                )
            )
        else:
            checks.append(check_passed(DARK_LIGHT_LEAK_GATE_NAME, worst, VALUE_STEP_ALLOWANCE, _LEAK_SOURCE))

    ceilings = [
        (f.values[FRAME_SATURATED_PIXEL_FRACTION], f.path)
        for f in measured
        if FRAME_SATURATED_PIXEL_FRACTION in f.values
    ]
    if not ceilings:
        checks.append(
            check_not_checked(
                DARK_CEILING_SHARE_CHECK_NAME, "no saturated share is stored yet", _CEILING_SOURCE
            )
        )
    else:
        worst_share = max(share for share, _ in ceilings)
        measurements["saturated_pixel_fraction_max"] = worst_share
        bright = [
            (path, f"{share:.0%} of its pixels are at the saturation level")
            for share, path in ceilings
            if share >= CEILING_SHARE_LIMIT
        ]
        if bright:
            concerns.extend(bright)
            checks.append(
                check_failed(
                    DARK_CEILING_SHARE_CHECK_NAME,
                    f"{len(bright)} dark frame(s) have up to {worst_share:.0%} of their pixels at the "
                    "saturation "
                    "level: light reached the sensor",
                    worst_share,
                    CEILING_SHARE_LIMIT,
                    _CEILING_SOURCE,
                )
            )
        else:
            checks.append(
                check_passed(DARK_CEILING_SHARE_CHECK_NAME, worst_share, CEILING_SHARE_LIMIT, _CEILING_SOURCE)
            )

    # Level against the bias level of the same setting.
    levels = [(f.values[FRAME_CLIPPED_MEAN_LEVEL], f.path) for f in measured]
    if reference is None:
        checks.append(check_not_checked(DARK_LEVEL_GATE_NAME, no_bias, _DARK_LEVEL_SOURCE))
    elif not step or not levels:
        checks.append(
            check_not_checked(
                DARK_LEVEL_GATE_NAME, "the value step of the sensor is not known yet", _DARK_LEVEL_SOURCE
            )
        )
    else:
        allowance = DARK_CURRENT_ALLOWANCE_ADU_PER_SECOND * (exposure_seconds or 0.0)
        excesses = [
            (max(abs(level - reference.level_adu) - allowance, 0.0) / step, path, level)
            for level, path in levels
        ]
        worst = max(excess for excess, _, _ in excesses)
        measurements["mean_level_adu"] = statistics.median(level for level, _ in levels)
        measurements["bias_level_adu"] = reference.level_adu
        off = [
            (path, f"mean level {level:.1f} ADU, bias level is {reference.level_adu:.1f} ADU")
            for excess, path, level in excesses
            if excess > VALUE_STEP_ALLOWANCE
        ]
        if off:
            concerns.extend(off)
            checks.append(
                check_failed(
                    DARK_LEVEL_GATE_NAME,
                    f"{len(off)} of {len(levels)} dark frame(s) sit up to {worst:.1f} value steps from "
                    "the bias "
                    f"level of the same setting ({VALUE_STEP_ALLOWANCE:g} steps allowed): a wrong level "
                    "means a "
                    "wrong subtraction, and a master that is already calibrated reads near zero",
                    worst,
                    VALUE_STEP_ALLOWANCE,
                    _DARK_LEVEL_SOURCE,
                )
            )
        else:
            checks.append(check_passed(DARK_LEVEL_GATE_NAME, worst, VALUE_STEP_ALLOWANCE, _DARK_LEVEL_SOURCE))

    zero_clipped = any(
        f.values.get(FRAME_ZERO_PIXEL_FRACTION, 0.0) >= ZERO_PIXEL_FRACTION_LIMIT for f in measured
    )
    read_noise_adu, reason = _read_noise_adu(profile, *setting, zero_clipped)
    checks.append(_noise_check(DARK_NOISE_GATE_NAME, measured, read_noise_adu, reason, upper_limit=False))
    return checks


def _bias_checks(
    measured: Sequence[FrameFacts],
    profile: CameraProfile | None,
    setting: tuple[object, object, object],
    concerns: list[tuple[str, str]],
    measurements: dict[str, float | None],
) -> list[GateResult]:
    """Judge the checks that only biases have.

    Parameters
    ----------
    measured : `Sequence` [`FrameFacts`]
        The bias frames with stored numbers.
    profile : `CameraProfile` or `None`
        The camera's profile, for the bias level and the read noise.
    setting : `tuple`
        The gain, offset and binning of the set.
    concerns : `list`
        Receives a (path, reason) pair for each frame a check singled out.
    measurements : `dict`
        Receives the numbers behind the checks.

    Returns
    -------
    checks : `list` [`GateResult`]
        The zero, level and noise checks.
    """
    checks = [_zero_check(BIAS_ZERO_PIXEL_GATE_NAME, "bias", measured, concerns)]
    stored = None if profile is None else profile.bias_level_for(*setting)
    levels = [f.values[FRAME_CLIPPED_MEAN_LEVEL] for f in measured]
    steps = [f.values[FRAME_VALUE_STEP] for f in measured if f.values.get(FRAME_VALUE_STEP)]
    if levels:
        measurements["mean_level_adu"] = statistics.median(levels)
    if stored is None:
        checks.append(
            check_not_checked(
                BIAS_LEVEL_GATE_NAME,
                f"the camera profile has no bias level for gain {setting[0]}, offset {setting[1]}",
                _BIAS_LEVEL_SOURCE,
            )
        )
    elif not levels or not steps:
        checks.append(
            check_not_checked(
                BIAS_LEVEL_GATE_NAME, "no level or value step is stored yet", _BIAS_LEVEL_SOURCE
            )
        )
    else:
        expected = stored.bias_level_adu.value
        step = statistics.median(steps)
        excess = abs(statistics.median(levels) - expected) / step
        measurements["profile_bias_level_adu"] = expected
        if excess > VALUE_STEP_ALLOWANCE:
            checks.append(
                check_failed(
                    BIAS_LEVEL_GATE_NAME,
                    f"the bias level is {statistics.median(levels):.1f} ADU, {excess:.1f} value steps "
                    "from the "
                    f"{expected:.1f} ADU the camera profile stores ({VALUE_STEP_ALLOWANCE:g} steps "
                    "allowed): the "
                    "frames may be taken at another offset, or already calibrated",
                    excess,
                    VALUE_STEP_ALLOWANCE,
                    _BIAS_LEVEL_SOURCE,
                )
            )
        else:
            checks.append(
                check_passed(BIAS_LEVEL_GATE_NAME, excess, VALUE_STEP_ALLOWANCE, _BIAS_LEVEL_SOURCE)
            )
    zero_clipped = any(
        f.values.get(FRAME_ZERO_PIXEL_FRACTION, 0.0) >= ZERO_PIXEL_FRACTION_LIMIT for f in measured
    )
    read_noise_adu, reason = _read_noise_adu(profile, *setting, zero_clipped)
    checks.append(_noise_check(BIAS_NOISE_GATE_NAME, measured, read_noise_adu, reason, upper_limit=True))
    return checks


def assess_dark_or_bias_frames(
    kind: str,
    paths: Sequence[str],
    facts_by_path: Mapping[str, FrameFacts],
    *,
    minimum_frames: int,
    profile: CameraProfile | None,
    setting: tuple[object, object, object],
    exposure_seconds: float | None = None,
    reference: BiasReference | None = None,
) -> SetJudgement:
    """Judge a set of dark or bias frames from their stored numbers.

    Parameters
    ----------
    kind : `str`
        ``"dark"`` or ``"bias"``.
    paths : `Sequence` [`str`]
        The frames to judge: a library set, or the frames a stack used.
    facts_by_path : `Mapping` [`str`, `FrameFacts`]
        The stored numbers, by path.
    minimum_frames : `int`
        The configured minimum number of frames for a master.
    profile : `CameraProfile` or `None`
        The camera's profile.
    setting : `tuple`
        The gain, offset and binning of the set.
    exposure_seconds : `float` or `None`, optional
        A dark's exposure.
    reference : `BiasReference` or `None`, optional
        For a dark, the bias frames of the same setting. Without it the light
        leak and level checks are ``not_checked``.

    Returns
    -------
    judgement : `SetJudgement`
        The checks, the frames of concern and the numbers behind them. When no
        frame has stored numbers every check except the count is
        ``not_checked`` and says so.
    """
    concerns: list[tuple[str, str]] = []
    measurements: dict[str, float | None] = {}
    count_check, unique_count = _count_check(paths, facts_by_path, minimum_frames, concerns)
    measured = [
        facts_by_path[path] for path in paths if path in facts_by_path and facts_by_path[path].is_measured
    ]
    names = (
        (
            DARK_ZERO_PIXEL_GATE_NAME,
            DARK_LIGHT_LEAK_GATE_NAME,
            DARK_CEILING_SHARE_CHECK_NAME,
            DARK_LEVEL_GATE_NAME,
            DARK_NOISE_GATE_NAME,
        )
        if kind == "dark"
        else (BIAS_ZERO_PIXEL_GATE_NAME, BIAS_LEVEL_GATE_NAME, BIAS_NOISE_GATE_NAME)
    )
    if not measured:
        reason = f"none of the {len(paths)} {kind} frame(s) has been measured yet; start the measuring job"
        checks = [count_check, *(check_not_checked(name, reason) for name in names)]
    elif kind == "dark":
        checks = [
            count_check,
            *_dark_checks(measured, exposure_seconds, reference, profile, setting, concerns, measurements),
        ]
    else:
        checks = [count_check, *_bias_checks(measured, profile, setting, concerns, measurements)]
    steps = [f.values[FRAME_VALUE_STEP] for f in measured if f.values.get(FRAME_VALUE_STEP)]
    if steps:
        measurements["value_step_adu"] = statistics.median(steps)
    noises = [f.values[FRAME_NEIGHBOR_NOISE] for f in measured if FRAME_NEIGHBOR_NOISE in f.values]
    if noises:
        measurements["noise_adu"] = statistics.median(noises)
    return SetJudgement(
        checks=checks,
        concerns=_concerns(concerns),
        measurements=measurements,
        frame_count=len(paths),
        unique_frame_count=unique_count,
        measured_frame_count=len(measured),
    )


def health_of_judgement(
    judgement: SetJudgement,
    *,
    kind: str,
    row_key: str,
    camera: str,
    gain: str,
    offset: float,
    binning: str | None,
    temperature_slot_c: float | None = None,
    exposure_seconds: float | None = None,
) -> CalibrationSetHealth:
    """Put a judgement and the set's identity into the common result model.

    Parameters
    ----------
    judgement : `SetJudgement`
        What the checks found.
    kind, row_key, camera, gain, offset, binning
        The set's identity. `row_key` is the one `CalibrationCatalog.query`
        gives it.
    temperature_slot_c, exposure_seconds : `float` or `None`, optional
        A dark's temperature slot and exposure.

    Returns
    -------
    health : `CalibrationSetHealth`
        The set's health, with the status and the one-sentence summary.
    """
    unmeasured = judgement.frame_count - judgement.measured_frame_count
    note = f"{unmeasured} of {judgement.frame_count} frame(s) are not measured yet." if unmeasured else ""
    return CalibrationSetHealth(
        kind=kind,
        rowKey=row_key,
        camera=camera,
        gain=gain,
        offset=offset,
        binning=binning,
        temperatureSlotC=temperature_slot_c,
        exposureSeconds=exposure_seconds,
        frameCount=judgement.frame_count,
        uniqueFrameCount=judgement.unique_frame_count,
        measuredFrameCount=judgement.measured_frame_count,
        status=combine_status(judgement.checks),
        summary=summarize_checks(judgement.checks, note),
        checks=judgement.checks,
        framesOfConcern=judgement.concerns,
        measurements=judgement.measurements,
    )


def flat_set_checks(assessment: FlatSetAssessment, minimum_frames: int) -> list[GateResult]:
    """Turn a flat set's assessment into the same records the other kinds use.

    The flat measurement and its limits are those of
    `flat_calibration.assess_flats`; the level and noise decisions are made by
    the stack's own `calibration_gates`, so a flat set and a stack that used
    it always agree.

    Parameters
    ----------
    assessment : `FlatSetAssessment`
        What `assess_flat_group` measured.
    minimum_frames : `int`
        The configured minimum number of frames for a master.

    Returns
    -------
    checks : `list` [`GateResult`]
        The ``flat_level`` and ``flat_noise`` checks, then the frame count.
    """
    from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import calibration_gates

    diagnostics = {
        "flat_calibration": {
            "frame_count": assessment.frame_count,
            "level_fraction": assessment.level_fraction,
            "noise_fraction": assessment.noise_fraction,
            "smoothing_sigma_pixels": assessment.smoothing_sigma_pixels,
            "issues": list(assessment.issues),
        }
    }
    level_and_noise = [
        gate for gate in calibration_gates(diagnostics) if gate.name in ("flat_level", "flat_noise")
    ]
    if assessment.frame_count < minimum_frames:
        count = check_failed(
            CALIBRATION_FRAME_COUNT_CHECK_NAME,
            f"too few flat frames: {assessment.frame_count}, fewer than the minimum of {minimum_frames}",
            float(assessment.frame_count),
            float(minimum_frames),
            "configured minimum of frames",
        )
    else:
        count = check_passed(
            CALIBRATION_FRAME_COUNT_CHECK_NAME,
            float(assessment.frame_count),
            float(minimum_frames),
            "configured minimum of frames",
        )
    return [*level_and_noise, count]
