"""Tools to analyze mount tracking and image quality.

This file looks at the data saved about each image to figure out how well
the telescope was tracking the sky. It checks for things like the mount
drifting over time, periodic mechanical errors (like the worm gear wobbling),
sudden jumps, and changes in focus or sky brightness.

It only analyzes existing data; it doesn't process images or save new files.
"""

import itertools
import logging
import math
import statistics
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from astrometricslib.models.quality_summary import TrackingQualitySummary

# A mount's worm period is the dominant periodic-error term and typically
# falls in the low minutes; anything far outside this band is more likely
# drift, wind, or a guiding correction than periodic error. Used only to
# decide which detected period is worth reporting as PE -- the periodogram
# itself is unconstrained.
MINIMUM_PERIODIC_ERROR_PERIOD_SECONDS = 60.0
MAXIMUM_PERIODIC_ERROR_PERIOD_SECONDS = 1200.0

# A detected period must carry this share of the series' total variance
# before it is called periodic error rather than noise.
#
# 0.25 was too permissive to mean anything: run across this catalog's
# 28 deep-sky sessions with a detected period, the periods themselves
# scattered from 1.0 to 14.2 minutes with no clustering, including
# among high-power detections (M 1 at 1.3min/0.91, IC 1805 at
# 4.6min/0.92, NGC 7023 at 5.0min/0.88) -- a real worm period is a
# mechanical constant and should recur across sessions and targets, not
# vary target to target. That scatter means the periodogram was mostly
# fitting whichever low-frequency guiding correction or drift residual
# a short session happened to contain, not real periodic error.
#
# Raising the bar to 0.5 cuts the weakest half of those detections
# (0.257-0.487) without touching the stronger ones, but does not by
# itself prove what remains is mechanical -- see the finding text below,
# which is now hedged accordingly rather than asserting periodic error
# from a single session.
MINIMUM_PERIODIC_ERROR_POWER_FRACTION = 0.5

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
    """Filter out images missing timestamps or location data.

    Parameters
    ----------
    frames : `list`
        The image records to check.

    Returns
    -------
    ordered : `list`
        The valid images, sorted by when they were taken.
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
    """Count how many images have a position shift too big to be real.

    Parameters
    ----------
    frames : `list`
        The image records to check.

    Returns
    -------
    count : `int`
        How many images have a shift bigger than
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
    """Split a list of images into separate observing sessions.

    If two images in a row were taken more than `gap_hours` apart, we
    assume the telescope was turned off in between and start a new
    session. This keeps us from comparing images taken on different
    nights as if they were part of one continuous run.

    Parameters
    ----------
    frames : `list`
        Images sorted by the time they were taken.
    gap_hours : `float`, optional
        How many hours of silence means a new session has started.

    Returns
    -------
    sessions : `list` of `list`
        The images, grouped into separate sessions, in time order.
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
    """Calculate the average rate of change per hour using a straight line fit.

    Parameters
    ----------
    times : `list` of `float`
        Timestamps for each data point (in seconds).
    values : `list` of `float`
        The value recorded at each time.

    Returns
    -------
    slope_per_hour : `float` or `None`
        The average change per hour. Returns None if there's not enough data.
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
    """Find a repeating pattern (like a sine wave) in the data.

    This helps us find the "periodic error" of the telescope mount, which
    is a regular wobble caused by the gears turning.

    Parameters
    ----------
    times : `list` of `float`
        Timestamps for each data point (in seconds).
    values : `list` of `float`
        The value recorded at each time.

    Returns
    -------
    period_seconds : `float` or `None`
        The time in seconds it takes for the wobble to repeat.
    power_fraction : `float`
        A number from 0 to 1 showing how much of the movement is caused
        by this specific wobble, rather than random noise.
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


def _analyze_one_session(frames: list) -> dict[str, Any]:
    """Calculate tracking statistics for a single observing session.

    This calculates things like the total drift, the average speed of the
    drift, and the biggest sudden jump.

    Parameters
    ----------
    frames : `list`
        Images from one session, sorted by time, that include timestamps
        and movement data.

    Returns
    -------
    analysis : `dict`
        A dictionary containing the tracking numbers (like `drift_x_px` and
        `max_excursion_px`) and a list of `findings` (text warnings if
        something looks wrong).
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
        # One session cannot confirm mechanical periodic error: a
        # worm's period is a fixed constant, so a genuine detection
        # must recur at the same period across sessions and targets.
        # This is one session's strongest candidate period, not a
        # verdict -- see _combine_session_analyses' cross-session check
        # for the confirmed case.
        analysis["findings"].append(
            f"Periodic drift on the {axis} axis with a ~{period / 60:.1f} minute period "
            f"explains {power:.0%} of this session's residual motion. A single session cannot "
            "confirm mechanical periodic error; check whether this period recurs elsewhere."
        )

    for rate, axis_name in ((rate_x, "x"), (rate_y, "y")):
        if rate is not None and abs(rate) > 10.0:
            analysis["findings"].append(
                f"Sustained {axis_name}-axis drift of {rate:.1f} px/hour, "
                "consistent with polar misalignment or an uncorrected tracking rate."
            )

    flip_indices = detect_meridian_flips(frames)
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
    """Combine the reports from multiple observing sessions into one.

    This reports the *worst* session for each problem, rather than the average.
    If you had 4 good nights and 1 terrible night, the average might look okay,
    but we want to highlight the terrible night so you can figure out what
    went wrong.

    Parameters
    ----------
    usable_frame_count : `int`
        Total number of images that had useful data.
    session_count : `int`
        Total number of sessions found.
    session_analyses : `list` of `dict`
        The list of individual session reports.

    Returns
    -------
    analysis : `dict`
        A single dictionary containing the worst-case numbers across all
        sessions, plus the individual session reports inside it.
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

    # A worm's period is a mechanical constant, so it should recur
    # across independent sessions. Two sessions agreeing within 15% is
    # the corroboration a single session's finding above explicitly
    # says it lacks; this is the only place in this module that
    # promotes a candidate period to an actual mechanical claim.
    periods = [
        session["periodic_error_period_seconds"]
        for session in session_analyses
        if session.get("periodic_error_period_seconds")
    ]
    for first, second in itertools.combinations(periods, 2):
        if abs(first - second) <= 0.15 * max(first, second):
            findings.append(
                f"Periodic error near {statistics.fmean([first, second]) / 60:.1f} minutes recurs "
                "across independent sessions, corroborating a mechanical (worm gear) cause."
            )
            break

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
    """Calculate tracking statistics for all images in a target.

    This function first splits the images into separate observing sessions
    (like different nights), then calculates the tracking performance for
    each session.

    Parameters
    ----------
    target : `Any`
        The target to analyze.

    Returns
    -------
    analysis : `dict`
        A dictionary summarizing the mount's tracking performance.
    """
    all_frames = _ordered_frames_with_shifts(target.frames or [])
    sessions = split_frames_into_sessions(all_frames)
    long_enough = [session for session in sessions if len(session) >= MINIMUM_SESSION_FRAMES]
    session_analyses = [_analyze_one_session(session) for session in long_enough]

    if session_analyses:
        return _combine_session_analyses(len(all_frames), len(sessions), session_analyses)

    # No session reached MINIMUM_SESSION_FRAMES. Analysing only the
    # longest single session -- never `all_frames` -- matters here:
    # concatenating every session back together is exactly the
    # cross-night join this function's docstring describes fixing
    # (NGC 7023's 8,094-hour span, 9,779px excursion). A target whose
    # every session is individually short still deserves that session's
    # own, correctly-scoped analysis rather than none at all.
    longest_session = max(sessions, key=len) if sessions else []
    analysis = _analyze_one_session(longest_session)
    analysis["sessions_found"] = len(sessions)
    analysis["sessions_analyzed"] = 0
    analysis["sessions"] = []
    return analysis


def fwhm_in_arcsec(frame: Any) -> float | None:
    """Convert an image's sharpness (FWHM) from pixels to arcseconds.

    Measuring in arcseconds lets us compare image sharpness across different
    cameras and telescopes, because a pixel on one camera might cover more
    or less sky than a pixel on another.

    Parameters
    ----------
    frame : `Any`
        The image record to calculate.

    Returns
    -------
    fwhm_arcsec : `float` or `None`
        The sharpness in arcseconds, or None if we don't have enough data.
    """
    fwhm_px = frame.registration_fwhm_x_px
    if fwhm_px is None or frame.pixel_scale_arcsec is None:
        return None
    return round(fwhm_px * frame.pixel_scale_arcsec, 2)


def detect_meridian_flips(frames: list) -> list[int]:
    """Find out when the telescope flipped over to the other side of the pier.

    Telescopes often have to "flip" when a target crosses the highest point
    in the sky (the meridian) so they don't crash into the mount. This causes
    a big jump in the image position, which we need to ignore so we don't
    count it as a tracking error.

    Parameters
    ----------
    frames : `list`
        Images sorted by time.

    Returns
    -------
    flip_indices : `list` of `int`
        The list indexes of the images where a flip happened.
    """
    flip_indices = []
    last_known_side = None
    for index, frame in enumerate(frames):
        side = frame.pier_side
        if side is None:
            continue
        if last_known_side is not None and side != last_known_side:
            flip_indices.append(index)
        last_known_side = side
    return flip_indices


def analyze_input_conditions(target: Any) -> dict[str, Any]:
    """Calculate statistics about the sky and image quality.

    This checks things like the average sharpness of the stars, how many
    images had trailing (stretched stars), and the brightness of the sky
    background.

    Parameters
    ----------
    target : `Any`
        The target whose images we are analyzing.

    Returns
    -------
    analysis : `dict`
        A dictionary of the calculated statistics and text findings.
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
    """Check if the software successfully threw out the bad images.

    If our stacking software throws out images, we want to make sure it
    threw out the blurry or bright ones, and kept the sharp, dark ones.
    This function compares the quality of the kept images vs rejected ones.

    Parameters
    ----------
    target : `Any`
        The target to check.

    Returns
    -------
    analysis : `dict`
        A summary comparing the two groups of images.
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


def build_tracking_quality_summary(target: Any) -> TrackingQualitySummary | None:
    """Create a complete tracking report for a target.

    This runs all the individual tracking and quality checks and bundles
    the results into a single summary object that gets saved to the database.

    Parameters
    ----------
    target : `Any`
        The target to analyze.

    Returns
    -------
    summary : `TrackingQualitySummary` or `None`
        The final summary object, or None if there wasn't enough data.
    """
    from astrometricslib.models.quality_summary import TrackingPipelineQualityMetrics, TrackingQualitySummary

    guiding = analyze_guiding(target)
    conditions = analyze_input_conditions(target)
    if not guiding.get("usable_frames") and conditions.get("median_fwhm_px") is None:
        return None

    periods = [
        session["periodic_error_period_seconds"]
        for session in guiding.get("sessions", [])
        if session.get("periodic_error_period_seconds")
    ]
    corroborated = any(
        abs(first - second) <= 0.15 * max(first, second)
        for first, second in itertools.combinations(periods, 2)
    )

    findings = list(guiding.get("findings", [])) + list(conditions.get("findings", []))
    summary = TrackingQualitySummary(
        target_id=target.id,
        tracking_metrics=TrackingPipelineQualityMetrics(
            sessions_found=guiding.get("sessions_found", 0),
            sessions_analyzed=guiding.get("sessions_analyzed", 0),
            usable_frames=guiding.get("usable_frames", 0),
            span_hours=guiding.get("span_hours"),
            drift_rate_x_px_per_hour=guiding.get("drift_rate_x_px_per_hour"),
            drift_rate_y_px_per_hour=guiding.get("drift_rate_y_px_per_hour"),
            max_excursion_px=guiding.get("max_excursion_px"),
            meridian_flips=guiding.get("meridian_flips", 0),
            periodic_error_period_seconds=guiding.get("periodic_error_period_seconds"),
            periodic_error_strength=guiding.get("periodic_error_strength", 0.0),
            periodic_error_corroborated=corroborated,
            trailed_frame_count=conditions.get("trailed_frame_count", 0),
            median_fwhm_px=conditions.get("median_fwhm_px"),
            fwhm_spread_px=conditions.get("fwhm_spread_px"),
            median_roundness=conditions.get("median_roundness"),
            median_background=conditions.get("median_background"),
            background_spread=conditions.get("background_spread"),
        ),
    )

    real_findings = [
        finding
        for finding in findings
        if not finding.startswith("No drift")
        and not finding.startswith("Seeing, star shape")
        and not finding.startswith("Not enough frames")
        and "cannot be assessed" not in finding
    ]
    if real_findings:
        summary.flagged = True
        summary.flag_reasons = real_findings

    return summary
