"""Purpose: Group plate-solve alignment attempts into one session per target.

Description: Every plate solve that checks the mount's pointing is an
`AlignmentAttempt`. This module groups them by target and works out how
well the mount tracked each one:

- **Grouping.** Solves with the same target name form one group. A solve
  without a name joins the group whose first solve lies within 0.5 degrees
  of it, or starts a new group.
- **Runs.** Inside a group, a gap of more than two hours starts a new run,
  so two nights on one target are not counted as one long exposure.
- **Jitter.** Coarse solves (status ``warning``, or more than 120
  arcseconds off) are slews, not tracking, and are left out when there are
  others. In a run of three or more solves, the first is the settling
  slew and is also left out. Jitter is the root-mean-square (RMS) scatter
  of the remaining offsets around the run's own mean offset, pooled over
  runs. A deliberate framing offset therefore does not count as error.
- **Drift.** A least-squares straight line through the offsets against
  time, in arcseconds per minute, for groups that span more than ten
  seconds.
- **Average position.** Right ascension (RA) is averaged on the circle,
  so solves at 359 and 1 degrees average to 0 degrees, not 180.

It is a pure function of its input. `AlignmentTargetSession.from_attempts`
is the public way to call it.
"""

from __future__ import annotations

import itertools
import math

from wayfindinglib.models.session.telemetry import (
    AlignmentAttempt,
    AlignmentTargetSession,
    AlignmentTrackPoint,
)

__all__ = ["group_alignment_attempts", "mean_position_deg", "pooled_jitter_arcsec"]

SAME_TARGET_RADIUS_DEG = 0.5
"""An unnamed solve within this distance of a group's first solve joins it."""

RUN_GAP_SECONDS = 7200.0
"""A gap longer than this (two hours) between solves starts a new run."""

SLEW_ERROR_ARCSEC = 120.0
"""A solve further off than this is a slew, not tracking."""

ASSUMED_CADENCE_SECONDS = 5.0
"""Spacing assumed between solves that carry no time."""

MINIMUM_DRIFT_SPAN_SECONDS = 10.0
"""A drift rate is fitted only over more time than this."""


def mean_position_deg(positions: list[tuple[float, float]]) -> tuple[float | None, float | None]:
    """Return the average sky position, wrapping right ascension at 0/360.

    Parameters
    ----------
    positions : `list` [`tuple` [`float`, `float`]]
        Right ascension and declination pairs, in degrees.

    Returns
    -------
    ra_deg, dec_deg : `float` or `None`
        The circular mean of right ascension (0 to 360) and the plain mean
        of declination, or `None` for an empty list.
    """
    if not positions:
        return None, None
    sin_sum = sum(math.sin(math.radians(ra)) for ra, _ in positions)
    cos_sum = sum(math.cos(math.radians(ra)) for ra, _ in positions)
    ra_deg = math.degrees(math.atan2(sin_sum, cos_sum)) % 360.0
    dec_deg = sum(dec for _, dec in positions) / len(positions)
    return ra_deg, dec_deg


def _total_error(attempt: AlignmentAttempt) -> float:
    """Return a solve's pointing error, from its offsets if not recorded.

    Parameters
    ----------
    attempt : `AlignmentAttempt`
        One solve.

    Returns
    -------
    error_arcsec : `float`
        The pointing error in arcseconds.
    """
    if attempt.pointing_error_arcsec is not None:
        return attempt.pointing_error_arcsec
    return math.hypot(attempt.delta_ra_arcsec or 0.0, attempt.delta_dec_arcsec or 0.0)


