"""Purpose: Guiding and Input-Data Quality Analysis.

Description: Turns the per-frame facts already recorded on `FrameRecord`
into statements about the *acquisition* rather than the processing --
mount tracking drift, periodic error, trailing, focus drift, and sky
conditions -- plus a check on whether a pipeline's frame rejection
actually tracked frame quality.

These are read-only analyses over data other stages recorded. Nothing
here re-measures a frame or touches disk.
"""

import itertools
import logging
import math
import statistics
from typing import Any

logger = logging.getLogger(__name__)

# A mount's worm period is the dominant periodic-error term and typically
# falls in the low minutes; anything far outside this band is more likely
# drift, wind, or a guiding correction than periodic error. Used only to
# decide which detected period is worth reporting as PE -- the periodogram
# itself is unconstrained.
MINIMUM_PERIODIC_ERROR_PERIOD_SECONDS = 60.0
MAXIMUM_PERIODIC_ERROR_PERIOD_SECONDS = 1200.0

# A detected period must carry this share of the series' total variance
# before it is called periodic error rather than noise. Chosen so a flat
# or purely-drifting series never reports a period.
MINIMUM_PERIODIC_ERROR_POWER_FRACTION = 0.25

# Silence longer than this ends an observing session. Six hours is
# longer than any plausible within-night pause (a meridian flip, a cloud
# break, a refocus) and shorter than the gap between two nights, so it
# separates sessions without splitting one. This catalog's own frames
# sit either minutes apart within a night or at least a full day apart
# between them, so the threshold is not delicately placed.
SESSION_GAP_HOURS = 6.0

# Sessions shorter than this carry too few shifts for a drift rate or a
# periodogram to mean anything; they are counted and skipped rather than
# producing a confident number from three points.
MINIMUM_SESSION_FRAMES = 5

# A frame-to-frame shift larger than this is not a tracking excursion.
# No amateur mount moves a thousand pixels between consecutive subs and
# still produces a stackable sequence; a value that large means the
# registration transform for that frame is not a small translation.
#
# On the 2026-08-24 catalog 670 of 1,666 registered frames (40%) carried
# such values, exclusively on the colour DSLR targets, clustering at
# dx~6023/dy~3947 -- essentially the 6000x4000 frame size. NGC 7023 was
# the worst at 456 of 535, yet stacked all 535 frames with its FWHM
# *improving* from 8.15px to 5.68px, so the alignment Siril actually
# applied was sound and only the recorded numbers are wrong.
#
# The cause is unconfirmed: `parse_registration_data`'s docstring assumes
# "only one registration layer is ever present per file", which holds for
# mono but not for a 3-channel colour sequence where Siril writes R0/R1/R2.
# Verify against a real colour .seq before trusting these values; until
# then they are excluded so they cannot masquerade as tracking faults.
IMPLAUSIBLE_REGISTRATION_SHIFT_PX = 1000.0

# Above this, a frame's stars are elongated enough that the frame looks
# trailed rather than merely soft. Siril's roundness is fwhm_min/fwhm_max,
# so 1.0 is circular and smaller is more elongated.
TRAILED_FRAME_ROUNDNESS_THRESHOLD = 0.75


def _ordered_frames_with_shifts(frames: list) -> list:
    """Select frames carrying both a timestamp and a registration shift.

    Parameters
    ----------
    frames : `list`
        Frame records to filter.

    Returns
    -------
    ordered : `list`
        The usable frames, sorted by acquisition time.
    """
    usable = [
        frame
        for frame in frames
        if frame.timestamp is not None
        and frame.registration_dx_px is not None
        and frame.registration_dy_px is not None
        and abs(frame.registration_dx_px) <= IMPLAUSIBLE_REGISTRATION_SHIFT_PX
        and abs(frame.registration_dy_px) <= IMPLAUSIBLE_REGISTRATION_SHIFT_PX
    ]
    return sorted(usable, key=lambda frame: frame.timestamp)


def count_implausible_shifts(frames: list) -> int:
    """Count frames whose recorded shift cannot be a tracking excursion.

    Parameters
    ----------
    frames : `list`
        Frame records to inspect.

    Returns
    -------
    count : `int`
        Frames carrying a shift beyond
        `IMPLAUSIBLE_REGISTRATION_SHIFT_PX`.
    """
    return sum(
        1
        for frame in frames
        if frame.registration_dx_px is not None
        and frame.registration_dy_px is not None
        and (
            abs(frame.registration_dx_px) > IMPLAUSIBLE_REGISTRATION_SHIFT_PX
            or abs(frame.registration_dy_px) > IMPLAUSIBLE_REGISTRATION_SHIFT_PX
        )
    )


