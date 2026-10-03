"""Purpose: Judge how the current observing session is going, right now.

Description: The after-the-night analyses in this package read stored
records. This module reads the live Ekos files instead (the analyze log
and the KStars text log) and answers the questions an observer asks during
a session: Is guiding steady? Did the last dither work? Which frames are
already ruined? It stores nothing and reads only what Ekos has written so
far.

Every threshold below is measured from the session's own data, so it adapts
to the equipment in use. Each carries a comment giving how it was chosen
and the data it was checked against.
"""

import math
import statistics
import time
from collections.abc import Sequence

import numpy as np

from wayfindinglib.models.session.ekos_session import EkosCapture, EkosGuideStat, EkosSessionContext
from wayfindinglib.models.session.live_session_status import (
    DitherEvent,
    ExposureSummary,
    GuidingExcursion,
    GuidingWindowSummary,
    LiveSessionStatus,
)

EARLY_EXPOSURE_SECONDS = 30.0
"""Length of the opening stretch of an exposure compared with the rest.

A dither's after-effects show in the first moments of the next exposure.
Checked on the 2026-10-02 M 27 session: the 30 s after a dither averaged
2.3 to 6.0 arcsec of guide error, against 1.3 to 1.7 arcsec for the rest.
"""

DITHER_FOLLOW_SECONDS = 130.0
"""How long before an exposure a dither counts as "just before" it.

One exposure plus its settle time on that session: 120 s exposure plus a
settle of up to 30 s would be 150 s, but the dither ends before the
settle begins, and the exposure starts at the end of the settle, so a
dither that began within this time of the exposure's start led into it.
"""

EXCURSION_MEDIAN_MULTIPLE = 8.0
"""An excursion is a guide error above this multiple of the session median.

Using the session's own median keeps the limit tied to the equipment's
normal guiding. On 2026-10-02 the median total error was about 1.3 arcsec,
giving a limit near 10 arcsec. Quiet guiding peaked below 4 arcsec and
never reached it. The three real events (a 12 arcminute jump during a
dither, and two minutes of RA drift reaching 300 arcsec) all exceeded it by
a wide margin.
"""

EXCURSION_FLOOR_ARCSEC = 5.0
"""The smallest excursion limit, whatever the median is.

A session with unusually tight guiding would otherwise flag ordinary
fluctuations of a couple of arcseconds.
"""

EXCURSION_JOIN_SECONDS = 20.0
"""Two over-limit runs closer together than this are one excursion.

The guider re-locks on its star after a large error, which briefly resets
the error to near zero. On 2026-10-02 the reset lasted a few seconds
between three runs of one drift, so a join time of 20 s merges them while
keeping the 21:15 and 22:17 events apart.
"""

STOPPED_AXIS_TOLERANCE = 0.25
"""Fraction a drift rate may differ from a stopped mount's and still match.

The drift measured on 2026-10-02 was 7 arcsec per second against a
stopped-mount value of 7.1 at declination +61.7 degrees. A quarter allows
for the fit's error without matching ordinary tracking errors.
"""

SIDEREAL_RATE_ARCSEC_PER_SECOND = 15.0411
"""How fast the sky turns: arcseconds of right ascension per second."""

LOW_STAR_FRACTION = 0.5
"""A frame with fewer stars than this fraction of the usual one is flagged.

The usual count is the median of the frames before it. Trailed or clouded
frames lose their stars quickly. On 2026-10-02 a normal frame held about
240 stars, and the frame exposed during the two-minute drift held a small
fraction of that.
"""

MINIMUM_FRAMES_FOR_STAR_BASELINE = 5
"""Frames needed before a star-count baseline means anything."""

JUMP_FIRST_SAMPLE_FRACTION = 0.5
"""An excursion is a jump if its first sample is already this much of its peak.

A drift builds up over many samples, so its first over-limit sample is a
small share of the peak (33 of 307 arcsec for the 21:53 drift on
2026-10-02). A jump arrives at full size in one step (the 21:15 Dec jump
was at its 739 arcsec peak from its first sample).
"""

MINIMUM_SAMPLES_FOR_DRIFT_FIT = 3
"""Measurements needed to fit a straight line to part of an excursion."""

RECENT_EXPOSURE_COUNT = 10
"""How many of the latest exposures the status lists."""

STALE_RECORD_SECONDS = 300.0
"""A file with no new record for this long is reported as stale.

Ekos writes a guide measurement every few seconds while guiding, so five
minutes of silence means guiding or Ekos itself has stopped.
"""