def _separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Return the small-angle distance between two sky positions.

    Parameters
    ----------
    ra1, dec1 : `float`
        The first position, in degrees.
    ra2, dec2 : `float`
        The second position, in degrees.

    Returns
    -------
    distance_deg : `float`
        The distance in degrees. The RA difference is wrapped to
        -180..180 degrees and scaled by the cosine of `dec2`.
    """
    d_ra = (ra1 - ra2 + 180.0) % 360.0 - 180.0
    return math.hypot(d_ra * math.cos(math.radians(dec2)), dec1 - dec2)


def _usable(attempts: list[AlignmentAttempt]) -> list[AlignmentAttempt]:
    """Keep solves with a position, drop repeats, and sort them by time.

    Parameters
    ----------
    attempts : `list` [`AlignmentAttempt`]
        Solves in any order.

    Returns
    -------
    usable : `list` [`AlignmentAttempt`]
        Solves with RA and declination, each time-and-target pair once,
        oldest first. Solves without a time sort first.
    """
    seen: set[tuple[object, ...]] = set()
    usable = []
    for attempt in attempts:
        if attempt.ra is None or attempt.dec is None or math.isnan(attempt.ra) or math.isnan(attempt.dec):
            continue
        key: tuple[object, ...] = (
            (attempt.timestamp, attempt.target_name or "")
            if attempt.timestamp is not None
            else (attempt.ra, attempt.dec)
        )
        if key in seen:
            continue
        seen.add(key)
        usable.append(attempt)
    usable.sort(key=lambda attempt: attempt.timestamp or 0.0)
    return usable


def _group_by_target(attempts: list[AlignmentAttempt]) -> list[list[AlignmentAttempt]]:
    """Split time-ordered solves into one group per target.

    Parameters
    ----------
    attempts : `list` [`AlignmentAttempt`]
        Usable solves, oldest first.

    Returns
    -------
    groups : `list` [`list` [`AlignmentAttempt`]]
        One list per target, in the order the targets first appear.
    """
    groups: dict[str, list[AlignmentAttempt]] = {}
    for attempt in attempts:
        key = (attempt.target_name or "").strip()
        if not key:
            ra, dec = attempt.ra % 360.0, attempt.dec
            nearest, nearest_distance = None, SAME_TARGET_RADIUS_DEG
            for existing_key, group in groups.items():
                distance = _separation_deg(ra, dec, group[0].ra % 360.0, group[0].dec)
                if distance < nearest_distance:
                    nearest, nearest_distance = existing_key, distance
            key = nearest or f"Target_{attempt.ra:.2f}_{attempt.dec:.2f}"
        groups.setdefault(key, []).append(attempt)
    return list(groups.values())


def _split_runs(attempts: list[AlignmentAttempt]) -> list[list[AlignmentAttempt]]:
    """Split one target's solves into runs at gaps of more than two hours.

    Parameters
    ----------
    attempts : `list` [`AlignmentAttempt`]
        One target's solves, oldest first. Must not be empty.

    Returns
    -------
    runs : `list` [`list` [`AlignmentAttempt`]]
        The runs, oldest first.
    """
    runs = [[attempts[0]]]
    for previous, current in itertools.pairwise(attempts):
        gap = (current.timestamp or 0.0) - (previous.timestamp or 0.0)
        if previous.timestamp is not None and current.timestamp is not None and gap > RUN_GAP_SECONDS:
            runs.append([current])
        else:
            runs[-1].append(current)
    return runs


def _jitter(runs: list[list[AlignmentAttempt]]) -> tuple[float, float, float]:
    """Pool the scatter around each run's mean offset over all runs.

    Parameters
    ----------
    runs : `list` [`list` [`AlignmentAttempt`]]
        Tracking runs of one target.

    Returns
    -------
    elapsed_seconds, rms_ra, rms_dec : `float`
        Time spent across runs of two or more solves, and the pooled RMS
        scatter in RA and declination, in arcseconds.
    """
    elapsed = 0.0
    sum_var_ra = sum_var_dec = 0.0
    frames = 0
    for run in runs:
        if len(run) < 2:
            continue
        start, end = run[0].timestamp or 0.0, run[-1].timestamp or 0.0
        elapsed += end - start if end >= start else (len(run) - 1) * ASSUMED_CADENCE_SECONDS
        settled = run[1:] if len(run) >= 3 else run
        count = len(settled)
        mean_ra = sum(a.delta_ra_arcsec or 0.0 for a in settled) / count
        mean_dec = sum(a.delta_dec_arcsec or 0.0 for a in settled) / count
        sum_var_ra += sum(((a.delta_ra_arcsec or 0.0) - mean_ra) ** 2 for a in settled)
        sum_var_dec += sum(((a.delta_dec_arcsec or 0.0) - mean_dec) ** 2 for a in settled)
        frames += count
    if frames == 0:
        return elapsed, 0.0, 0.0
    return elapsed, math.sqrt(sum_var_ra / frames), math.sqrt(sum_var_dec / frames)


def _drift(points: list[AlignmentTrackPoint], elapsed_seconds: float) -> tuple[float, float]:
    """Fit straight lines to the offsets against time.

    Parameters
    ----------
    points : `list` [`AlignmentTrackPoint`]
        The target's solves.
    elapsed_seconds : `float`
        Time spent tracking. No drift is fitted for ten seconds or less.

    Returns
    -------
    drift_ra, drift_dec : `float`
        The slopes in arcseconds per minute, or 0 when they cannot be fitted.
    """
    if len(points) < 2 or elapsed_seconds <= MINIMUM_DRIFT_SPAN_SECONDS:
        return 0.0, 0.0
    count = len(points)
    minutes = [p.elapsed_seconds / 60.0 for p in points]
    sum_t = sum(minutes)
    sum_tt = sum(t * t for t in minutes)
    denominator = count * sum_tt - sum_t * sum_t
    if abs(denominator) <= 1e-6:
        return 0.0, 0.0
    sum_ra = sum(p.delta_ra_arcsec for p in points)
    sum_dec = sum(p.delta_dec_arcsec for p in points)
    sum_t_ra = sum(t * p.delta_ra_arcsec for t, p in zip(minutes, points, strict=True))
    sum_t_dec = sum(t * p.delta_dec_arcsec for t, p in zip(minutes, points, strict=True))
    return (
        (count * sum_t_ra - sum_t * sum_ra) / denominator,
        (count * sum_t_dec - sum_t * sum_dec) / denominator,
    )


def _summarize(group: list[AlignmentAttempt], index: int) -> AlignmentTargetSession:
    """Work out one target's statistics.

    Parameters
    ----------
    group : `list` [`AlignmentAttempt`]
        The target's solves, oldest first.
    index : `int`
        The group's position, used in its id and in names for unnamed targets.

    Returns
    -------
    session : `AlignmentTargetSession`
        The target's summary.
    """
    first, last = group[0], group[-1]
    fallback_name = f"Target Session #{index + 1}" if len(group) > 1 else f"Sync #{index + 1}"
    mean_ra, mean_dec = mean_position_deg([(a.ra, a.dec) for a in group])

    tracking = [a for a in group if a.status != "warning" and _total_error(a) <= SLEW_ERROR_ARCSEC]
    elapsed, rms_ra, rms_dec = _jitter(_split_runs(tracking or group))
    if elapsed <= 0:
        elapsed = (len(group) - 1) * ASSUMED_CADENCE_SECONDS

    t0 = first.timestamp or 0.0
    points = []
    for position, attempt in enumerate(group):
        timestamp = (
            attempt.timestamp if attempt.timestamp is not None else t0 + position * ASSUMED_CADENCE_SECONDS
        )
        points.append(
            AlignmentTrackPoint(
                elapsed_seconds=timestamp - t0,
                delta_ra_arcsec=attempt.delta_ra_arcsec or 0.0,
                delta_dec_arcsec=attempt.delta_dec_arcsec or 0.0,
                total_error_arcsec=_total_error(attempt),
                timestamp=timestamp,
            )
        )
    drift_ra, drift_dec = _drift(points, elapsed)

    return AlignmentTargetSession(
        id=f"session_cluster_{index}",
        target_name=first.target_name or fallback_name,
        mean_ra_deg=mean_ra,
        mean_dec_deg=mean_dec,
        frame_count=len(group),
        initial_error_arcsec=_total_error(first),
        rms_ra_arcsec=rms_ra,
        rms_dec_arcsec=rms_dec,
        rms_total_arcsec=math.hypot(rms_ra, rms_dec),
        drift_ra_arcsec_per_min=drift_ra,
        drift_dec_arcsec_per_min=drift_dec,
        start_time=first.timestamp,
        end_time=last.timestamp,
        elapsed_seconds=elapsed,
        time_series=points,
        attempts=group,
    )


def group_alignment_attempts(attempts: list[AlignmentAttempt]) -> list[AlignmentTargetSession]:
    """Group plate solves into one session per target, with statistics.

    Parameters
    ----------
    attempts : `list` [`AlignmentAttempt`]
        Solves in any order.

    Returns
    -------
    sessions : `list` [`AlignmentTargetSession`]
        One per target, in the order the targets were first solved. Empty
        when no solve has a position.
    """
    groups = _group_by_target(_usable(attempts))
    return [_summarize(group, index) for index, group in enumerate(groups)]


def pooled_jitter_arcsec(sessions: list[AlignmentTargetSession]) -> float | None:
    """Combine the jitter of several targets, weighted by their solve counts.

    Parameters
    ----------
    sessions : `list` [`AlignmentTargetSession`]
        Target sessions, for example one night's.

    Returns
    -------
    jitter_arcsec : `float` or `None`
        The square root of the solve-weighted mean of the squared jitter
        over targets with two or more solves, or `None` if there are none.
    """
    tracked = [s for s in sessions if s.frame_count >= 2]
    frames = sum(s.frame_count for s in tracked)
    if frames == 0:
        return None
    return math.sqrt(sum(s.frame_count * s.rms_total_arcsec**2 for s in tracked) / frames)