def split_frames_into_sessions(frames: list, gap_hours: float = SESSION_GAP_HOURS) -> list[list]:
    """Split time-ordered frames wherever an observing gap occurs.

    Tracking statistics only mean anything inside one continuous run of
    the mount. Across a gap the mount has slewed, been re-centred, and
    very likely been powered down, so a shift measured from one night to
    the next describes re-pointing, not tracking.

    Concatenating nights produced physically impossible numbers on the
    2026-08-24 catalog: NGC 7023's 535 frames span 9 separate nights and
    reported a "span" of 8,094 hours with a 9,779 px excursion -- 1.6x
    the sensor width -- and M 51's three nights spread over 15 months
    reported 10,962 hours.

    Parameters
    ----------
    frames : `list`
        Frames carrying timestamps, already sorted by acquisition time.
    gap_hours : `float`, optional
        Silence longer than this starts a new session.

    Returns
    -------
    sessions : `list` [`list`]
        One list of frames per session, in time order.
    """
    if not frames:
        return []

    sessions: list[list] = [[frames[0]]]
    gap_seconds = gap_hours * 3600.0
    for previous_frame, frame in itertools.pairwise(frames):
        if frame.timestamp - previous_frame.timestamp > gap_seconds:
            sessions.append([])
        sessions[-1].append(frame)
    return sessions


def _linear_trend_per_hour(times: list[float], values: list[float]) -> float | None:
    """Fit a least-squares slope, expressed per hour.

    Parameters
    ----------
    times : `list` [`float`]
        Epoch seconds, one per sample.
    values : `list` [`float`]
        Sample values, aligned with `times`.

    Returns
    -------
    slope_per_hour : `float` or `None`
        The fitted slope in value-units per hour, or `None` if the
        samples span no time.
    """
    if len(times) < 3:
        return None
    mean_time = statistics.fmean(times)
    mean_value = statistics.fmean(values)
    denominator = sum((time - mean_time) ** 2 for time in times)
    if denominator == 0:
        return None
    numerator = sum(
        (time - mean_time) * (value - mean_value) for time, value in zip(times, values, strict=True)
    )
    return (numerator / denominator) * 3600.0


def _dominant_period_seconds(times: list[float], values: list[float]) -> tuple[float | None, float]:
    """Find the strongest sinusoidal period in an unevenly sampled series.

    A direct Lomb-Scargle-style periodogram: the series is detrended,
    then correlated against sine and cosine at a sweep of trial periods.
    Written out rather than pulled from scipy because frames are unevenly
    spaced (an FFT would need resampling, which invents data at the exact
    timescale being measured).

    Parameters
    ----------
    times : `list` [`float`]
        Epoch seconds, one per sample.
    values : `list` [`float`]
        Sample values, aligned with `times`.

    Returns
    -------
    period_seconds : `float` or `None`
        The strongest period found within the mount-plausible band, or
        `None` if the series is too short or carries no clear period.
    power_fraction : `float`
        The share of the detrended series' variance that period explains,
        between 0 and 1.
    """
    if len(times) < 8:
        return None, 0.0

    # Detrend first: an uncorrected drift is a much larger signal than
    # periodic error and would otherwise dominate every trial period.
    slope_per_hour = _linear_trend_per_hour(times, values)
    if slope_per_hour is None:
        return None, 0.0
    start_time = times[0]
    mean_time = statistics.fmean(times)
    mean_value = statistics.fmean(values)
    # The slope term is centered on the mean time, not the first sample:
    # anchoring it at the start while also subtracting the mean removes
    # the trend twice at t=0 and leaves a constant offset in the
    # residuals. That offset inflates total_power and so deflates every
    # period's power fraction -- measured on a synthetic 8-minute
    # periodic error with drift, it pushed a true detection from 0.63
    # down to 0.20, under the reporting threshold.
    residuals = [
        value - mean_value - (slope_per_hour / 3600.0) * (time - mean_time)
        for time, value in zip(times, values, strict=True)
    ]
    total_power = sum(residual**2 for residual in residuals)
    if total_power <= 0:
        return None, 0.0

    span_seconds = times[-1] - times[0]
    if span_seconds <= 0:
        return None, 0.0
    # A period is only resolvable if the series covers it at least twice.
    longest_period = min(MAXIMUM_PERIODIC_ERROR_PERIOD_SECONDS, span_seconds / 2.0)
    if longest_period <= MINIMUM_PERIODIC_ERROR_PERIOD_SECONDS:
        return None, 0.0

    best_period = None
    best_power = 0.0
    trial_count = 240
    for step in range(trial_count):
        period = MINIMUM_PERIODIC_ERROR_PERIOD_SECONDS + (
            longest_period - MINIMUM_PERIODIC_ERROR_PERIOD_SECONDS
        ) * (step / (trial_count - 1))
        angular_frequency = 2.0 * math.pi / period
        sine_sum = sum(
            residual * math.sin(angular_frequency * (time - start_time))
            for time, residual in zip(times, residuals, strict=True)
        )
        cosine_sum = sum(
            residual * math.cos(angular_frequency * (time - start_time))
            for time, residual in zip(times, residuals, strict=True)
        )
        sine_norm = sum(math.sin(angular_frequency * (time - start_time)) ** 2 for time in times)
        cosine_norm = sum(math.cos(angular_frequency * (time - start_time)) ** 2 for time in times)
        if sine_norm <= 0 or cosine_norm <= 0:
            continue
        power = 0.5 * ((sine_sum**2 / sine_norm) + (cosine_sum**2 / cosine_norm))
        if power > best_power:
            best_power = power
            best_period = period

    return best_period, min(1.0, (2.0 * best_power) / total_power)