def _rms(values: Sequence[float]) -> float | None:
    """Return the root-mean-square of `values`, or `None` if empty.

    Parameters
    ----------
    values : `Sequence` [`float`]
        The numbers to combine.

    Returns
    -------
    rms : `float` or `None`
        The root-mean-square.
    """
    if not values:
        return None
    return math.sqrt(sum(value * value for value in values) / len(values))


def _total_error(sample: EkosGuideStat) -> float:
    """Combine one sample's RA and Dec errors into a single distance.

    Parameters
    ----------
    sample : `EkosGuideStat`
        One guiding measurement.

    Returns
    -------
    error_arcsec : `float`
        The distance of the guide star from its target, in arcseconds.
    """
    return math.hypot(sample.ra_error_arcsec, sample.dec_error_arcsec)


def summarize_guiding_window(
    guide_stats: Sequence[EkosGuideStat], end_time: float, window_seconds: float
) -> GuidingWindowSummary:
    """Summarize guiding accuracy over the stretch ending at `end_time`.

    Parameters
    ----------
    guide_stats : `Sequence` [`EkosGuideStat`]
        All guiding measurements, in time order.
    end_time : `float`
        End of the window, in seconds since the Unix epoch.
    window_seconds : `float`
        Length of the window.

    Returns
    -------
    summary : `GuidingWindowSummary`
        Root-mean-square errors over the window.
    """
    inside = [s for s in guide_stats if end_time - window_seconds < s.timestamp <= end_time]
    return GuidingWindowSummary(
        window_seconds=window_seconds,
        sample_count=len(inside),
        ra_rms_arcsec=_rms([s.ra_error_arcsec for s in inside]),
        dec_rms_arcsec=_rms([s.dec_error_arcsec for s in inside]),
        total_rms_arcsec=_rms([_total_error(s) for s in inside]),
    )


def _axis_error(sample: EkosGuideStat, axis: str) -> float:
    """Return one sample's error along `axis`.

    Parameters
    ----------
    sample : `EkosGuideStat`
        One guiding measurement.
    axis : `str`
        ``"RA"`` or ``"Dec"``.

    Returns
    -------
    error_arcsec : `float`
        The signed error along that axis, in arcseconds.
    """
    return sample.ra_error_arcsec if axis == "RA" else sample.dec_error_arcsec


def _median_drift_rate(
    run: Sequence[EkosGuideStat], axis: str, reacquire_times: Sequence[float]
) -> float | None:
    """Estimate how fast the guide error grew during an excursion.

    Each guider re-lock resets the error, so one straight-line fit across
    a whole excursion would underestimate the drift. The excursion is cut
    at every re-lock, each piece is fitted on its own, and the median
    slope is returned.

    Parameters
    ----------
    run : `Sequence` [`EkosGuideStat`]
        The measurements inside one excursion, in time order.
    axis : `str`
        ``"RA"`` or ``"Dec"``: the axis to fit.
    reacquire_times : `Sequence` [`float`]
        When the guider re-locked on its star.

    Returns
    -------
    rate : `float` or `None`
        The drift in arcseconds per second, or `None` if no piece has at
        least three measurements.
    """
    cuts = sorted(t for t in reacquire_times if run[0].timestamp < t < run[-1].timestamp)
    pieces: list[list[EkosGuideStat]] = [[]]
    cut_index = 0
    for sample in run:
        while cut_index < len(cuts) and sample.timestamp > cuts[cut_index]:
            pieces.append([])
            cut_index += 1
        pieces[-1].append(sample)
    slopes = []
    for piece in pieces:
        if len(piece) < MINIMUM_SAMPLES_FOR_DRIFT_FIT:
            continue
        times = np.array([sample.timestamp for sample in piece])
        errors = np.array([
            sample.ra_error_arcsec if axis == "RA" else sample.dec_error_arcsec for sample in piece
        ])
        slopes.append(abs(float(np.polyfit(times - times[0], errors, 1)[0])))
    return statistics.median(slopes) if slopes else None


