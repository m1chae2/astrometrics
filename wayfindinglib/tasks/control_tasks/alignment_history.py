"""Purpose: Read the recorded plate-solve alignment attempts.

Description: Every plate solve that checks the mount's pointing is
recorded as an alignment attempt: the centering loop of
``control.mount.slew(center=True)``, the syncs another program sends to
the mount, and the solves read back from downloaded frames. This module
holds the owners of `review.alignment.query` (the attempts, grouped into
one `AlignmentTargetSession` per target, with the latest polar alignment)
and `review.alignment.query_nights` (one summary per night, with the
night's tracking jitter). It reads the log database's
`get_alignment_sessions` and `get_session_alignment_attempts`, so
"alignment session" has one definition.

The average pointing of a night is a circular mean of right ascension.
A plain average of 359 and 1 degrees is 180 degrees, on the far side of
the sky; the circular mean is 0 degrees, which is right.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from astrometricslib.arguments import Page, TimeInput, check_include, to_epoch_seconds
from astrometricslib.exceptions import AstrometricsError, InvalidArgumentError
from wayfindinglib.analytics.alignment_sessions import (
    group_alignment_attempts,
    mean_position_deg,
    pooled_jitter_arcsec,
)
from wayfindinglib.models.session.alignment_records import AlignmentRecords
from wayfindinglib.models.session.telemetry import (
    AlignmentAttempt,
    AlignmentSessionSummary,
    PolarAlignmentStatus,
)

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext

__all__ = [
    "ALIGNMENT_PARTS",
    "ATTEMPT_STATUSES",
    "attempt_models",
    "polar_status_from_row",
    "query_alignment_nights",
    "query_alignment_records",
]

ALIGNMENT_PARTS = ("tracking",)
"""The heavy parts `query_alignment_records` can add."""

ATTEMPT_STATUSES = ("solving", "failed", "warning", "aligned", "idle")
"""The statuses an `AlignmentAttempt` can have. An older record with any
other status is shown as ``aligned``."""


def attempt_models(rows: list[dict[str, Any]]) -> list[AlignmentAttempt]:
    """Turn alignment log rows into `AlignmentAttempt` models.

    Parameters
    ----------
    rows : `list` [`dict` [`str`, `Any`]]
        Alignment log rows, oldest first.

    Returns
    -------
    attempts : `list` [`AlignmentAttempt`]
        One per row. A status the model does not know is shown as
        ``aligned``.
    """
    return [
        AlignmentAttempt(
            status=row.get("status") if row.get("status") in ATTEMPT_STATUSES else "aligned",
            delta_ra_arcsec=row.get("delta_ra_arcsec"),
            delta_dec_arcsec=row.get("delta_dec_arcsec"),
            ra=row.get("mount_ra"),
            dec=row.get("mount_dec"),
            pointing_error_arcsec=row.get("pointing_error_arcsec"),
            timestamp=row.get("timestamp"),
            target_name=row.get("target_name"),
            session_id=row.get("session_id"),
        )
        for row in rows
    ]


def _target_counts(context: ControlContext) -> dict[str, int]:
    """Count library targets with frames from each night.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the target catalog.

    Returns
    -------
    counts : `dict` [`str`, `int`]
        Night id to target count. Empty if the catalog cannot be read.
    """
    try:
        return dict(context.astrometrics.targets.count_by_night().targets_by_night)
    except AstrometricsError, sqlite3.Error:
        return {}


def query_alignment_nights(
    context: ControlContext, limit: int | None = None, offset: int = 0
) -> Page[AlignmentSessionSummary]:
    """List one alignment summary per recorded night, newest first.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the log database and the target catalog.
    limit : `int` or `None`, optional
        The most nights to return. `None` returns every night the log
        database summarizes (its 50 newest).
    offset : `int`, optional
        How many nights to skip first.

    Returns
    -------
    nights : `Page` [`AlignmentSessionSummary`]
        Each with the night's syncs, its targets, its times, its mean
        pointing error, its latest polar alignment errors, the mean
        pointing and the tracking jitter.

    Raises
    ------
    InvalidArgumentError
        If `limit` is below 1 or `offset` is below 0.
    """
    if limit is not None and limit < 1:
        raise InvalidArgumentError("limit must be at least 1, or None for every match.")
    if offset < 0:
        raise InvalidArgumentError("offset must be 0 or more.")
    logs = context.records
    rows = logs.get_alignment_sessions()
    end = len(rows) if limit is None else min(len(rows), offset + limit)
    target_counts = _target_counts(context)
    summaries = []
    for row in rows[offset:end]:
        attempts = attempt_models(logs.get_session_alignment_attempts(row["session_id"]))
        mean_ra, mean_dec = mean_position_deg([
            (a.ra, a.dec) for a in attempts if a.ra is not None and a.dec is not None
        ])
        summaries.append(
            AlignmentSessionSummary(
                session_id=row["session_id"],
                session_date=row["session_date"],
                sync_count=row.get("sync_count") or 0,
                target_count=target_counts.get(row["session_date"], 0),
                start_time=row.get("start_time"),
                end_time=row.get("end_time"),
                avg_error_arcsec=row.get("avg_error_arcsec"),
                polar_error_arcsec=row.get("polar_error_arcsec"),
                polar_alt_error_arcsec=row.get("polar_alt_error_arcsec"),
                polar_az_error_arcsec=row.get("polar_az_error_arcsec"),
                mean_ra_deg=mean_ra,
                mean_dec_deg=mean_dec,
                rms_jitter_arcsec=pooled_jitter_arcsec(group_alignment_attempts(attempts)),
            )
        )
    return Page(items=tuple(summaries), total=len(rows), next_offset=end if end < len(rows) else None)


def polar_status_from_row(row: dict[str, Any]) -> PolarAlignmentStatus:
    """Turn one stored polar alignment row into a `PolarAlignmentStatus`.

    Parameters
    ----------
    row : `dict` [`str`, `Any`]
        A row of the stored polar alignment runs.

    Returns
    -------
    status : `PolarAlignmentStatus`
        The run. A status the model does not know reads as ``aligned``.
    """
    status = row.get("status", "aligned")
    return PolarAlignmentStatus(
        status=status if status in ("idle", "in_progress", "aligned", "warning") else "aligned",
        total_error_arcsec=row.get("total_error_arcsec"),
        alt_error_arcsec=row.get("alt_error_arcsec"),
        az_error_arcsec=row.get("az_error_arcsec"),
        pole_ra=row.get("pole_ra"),
        pole_dec=row.get("pole_dec"),
        paa_points=row.get("paa_points", []),
        timestamp=row.get("timestamp"),
    )


def query_alignment_records(
    context: ControlContext,
    night: str | None = None,
    since: TimeInput | None = None,
    until: TimeInput | None = None,
    limit: int | None = None,
    include: list[str] | None = None,
) -> AlignmentRecords:
    """Read the stored alignment attempts, grouped by target.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the log database and, for ``"tracking"``, the targets.
    night : `str`, optional
        Only this observing night, such as ``"2026-09-24"``. Every night
        when omitted.
    since, until : `str`, `datetime` or `astropy.time.Time`, optional
        Only attempts from this time to that one. Text is ISO 8601; no
        time zone means UTC.
    limit : `int` or `None`, optional
        Keep only the newest this many attempts. `None` keeps every one.
    include : `list` [`str`], optional
        ``["tracking"]`` adds a tracking row for each library target
        imaged in the time covered, built from the guiding samples near
        its frames.

    Returns
    -------
    records : `AlignmentRecords`
        The attempts, oldest first, their grouping by target, and the
        latest polar alignment run of the night (of any night when no
        night is given). A status the attempt model does not know reads as
        ``aligned``.

    Raises
    ------
    InvalidArgumentError
        If `limit` is below 1, a time cannot be read, or `include` names an
        unknown part.
    """
    if limit is not None and limit < 1:
        raise InvalidArgumentError("limit must be at least 1, or None for every match.")
    parts = check_include(include, ALIGNMENT_PARTS)
    since_s, until_s = to_epoch_seconds(since, "since"), to_epoch_seconds(until, "until")
    records = context.records
    if night is None and limit is not None and until_s is None:
        # The newest rows after `since` are the newest rows, cut at `since`.
        rows = list(reversed(records.get_alignment_attempts(limit=limit)))
    else:
        rows = records.get_session_alignment_attempts(night, limit=None)
    rows = [
        row
        for row in rows
        if (since_s is None or (row.get("timestamp") or 0) >= since_s)
        and (until_s is None or (row.get("timestamp") or 0) <= until_s)
    ]
    if limit is not None:
        rows = rows[-limit:]
    attempts = attempt_models(rows)
    if "tracking" in parts:
        targets = context.astrometrics.targets.query(include_empty=True).items
        attempts.extend(
            AlignmentAttempt.model_validate(row)
            for row in records.get_session_target_telemetry(night, targets)
        )
    runs = records.get_polar_alignments(session_id=night, limit=1)
    polar = polar_status_from_row(runs[0]) if runs else None
    return AlignmentRecords(
        attempts=attempts, targets=group_alignment_attempts(attempts), polar_alignment=polar
    )