def _analyze_one_session(target: Any, frames: list) -> dict[str, Any]:
    """Describe one observing session's mount behaviour.

    Every statistic here assumes a continuous run of the mount, so the
    caller must pass a single session's frames -- see
    `split_frames_into_sessions` for why concatenating nights makes
    these numbers meaningless.

    Parameters
    ----------
    target : `Any`
        The target the session belongs to, used for meridian-flip
        detection.
    frames : `list`
        One session's frames, time-ordered, each carrying a timestamp
        and a registration shift.

    Returns
    -------
    analysis : `dict` [`str`, `Any`]
        ``usable_frames``, ``span_hours``, ``drift_x_px``/``drift_y_px``
        (net displacement), ``drift_rate_x_px_per_hour``/
        ``drift_rate_y_px_per_hour``, ``max_excursion_px``,
        ``periodic_error_period_seconds``, ``periodic_error_strength``,
        and ``findings`` (human-readable statements). ``usable_frames``
        is 0 when no frame carries both a timestamp and a shift.
    """
    analysis: dict[str, Any] = {
        "usable_frames": len(frames),
        "span_hours": None,
        "drift_x_px": None,
        "drift_y_px": None,
        "drift_rate_x_px_per_hour": None,
        "drift_rate_y_px_per_hour": None,
        "max_excursion_px": None,
        "periodic_error_period_seconds": None,
        "periodic_error_strength": 0.0,
        "findings": [],
    }
    if len(frames) < 3:
        analysis["findings"].append(
            "Not enough frames carry both a timestamp and a registration shift to analyze tracking."
        )
        return analysis

    times = [frame.timestamp for frame in frames]
    shifts_x = [frame.registration_dx_px for frame in frames]
    shifts_y = [frame.registration_dy_px for frame in frames]

    # Every shift being exactly zero means the shifts were never really
    # recorded, not that the mount was perfect -- see stacking_tasks'
    # note on the preserved-sequence fix.
    if not any(shifts_x) and not any(shifts_y):
        analysis["findings"].append(
            "Every frame records a zero shift, so tracking cannot be assessed. "
            "Re-stack this target to capture real registration shifts."
        )
        return analysis

    analysis["span_hours"] = round((times[-1] - times[0]) / 3600.0, 2)
    analysis["drift_x_px"] = round(shifts_x[-1] - shifts_x[0], 2)
    analysis["drift_y_px"] = round(shifts_y[-1] - shifts_y[0], 2)

    rate_x = _linear_trend_per_hour(times, shifts_x)
    rate_y = _linear_trend_per_hour(times, shifts_y)
    analysis["drift_rate_x_px_per_hour"] = round(rate_x, 2) if rate_x is not None else None
    analysis["drift_rate_y_px_per_hour"] = round(rate_y, 2) if rate_y is not None else None

    excursions = [
        math.hypot(x - shifts_x[index - 1], y - shifts_y[index - 1])
        for index, (x, y) in enumerate(zip(shifts_x, shifts_y, strict=True))
        if index > 0
    ]
    analysis["max_excursion_px"] = round(max(excursions), 2) if excursions else None

    # Periodic error shows in the axis the worm drives; both are tested
    # and the stronger reported.
    period_x, power_x = _dominant_period_seconds(times, shifts_x)
    period_y, power_y = _dominant_period_seconds(times, shifts_y)
    period, power, axis = (period_x, power_x, "x") if power_x >= power_y else (period_y, power_y, "y")
    if period is not None and power >= MINIMUM_PERIODIC_ERROR_POWER_FRACTION:
        analysis["periodic_error_period_seconds"] = round(period)
        analysis["periodic_error_strength"] = round(power, 3)
        analysis["findings"].append(
            f"Periodic drift on the {axis} axis with a ~{period / 60:.1f} minute period "
            f"explains {power:.0%} of the residual motion, consistent with mount periodic error."
        )

    for rate, axis_name in ((rate_x, "x"), (rate_y, "y")):
        if rate is not None and abs(rate) > 10.0:
            analysis["findings"].append(
                f"Sustained {axis_name}-axis drift of {rate:.1f} px/hour, "
                "consistent with polar misalignment or an uncorrected tracking rate."
            )

    flip_indices = detect_meridian_flips(target)
    analysis["meridian_flips"] = len(flip_indices)
    if flip_indices:
        analysis["findings"].append(
            f"{len(flip_indices)} meridian flip(s) during this session; the field shift across "
            "a flip is expected and is not a tracking fault."
        )

    if analysis["max_excursion_px"] and excursions:
        median_excursion = statistics.median(excursions)
        # Excursions at a flip boundary are excluded before calling one
        # anomalous: a flip legitimately moves the field, so counting it
        # would report a hardware fault for normal mount behaviour.
        flip_excursion_indices = {index - 1 for index in flip_indices}
        natural_excursions = [
            excursion for index, excursion in enumerate(excursions) if index not in flip_excursion_indices
        ]
        largest_natural = max(natural_excursions) if natural_excursions else 0.0
        if median_excursion > 0 and largest_natural > 10 * median_excursion:
            analysis["findings"].append(
                f"One frame jumped {largest_natural:.1f} px against a typical "
                f"{median_excursion:.1f} px, suggesting a bump, wind gust, or cable snag."
            )

    if not analysis["findings"]:
        analysis["findings"].append("No drift, periodic error, or excursion stands out.")
    return analysis


