"""Purpose: Read the recorded plate-solve alignment attempts, night by night.

Description: Every plate solve that checks the mount's pointing is
recorded as an alignment attempt: the centering loop of
``control.mount.slew(center=True)``, the syncs another program sends to
the mount, and the solves read back from downloaded frames. This module
answers `control.history.query(kind="alignment")` from those records,
through the log database's `get_alignment_sessions` and
`get_session_alignment_attempts`, so "alignment session" has one
definition.

Without a night, it lists one summary per night, with the night's
tracking jitter. With a night, it lists that night's attempts, the same
attempts grouped into one `AlignmentTargetSession` per target (with
jitter and drift rates), and its latest polar alignment measurement.

The average pointing of a night is a circular mean of right ascension.
A plain average of 359 and 1 degrees is 180 degrees, on the far side of
the sky; the circular mean is 0 degrees, which is right.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from astrometricslib import AstrometricsError, NotFoundError
from wayfindinglib.analytics.alignment_sessions import (
    group_alignment_attempts,
    mean_position_deg,
    pooled_jitter_arcsec,
)
from wayfindinglib.models.session.telemetry import AlignmentAttempt, AlignmentSessionSummary

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext

__all__ = ["ATTEMPT_STATUSES", "alignment_night", "alignment_nights"]

ATTEMPT_STATUSES = ("solving", "failed", "warning", "aligned", "idle")
"""The statuses an `AlignmentAttempt` can have. An older record with any
other status is shown as ``aligned``."""


def _attempt_models(rows: list[dict[str, Any]]) -> list[AlignmentAttempt]:
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
        return context.astrometrics.targets.query(detail="nights")["nights"]
    except AstrometricsError, KeyError, sqlite3.Error:
        return {}


def alignment_nights(context: ControlContext, limit: int) -> dict[str, Any]:
    """List one alignment summary per recorded night, newest first.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the log database and the target catalog.
    limit : `int`
        How many nights to list.

    Returns
    -------
    reply : `dict` [`str`, `Any`]
        ``kind``, ``total``, ``shown`` and ``sessions``: each with
        ``sessionId``, ``sessionDate``, ``syncCount``, ``targetCount``,
        ``startTime``, ``endTime``, ``avgErrorArcsec``, the polar
        alignment errors, the mean pointing ``meanRaDeg`` and
        ``meanDecDeg``, and the tracking jitter ``rmsJitterArcsec``.
    """
    logs = context.logger_interface
    rows = logs.get_alignment_sessions()
    target_counts = _target_counts(context)
    sessions = []
    for row in rows[:limit]:
        attempts = _attempt_models(logs.get_session_alignment_attempts(row["session_id"]))
        mean_ra, mean_dec = mean_position_deg([
            (a.ra, a.dec) for a in attempts if a.ra is not None and a.dec is not None
        ])
        summary = AlignmentSessionSummary(
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
        sessions.append(summary.model_dump(mode="json", by_alias=True))
    return {"kind": "alignment", "total": len(rows), "shown": len(sessions), "sessions": sessions}


def alignment_night(context: ControlContext, session_id: str) -> dict[str, Any]:
    """List one night's attempts, grouped by target, and its polar alignment.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the log database.
    session_id : `str`
        The night, named for the local date it began.

    Returns
    -------
    reply : `dict` [`str`, `Any`]
        ``kind``, ``session_id``, ``attempts`` (in time order, each an
        `AlignmentAttempt` in its camelCase form), ``targets`` (one
        `AlignmentTargetSession` per target, with jitter and drift rates)
        and ``polar_alignment`` (the latest measurement that night, or
        `None`).

    Raises
    ------
    NotFoundError
        If nothing is recorded for that night.
    """
    logs = context.logger_interface
    rows = logs.get_session_alignment_attempts(session_id)
    if not rows:
        raise NotFoundError(
            f"No alignment attempts are recorded for night {session_id!r}.",
            details={"session_id": session_id},
        )
    attempts = _attempt_models(rows)
    polar = logs.get_polar_alignment_logs(session_id=session_id, limit=1)
    return {
        "kind": "alignment",
        "session_id": session_id,
        "attempts": [a.model_dump(mode="json", by_alias=True) for a in attempts],
        "targets": [
            target.model_dump(mode="json", by_alias=True, exclude={"attempts"})
            for target in group_alignment_attempts(attempts)
        ],
        "polar_alignment": polar[0] if polar else None,
    }
