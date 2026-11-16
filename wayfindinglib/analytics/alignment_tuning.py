"""Purpose: Derive the plate-solve grouping limits from the mount's own solves.

Description: `alignment_sessions` groups the stored plate solves into targets
and runs, and decides which solves count as tracking. Its limits (how far a
named target's solves spread, how long a pause ends a run, how far off a
solve may be and still be tracking, and how far apart solves are when they
carry no time) are properties of how this mount is used. This module reads
every stored solve and sets each limit from the solves' own spread:

- Solves are grouped by their stored target name and observing night.
- The pause limit is the 99th percentile of the pauses between neighboring
  solves of one target on one night.
- The spacing assumed for solves with no time is the median of those pauses
  that stay inside a run.
- The slew limit is the 99th percentile of the pointing errors of the solves
  that follow the settling solve of a run, that is, of the tracking solves.
- The same-target radius is the 99th percentile of how far a named solve lies
  from the first solve of its target on its night.

A drift is kept only when it is significant (`DRIFT_SIGNIFICANCE_SIGMAS`), a
statistical rule rather than a tuned value, so it is not derived here.

The statistics are pure functions of plain rows, so a test needs no library.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections.abc import Sequence
from typing import Any

from astrometricslib.parameters import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    Tuner,
    TuningInputs,
    TuningResult,
    TuningSkippedError,
    resolve_parameter,
)
from wayfindinglib.analytics.alignment_sessions import (
    ASSUMED_CADENCE_SECONDS,
    RUN_GAP_SECONDS,
    SAME_TARGET_RADIUS_DEG,
    SLEW_ERROR_ARCSEC,
    _separation_deg,
)
from wayfindinglib.analytics.tuning_support import night_of, percentile, records_of

__all__ = [
    "ALIGNMENT_COVERAGE_PERCENT",
    "ALIGNMENT_GROUPING_TUNER",
    "MINIMUM_SOLVES_PER_RUN_FOR_SETTLING",
    "group_by_target_and_night",
    "pauses_between_nights",
    "pauses_within_nights",
    "radius_distances_deg",
    "split_into_runs",
    "tracking_errors_arcsec",
]

ALIGNMENT_COVERAGE_PERCENT = TunedParameter(
    name="wayfinding.alignment_coverage_percent",
    default=99.0,
    unit="percent",
    description="Share of ordinary plate-solve cases that a grouping limit must cover.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="WAY-D123",
)
"""Share of ordinary plate-solve cases that a grouping limit must cover.

A limit at the 99th percentile leaves one case in a hundred outside it, which
is the rate at which a stray solve (a mistyped target name, a pause for
clouds) may be called unusual. Register entry WAY-D123."""

MINIMUM_SOLVES_PER_RUN_FOR_SETTLING = TunedParameter(
    name="wayfinding.minimum_solves_per_run_for_settling",
    default=3,
    unit="solves",
    description="A run of at least this many solves loses its first solve as the settling slew.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="WAY-D123",
)
"""A run of at least this many solves loses its first solve as the settling
slew. It is the rule `alignment_sessions` uses for its jitter, so the slew
limit is measured on the same solves the jitter is. Register entry WAY-D123."""


def _timed_named_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the rows that name a target and carry a time.

    Parameters
    ----------
    rows : `~collections.abc.Sequence` [`dict`]
        Stored alignment rows.

    Returns
    -------
    kept : `list` [`dict`]
        Rows with a non-blank ``target_name`` and a ``timestamp``, by time.
    """
    kept = [
        row for row in rows if str(row.get("target_name") or "").strip() and row.get("timestamp") is not None
    ]
    return sorted(kept, key=lambda row: row["timestamp"])