def _combine_session_analyses(
    usable_frame_count: int, session_count: int, session_analyses: list[dict[str, Any]]
) -> dict[str, Any]:
    """Summarise per-session tracking analyses into one report.

    Reports the worst session for each statistic rather than an average.
    A single bad night is what an observer needs to find, and averaging
    it against good ones hides exactly the case worth acting on.

    Parameters
    ----------
    usable_frame_count : `int`
        Frames carrying both a timestamp and a registration shift.
    session_count : `int`
        Sessions found, including those too short to analyse.
    session_analyses : `list` [`dict`]
        One `_analyze_one_session` result per analysed session.

    Returns
    -------
    analysis : `dict` [`str`, `Any`]
        The same keys `_analyze_one_session` produces, taken from the
        worst session, plus ``sessions_analyzed``, ``sessions_found``
        and ``sessions`` holding each session's own analysis.
    """

    def _worst(key: str) -> Any:
        values = [session[key] for session in session_analyses if session.get(key) is not None]
        return max(values, key=abs) if values else None

    strongest_periodic = max(
        session_analyses, key=lambda session: session.get("periodic_error_strength") or 0.0
    )

    findings: list[str] = []
    for index, session in enumerate(session_analyses, start=1):
        for finding in session.get("findings", []):
            if finding.startswith("No drift"):
                continue
            findings.append(f"Session {index}: {finding}")
    if not findings:
        findings.append("No drift, periodic error, or excursion stands out in any session.")

    return {
        "usable_frames": usable_frame_count,
        "sessions_found": session_count,
        "sessions_analyzed": len(session_analyses),
        "sessions": session_analyses,
        "span_hours": _worst("span_hours"),
        "drift_x_px": _worst("drift_x_px"),
        "drift_y_px": _worst("drift_y_px"),
        "drift_rate_x_px_per_hour": _worst("drift_rate_x_px_per_hour"),
        "drift_rate_y_px_per_hour": _worst("drift_rate_y_px_per_hour"),
        "max_excursion_px": _worst("max_excursion_px"),
        "periodic_error_period_seconds": strongest_periodic.get("periodic_error_period_seconds"),
        "periodic_error_strength": strongest_periodic.get("periodic_error_strength", 0.0),
        "meridian_flips": sum(session.get("meridian_flips", 0) or 0 for session in session_analyses),
        "findings": findings,
    }