def find_guiding_excursions(
    guide_stats: Sequence[EkosGuideStat],
    reacquire_times: Sequence[float],
    declination_degrees: float | None,
) -> list[GuidingExcursion]:
    """Find the stretches where the guide error ran far above normal.

    Parameters
    ----------
    guide_stats : `Sequence` [`EkosGuideStat`]
        All guiding measurements, in time order.
    reacquire_times : `Sequence` [`float`]
        When the guider re-locked on its star.
    declination_degrees : `float` or `None`
        The mount's declination, used to say whether an RA drift matches a
        stopped mount.

    Returns
    -------
    excursions : `list` [`GuidingExcursion`]
        Each over-limit stretch, in time order.
    """
    if not guide_stats:
        return []
    median_error = statistics.median(_total_error(s) for s in guide_stats)
    limit = max(EXCURSION_MEDIAN_MULTIPLE * median_error, EXCURSION_FLOOR_ARCSEC)

    runs: list[list[EkosGuideStat]] = []
    for sample in guide_stats:
        if _total_error(sample) <= limit:
            continue
        if runs and sample.timestamp - runs[-1][-1].timestamp <= EXCURSION_JOIN_SECONDS:
            runs[-1].append(sample)
        else:
            runs.append([sample])

    excursions = []
    for run in runs:
        ra_peak = max(abs(s.ra_error_arcsec) for s in run)
        dec_peak = max(abs(s.dec_error_arcsec) for s in run)
        axis = "RA" if ra_peak >= dec_peak else "Dec"
        rate = _median_drift_rate(run, axis, reacquire_times)
        matches_stopped = None
        if axis == "RA" and rate is not None and declination_degrees is not None:
            stopped_rate = SIDEREAL_RATE_ARCSEC_PER_SECOND * math.cos(math.radians(declination_degrees))
            matches_stopped = abs(rate - stopped_rate) <= STOPPED_AXIS_TOLERANCE * stopped_rate
        started_at, ended_at = run[0].timestamp, run[-1].timestamp
        peak_error = max(_total_error(s) for s in run)
        kind = "jump" if _total_error(run[0]) >= JUMP_FIRST_SAMPLE_FRACTION * peak_error else "drift"
        excursions.append(
            GuidingExcursion(
                started_at=started_at,
                ended_at=ended_at,
                peak_error_arcsec=peak_error,
                axis=axis,
                kind=kind,
                drift_rate_arcsec_per_second=rate,
                matches_stopped_ra_axis=matches_stopped,
                reacquire_count=sum(1 for t in reacquire_times if started_at - 10.0 <= t <= ended_at + 10.0),
            )
        )
    return excursions


def summarize_exposures(
    captures: Sequence[EkosCapture],
    guide_stats: Sequence[EkosGuideStat],
    excursions: Sequence[GuidingExcursion],
    dithers: Sequence[DitherEvent],
    count: int = RECENT_EXPOSURE_COUNT,
) -> list[ExposureSummary]:
    """Summarize the latest exposures and flag the ones that look ruined.

    Parameters
    ----------
    captures : `Sequence` [`EkosCapture`]
        Finished exposures, in time order.
    guide_stats : `Sequence` [`EkosGuideStat`]
        All guiding measurements.
    excursions : `Sequence` [`GuidingExcursion`]
        Excursions already found in the session.
    dithers : `Sequence` [`DitherEvent`]
        Dithers read from the KStars log.
    count : `int`, optional
        How many of the latest exposures to return.

    Returns
    -------
    exposures : `list` [`ExposureSummary`]
        The latest `count` exposures, oldest first.
    """
    summaries = []
    for index in range(max(0, len(captures) - count), len(captures)):
        capture = captures[index]
        started_at = capture.completed_at - capture.exposure_seconds
        inside = [s for s in guide_stats if started_at <= s.timestamp <= capture.completed_at]
        early = [_total_error(s) for s in inside if s.timestamp < started_at + EARLY_EXPOSURE_SECONDS]
        rest = [_total_error(s) for s in inside if s.timestamp >= started_at + EARLY_EXPOSURE_SECONDS]

        flags: list[str] = []
        earlier_star_counts = [c.star_count for c in captures[max(0, index - 20) : index]]
        if len(earlier_star_counts) >= MINIMUM_FRAMES_FOR_STAR_BASELINE:
            usual = statistics.median(earlier_star_counts)
            if usual > 0 and capture.star_count < LOW_STAR_FRACTION * usual:
                flags.append(
                    f"Only {capture.star_count} stars against a usual {usual:.0f}: likely trailed or clouded."
                )
        for excursion in excursions:
            if excursion.ended_at >= started_at and excursion.started_at <= capture.completed_at:
                peak = excursion.peak_error_arcsec
                flags.append(f"A guiding excursion (peak {peak:.0f} arcsec) overlapped this exposure.")
                break

        summaries.append(
            ExposureSummary(
                frame_name=capture.file_path.rsplit("/", 1)[-1],
                completed_at=capture.completed_at,
                exposure_seconds=capture.exposure_seconds,
                star_count=capture.star_count,
                half_flux_radius_px=capture.half_flux_radius_px,
                eccentricity=capture.eccentricity,
                guide_rms_first_seconds_arcsec=_rms(early),
                guide_rms_rest_arcsec=_rms(rest),
                follows_dither=any(0.0 <= started_at - d.timestamp <= DITHER_FOLLOW_SECONDS for d in dithers),
                flags=flags,
            )
        )
    return summaries