def group_by_target_and_night(rows: Sequence[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Group named, timed solves by target name and observing night.

    Parameters
    ----------
    rows : `~collections.abc.Sequence` [`dict`]
        Stored alignment rows.

    Returns
    -------
    groups : `dict`
        Solves by ``(target name, night)``, each list oldest first.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in _timed_named_rows(rows):
        night = night_of(row)
        if night is None:
            continue
        groups.setdefault((str(row["target_name"]).strip(), night), []).append(row)
    return groups


def pauses_within_nights(groups: dict[tuple[str, str], list[dict[str, Any]]]) -> list[float]:
    """List the pauses between neighboring solves of one target on one night.

    Parameters
    ----------
    groups : `dict`
        Solves by ``(target name, night)``, from
        `group_by_target_and_night`.

    Returns
    -------
    pauses : `list` [`float`]
        The time between each solve and the next, in seconds.
    """
    pauses = []
    for solves in groups.values():
        for earlier, later in itertools.pairwise(solves):
            pauses.append(float(later["timestamp"]) - float(earlier["timestamp"]))
    return pauses


def pauses_between_nights(groups: dict[tuple[str, str], list[dict[str, Any]]]) -> list[float]:
    """List the pauses between one night's solves and the next night's.

    Parameters
    ----------
    groups : `dict`
        Solves by ``(target name, night)``.

    Returns
    -------
    pauses : `list` [`float`]
        For each target seen on two or more nights, the time from the end of
        one night's solves to the start of the next night's, in seconds.
    """
    by_target: dict[str, list[tuple[float, float]]] = {}
    for (target, _night), solves in groups.items():
        by_target.setdefault(target, []).append((
            float(solves[0]["timestamp"]),
            float(solves[-1]["timestamp"]),
        ))
    pauses = []
    for spans in by_target.values():
        spans.sort()
        for earlier, later in itertools.pairwise(spans):
            pauses.append(later[0] - earlier[1])
    return pauses


def split_into_runs(
    solves: Sequence[dict[str, Any]], pause_limit_seconds: float
) -> list[list[dict[str, Any]]]:
    """Split one target's solves into runs at pauses longer than a limit.

    Parameters
    ----------
    solves : `~collections.abc.Sequence` [`dict`]
        One target's timed solves, oldest first.
    pause_limit_seconds : `float`
        A pause longer than this starts a new run.

    Returns
    -------
    runs : `list` [`list` [`dict`]]
        The runs, oldest first. Empty when `solves` is empty.
    """
    runs: list[list[dict[str, Any]]] = []
    for solve in solves:
        if runs and float(solve["timestamp"]) - float(runs[-1][-1]["timestamp"]) <= pause_limit_seconds:
            runs[-1].append(solve)
        else:
            runs.append([solve])
    return runs


def _pointing_error(row: dict[str, Any]) -> float | None:
    """Return a solve's pointing error, from its offsets if not stored.

    Parameters
    ----------
    row : `dict`
        A stored alignment row.

    Returns
    -------
    error_arcsec : `float` or `None`
        The error, or `None` when neither the error nor both offsets exist.
    """
    stored = row.get("pointing_error_arcsec")
    if stored is not None:
        return float(stored)
    d_ra, d_dec = row.get("delta_ra_arcsec"), row.get("delta_dec_arcsec")
    if d_ra is None or d_dec is None:
        return None
    return math.hypot(float(d_ra), float(d_dec))


def tracking_errors_arcsec(
    groups: dict[tuple[str, str], list[dict[str, Any]]], pause_limit_seconds: float
) -> list[float]:
    """List the pointing errors of the solves that follow a run's first solve.

    The first solve of a run of three or more is the settling slew and is left
    out, as `alignment_sessions` leaves it out of the jitter. Runs shorter than
    that give no tracking error, because their only solves may still be the
    slew.

    Parameters
    ----------
    groups : `dict`
        Solves by ``(target name, night)``.
    pause_limit_seconds : `float`
        The pause that ends a run.

    Returns
    -------
    errors : `list` [`float`]
        The pointing errors in arcseconds.
    """
    errors: list[float] = []
    for solves in groups.values():
        for run in split_into_runs(solves, pause_limit_seconds):
            if len(run) < resolve_parameter(MINIMUM_SOLVES_PER_RUN_FOR_SETTLING).value:
                continue
            for solve in run[1:]:
                error = _pointing_error(solve)
                if error is not None:
                    errors.append(error)
    return errors


def radius_distances_deg(groups: dict[tuple[str, str], list[dict[str, Any]]]) -> list[float]:
    """List how far named solves lie from their group's first solve.

    Parameters
    ----------
    groups : `dict`
        Solves by ``(target name, night)``.

    Returns
    -------
    distances : `list` [`float`]
        For each solve after the first of its group that has a position, the
        distance from the first solve's position, in degrees.
    """
    distances = []
    for solves in groups.values():
        located = [
            solve
            for solve in solves
            if solve.get("mount_ra") is not None
            and solve.get("mount_dec") is not None
            and not math.isnan(solve["mount_ra"])
            and not math.isnan(solve["mount_dec"])
        ]
        if len(located) < 2:
            continue
        first = located[0]
        for solve in located[1:]:
            distances.append(
                _separation_deg(
                    float(solve["mount_ra"]) % 360.0,
                    float(solve["mount_dec"]),
                    float(first["mount_ra"]) % 360.0,
                    float(first["mount_dec"]),
                )
            )
    return distances


def tune_alignment_grouping(inputs: TuningInputs) -> TuningResult:
    """Derive the plate-solve grouping limits from every stored solve.

    Parameters
    ----------
    inputs : `TuningInputs`
        The tuning run's inputs. Reads the ``"wayfinder"`` service.

    Returns
    -------
    result : `TuningResult`
        The pause limit, the assumed spacing, the slew limit and the
        same-target radius. A value whose data are too thin is left out.

    Raises
    ------
    TuningSkippedError
        If no stored solve carries a target name and a time.
    """
    rows = records_of(inputs.service("wayfinder")).get_session_alignment_attempts("all", limit=10_000_000)
    groups = group_by_target_and_night(rows)
    within = pauses_within_nights(groups)
    if not within:
        raise TuningSkippedError("no target has two stored plate solves on one night")
    coverage = resolve_parameter(ALIGNMENT_COVERAGE_PERCENT).value

    values: dict[str, float] = {}
    counts: list[int] = []
    pause_limit = percentile(within, coverage)
    between = pauses_between_nights(groups)
    if between and pause_limit >= min(between):
        raise TuningSkippedError(
            "pauses inside a night are as long as the gap between nights, so runs cannot be told apart"
        )
    values[RUN_GAP_SECONDS.name] = pause_limit
    inside_runs = [pause for pause in within if pause <= pause_limit]
    values[ASSUMED_CADENCE_SECONDS.name] = statistics.median(inside_runs)
    counts.append(len(within))

    errors = tracking_errors_arcsec(groups, pause_limit)
    if errors:
        values[SLEW_ERROR_ARCSEC.name] = percentile(errors, coverage)
        counts.append(len(errors))
    distances = radius_distances_deg(groups)
    if distances:
        values[SAME_TARGET_RADIUS_DEG.name] = percentile(distances, coverage)
        counts.append(len(distances))

    return TuningResult(
        values=values,
        sample=min(counts),
        source=(
            f"The {coverage:g}th percentile of the pauses ({len(within)}), pointing errors "
            f"({len(errors)}) and target spreads ({len(distances)}) in {len(groups)} target-nights of "
            "stored plate solves; the spacing is the median pause inside a run."
        ),
    )


ALIGNMENT_GROUPING_TUNER = Tuner(
    name="wayfinding.alignment_grouping",
    parameters=(RUN_GAP_SECONDS, ASSUMED_CADENCE_SECONDS, SLEW_ERROR_ARCSEC, SAME_TARGET_RADIUS_DEG),
    method=(
        "Reads every stored plate solve of the mount and groups the named ones by target and night. "
        "Takes the 99th percentile of the pauses between neighboring solves (the run gap), of the pointing "
        "errors after each run's settling solve (the slew limit) and of the distance from a target's first "
        "solve (the same-target radius), and the median pause inside a run (the assumed spacing)."
    ),
    tune=tune_alignment_grouping,
)