def analyze_guiding(target: Any) -> dict[str, Any]:
    """Describe a target's mount behaviour, session by session.

    Frames are split into observing sessions first, because drift,
    excursion and periodic error only mean anything within one
    continuous run of the mount. Analysing a target's frames as a single
    series reports the re-pointing between nights as tracking error: on
    the 2026-08-24 catalog that gave NGC 7023 a span of 8,094 hours and
    a 9,779 px excursion, larger than the sensor is wide.

    Parameters
    ----------
    target : `Any`
        The target whose frames are analyzed.

    Returns
    -------
    analysis : `dict` [`str`, `Any`]
        ``usable_frames``, ``sessions_found``, ``sessions_analyzed``,
        and ``sessions`` (each session's own analysis), plus the worst
        session's ``span_hours``, ``drift_x_px``/``drift_y_px``,
        ``drift_rate_x_px_per_hour``/``drift_rate_y_px_per_hour``,
        ``max_excursion_px``, ``periodic_error_period_seconds``,
        ``periodic_error_strength`` and ``findings``.
    """
    all_frames = _ordered_frames_with_shifts(target.frames or [])
    sessions = split_frames_into_sessions(all_frames)
    long_enough = [session for session in sessions if len(session) >= MINIMUM_SESSION_FRAMES]
    session_analyses = [_analyze_one_session(target, session) for session in long_enough]

    if session_analyses:
        return _combine_session_analyses(len(all_frames), len(sessions), session_analyses)

    analysis = _analyze_one_session(target, all_frames)
    analysis["sessions_found"] = len(sessions)
    analysis["sessions_analyzed"] = 0
    analysis["sessions"] = []
    return analysis


def fwhm_in_arcsec(frame: Any) -> float | None:
    """Express a frame's FWHM in arcseconds rather than pixels.

    A FWHM in pixels is only meaningful alongside the pixel scale that
    produced it: the same seeing reads as a different pixel count on a
    different camera or focal length. Arcseconds are comparable across
    every frame in the library and are the unit seeing is actually
    discussed in.

    Parameters
    ----------
    frame : `Any`
        The frame record to convert.

    Returns
    -------
    fwhm_arcsec : `float` or `None`
        FWHM in arcseconds, or `None` if either the FWHM or the pixel
        scale is missing.
    """
    fwhm_px = frame.registration_fwhm_x_px
    if fwhm_px is None or frame.pixel_scale_arcsec is None:
        return None
    return round(fwhm_px * frame.pixel_scale_arcsec, 2)


def detect_meridian_flips(target: Any) -> list[int]:
    """Find where a session changed pier side.

    A meridian flip moves the field abruptly, so the shift between the
    frames either side of one is not a tracking fault. Without this, that
    jump reads as a bump or cable snag in `analyze_guiding`.

    Parameters
    ----------
    target : `Any`
        The target whose frames are examined.

    Returns
    -------
    flip_indices : `list` [`int`]
        Indices, into the time-ordered frame list, of each frame that
        begins a new pier side.
    """
    ordered = sorted(
        (f for f in (target.frames or []) if f.timestamp is not None and f.pier_side),
        key=lambda f: f.timestamp,
    )
    return [
        index for index in range(1, len(ordered)) if ordered[index].pier_side != ordered[index - 1].pier_side
    ]