def summarize_live_session(
    context: EkosSessionContext,
    guide_stats: Sequence[EkosGuideStat],
    dithers: Sequence[DitherEvent],
    analyze_file: str,
    kstars_log_file: str | None = None,
    window_seconds: float = 600.0,
    wall_clock_now: float | None = None,
) -> LiveSessionStatus:
    """Build the status of the current session from its Ekos records.

    Parameters
    ----------
    context : `EkosSessionContext`
        The parsed analyze log, without the guiding samples.
    guide_stats : `Sequence` [`EkosGuideStat`]
        The guiding measurements from the same log.
    dithers : `Sequence` [`DitherEvent`]
        Dithers read from the KStars log; empty if no log was available.
    analyze_file, kstars_log_file : `str`
        The files the records came from, for the report.
    window_seconds : `float`, optional
        Length of the recent-guiding window. Default is ten minutes.
    wall_clock_now : `float`, optional
        Current time, to measure how stale the file is. Defaults to the
        system clock; tests pass a fixed value.

    Returns
    -------
    status : `LiveSessionStatus`
        The session's current state and any problems found.
    """
    timestamps = [context.ended_at]
    if guide_stats:
        timestamps.append(guide_stats[-1].timestamp)
    as_of = max(timestamps)
    now = time.time() if wall_clock_now is None else wall_clock_now

    declination = context.mount_positions[-1].declination_degrees if context.mount_positions else None
    reacquire_times = [e.timestamp for e in context.guide_state_events if e.state == "Reacquiring"]
    excursions = find_guiding_excursions(guide_stats, reacquire_times, declination)
    exposures = summarize_exposures(context.captures, guide_stats, excursions, dithers)

    successful_focus = [run for run in context.autofocus_runs if run.succeeded]
    last_focus = successful_focus[-1] if successful_focus else None
    last_guide_state = context.guide_state_events[-1] if context.guide_state_events else None

    flags: list[str] = []
    for excursion in reversed(excursions):
        peak = excursion.peak_error_arcsec
        text = (
            f"Guiding {excursion.kind}: {excursion.axis} error peaked at {peak:.0f} arcsec"
            f" with {excursion.reacquire_count} re-lock(s)."
        )
        if excursion.kind == "drift" and excursion.matches_stopped_ra_axis:
            text += (
                f" The {excursion.drift_rate_arcsec_per_second:.1f} arcsec/s drift matches an RA axis that"
                " stopped tracking."
            )
        flags.append(text)
    finished_dithers = [d for d in dithers if d.succeeded is not None]
    failed = [d for d in finished_dithers if d.succeeded is False]
    if failed:
        flags.append(f"{len(failed)} of {len(finished_dithers)} dithers failed.")
    flagged_frames = [e.frame_name for e in exposures if e.flags]
    if flagged_frames:
        flags.append(f"Frames to check: {', '.join(flagged_frames)}.")
    if last_guide_state is not None and last_guide_state.state != "Guiding":
        flags.append(f"The guider is in state {last_guide_state.state!r}, not Guiding.")
    silence = now - as_of
    if silence > STALE_RECORD_SECONDS:
        flags.append(
            f"No new record for {silence / 60:.0f} minutes: Ekos may have stopped, or the file is stale."
        )

    return LiveSessionStatus(
        analyze_file=analyze_file,
        kstars_log_file=kstars_log_file,
        as_of=as_of,
        seconds_since_last_record=silence,
        guider_state=last_guide_state.state if last_guide_state else None,
        guider_state_since=last_guide_state.timestamp if last_guide_state else None,
        declination_degrees=declination,
        temperature_c=context.temperatures[-1].temperature_c if context.temperatures else None,
        recent_guiding=summarize_guiding_window(guide_stats, as_of, window_seconds),
        exposures=exposures,
        excursions=excursions,
        dithers=list(dithers),
        last_autofocus_at=last_focus.timestamp if last_focus else None,
        last_autofocus_temperature_c=last_focus.temperature_c if last_focus else None,
        flags=flags,
    )