def analyze_input_conditions(target: Any) -> dict[str, Any]:
    """Describe seeing, trailing, and sky conditions across a target's frames.

    Parameters
    ----------
    target : `Any`
        The target whose frames are analyzed.

    Returns
    -------
    analysis : `dict` [`str`, `Any`]
        Median and spread for FWHM, roundness, and background, the count
        of trailed frames, and ``findings``.
    """
    frames = target.frames or []
    fwhm_values = [f.registration_fwhm_x_px for f in frames if f.registration_fwhm_x_px is not None]
    roundness_values = [f.registration_roundness for f in frames if f.registration_roundness is not None]
    background_values = [f.background_level for f in frames if f.background_level is not None]
    trailed = [
        f
        for f in frames
        if f.registration_roundness is not None
        and f.registration_roundness < TRAILED_FRAME_ROUNDNESS_THRESHOLD
    ]

    analysis: dict[str, Any] = {
        "median_fwhm_px": round(statistics.median(fwhm_values), 2) if fwhm_values else None,
        "fwhm_spread_px": round(max(fwhm_values) - min(fwhm_values), 2) if len(fwhm_values) > 1 else None,
        "median_roundness": round(statistics.median(roundness_values), 3) if roundness_values else None,
        "trailed_frame_count": len(trailed),
        "median_background": round(statistics.median(background_values), 1) if background_values else None,
        "background_spread": round(max(background_values) - min(background_values), 1)
        if len(background_values) > 1
        else None,
        "findings": [],
    }

    if trailed:
        analysis["findings"].append(
            f"{len(trailed)} frame(s) have roundness below "
            f"{TRAILED_FRAME_ROUNDNESS_THRESHOLD}, meaning visibly elongated stars."
        )
    if analysis["fwhm_spread_px"] and analysis["median_fwhm_px"]:
        if analysis["fwhm_spread_px"] > analysis["median_fwhm_px"]:
            analysis["findings"].append(
                f"FWHM varies by {analysis['fwhm_spread_px']:.1f} px around a median of "
                f"{analysis['median_fwhm_px']:.1f} px -- focus drift or changing seeing."
            )
    if analysis["background_spread"] and analysis["median_background"]:
        if analysis["background_spread"] > analysis["median_background"]:
            analysis["findings"].append(
                f"Sky background ranges over {analysis['background_spread']:.0f} ADU against a median "
                f"of {analysis['median_background']:.0f} -- moonlight, twilight, or cloud."
            )
    if not analysis["findings"]:
        analysis["findings"].append("Seeing, star shape, and sky background are all consistent.")
    return analysis


def evaluate_rejection_effectiveness(target: Any) -> dict[str, Any]:
    """Check whether frame rejection actually tracked frame quality.

    A pipeline that rejects frames should be rejecting the *worse* ones.
    Comparing the input quality of rejected frames against kept ones
    turns that assumption into something checkable: if the two groups
    look identical, the rejection is not selecting on quality.

    Parameters
    ----------
    target : `Any`
        The target whose stacking summary and frames are compared.

    Returns
    -------
    analysis : `dict` [`str`, `Any`]
        Median background and FWHM for rejected and kept frames, the
        counts of each, and ``findings``. ``comparable`` is `False` when
        there are too few measured frames on either side to compare.
    """
    summary = getattr(target, "stack_quality_summary", None)
    metrics = getattr(summary, "stacking_metrics", None) if summary else None
    excluded = list(getattr(metrics, "excluded_frames", []) or []) if metrics else []
    excluded_paths = {getattr(entry, "path", None) for entry in excluded}

    rejected = [f for f in (target.frames or []) if f.path in excluded_paths]
    kept = [f for f in (target.frames or []) if f.path not in excluded_paths]

    def median_of(frames, attribute):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        values = [getattr(f, attribute) for f in frames if getattr(f, attribute) is not None]
        return round(statistics.median(values), 2) if values else None

    analysis: dict[str, Any] = {
        "rejected_count": len(rejected),
        "kept_count": len(kept),
        "rejected_median_background": median_of(rejected, "background_level"),
        "kept_median_background": median_of(kept, "background_level"),
        "rejected_median_fwhm_px": median_of(rejected, "registration_fwhm_x_px"),
        "kept_median_fwhm_px": median_of(kept, "registration_fwhm_x_px"),
        "comparable": False,
        "findings": [],
    }

    if not rejected:
        analysis["findings"].append("No frames were rejected for this target.")
        return analysis
    if analysis["rejected_median_background"] is None or analysis["kept_median_background"] is None:
        analysis["findings"].append(
            "Rejected and kept frames cannot be compared: input quality has not been measured on both groups."
        )
        return analysis

    analysis["comparable"] = True
    background_ratio = analysis["rejected_median_background"] / max(analysis["kept_median_background"], 1e-9)
    if background_ratio > 1.5:
        analysis["findings"].append(
            f"Rejected frames sit at {background_ratio:.1f}x the sky background of kept frames -- "
            "rejection is selecting genuinely brighter-sky frames."
        )
    elif background_ratio < 0.67:
        analysis["findings"].append(
            f"Rejected frames are darker than kept ones ({background_ratio:.2f}x background), "
            "which is the opposite of what outlier rejection should select."
        )
    else:
        analysis["findings"].append(
            f"Rejected and kept frames have near-identical sky background "
            f"({background_ratio:.2f}x), so rejection is not selecting on background."
        )
    return analysis
