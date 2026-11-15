"""Purpose: Small helpers that the wayfindinglib parameter tuners share.

Description: A tuner (issue #175) derives a tuned value from this
observatory's own records. Most of them need the same few things: a
percentile of a list of numbers, a split of a list into "ordinary" values and
"unusual" ones, and a way to reach the stored records through a
`wayfindinglib.Wayfinder`. They live here so each tuner module holds only its
own method.

The functions are pure: they take plain numbers and return plain numbers. The
two record readers are the only functions that touch a `Wayfinder`.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from astrometricslib.parameters import TuningSkippedError

__all__ = [
    "butler_of",
    "largest_log_gap_threshold",
    "night_of",
    "percentile",
    "records_of",
]


def percentile(values: Sequence[float], percent: float) -> float:
    """Return a percentile of a list of numbers.

    Parameters
    ----------
    values : `~collections.abc.Sequence` [`float`]
        The numbers. Must not be empty.
    percent : `float`
        The percentile, from 0 to 100.

    Returns
    -------
    value : `float`
        The percentile, by linear interpolation between the sorted values.

    Raises
    ------
    ValueError
        If `values` is empty.
    """
    if len(values) == 0:
        raise ValueError("a percentile needs at least one value")
    return float(np.percentile(np.asarray(values, dtype=float), percent))


def largest_log_gap_threshold(values: Sequence[float], minimum_ratio: float) -> float | None:
    """Split positive numbers into a small group and a large group.

    Sort the numbers and find the two neighbors whose ratio is largest, that
    is, the widest gap on a logarithmic scale. The threshold is the geometric
    mean of those two neighbors, so it sits in the middle of the gap. Use it
    when a quantity has two kinds of cases (for example a re-centering step
    of a few arcminutes and a move to a new target of several degrees).

    Parameters
    ----------
    values : `~collections.abc.Sequence` [`float`]
        The numbers. Values that are not above 0 are ignored.
    minimum_ratio : `float`
        The smallest ratio between the two neighbors that counts as a real
        gap. With a narrower widest gap the two groups are not separate.

    Returns
    -------
    threshold : `float` or `None`
        The middle of the widest gap, or `None` when fewer than two usable
        numbers exist or the widest gap is narrower than `minimum_ratio`.
    """
    usable = sorted(value for value in values if value > 0.0 and math.isfinite(value))
    if len(usable) < 2:
        return None
    best_ratio = 0.0
    threshold = None
    for lower, upper in itertools.pairwise(usable):
        ratio = upper / lower
        if ratio > best_ratio:
            best_ratio = ratio
            threshold = math.sqrt(lower * upper)
    if best_ratio < minimum_ratio:
        return None
    return threshold


def night_of(row: dict[str, Any]) -> str | None:
    """Name the observing night of a stored record.

    Parameters
    ----------
    row : `dict` [`str`, `Any`]
        A stored alignment or guiding row. Its ``session_id`` is used when it
        has one; otherwise the night is worked out from its ``timestamp``.

    Returns
    -------
    night : `str` or `None`
        The night, or `None` when the row has neither a session nor a time.
    """
    from astrometricslib.reports import observing_night_id

    session = row.get("session_id")
    if session:
        return str(session)
    timestamp = row.get("timestamp")
    if timestamp is None:
        return None
    return str(observing_night_id(float(timestamp)))


def records_of(wayfinder: Any) -> Any:
    """Return the store of alignment and guiding records of a `Wayfinder`.

    Parameters
    ----------
    wayfinder : `wayfindinglib.Wayfinder`
        The entry point a tuning run gives under the name ``"wayfinder"``.

    Returns
    -------
    records : `ControlRecordStore`
        The records in ``wayfinding.db``.

    Raises
    ------
    TuningSkippedError
        If the records cannot be reached.
    """
    try:
        return wayfinder.control._context.records
    except (AttributeError, OSError, ValueError) as error:
        raise TuningSkippedError(f"the wayfinding records cannot be reached: {error}") from error


def butler_of(wayfinder: Any) -> Any:
    """Return the `DiskButler` of a `Wayfinder`, which holds the night records.

    Parameters
    ----------
    wayfinder : `wayfindinglib.Wayfinder`
        The entry point a tuning run gives under the name ``"wayfinder"``.

    Returns
    -------
    butler : `DiskButler`
        The storage layer.

    Raises
    ------
    TuningSkippedError
        If the storage cannot be reached.
    """
    try:
        return wayfinder.control._context.butler
    except (AttributeError, OSError, ValueError) as error:
        raise TuningSkippedError(f"the wayfinding storage cannot be reached: {error}") from error
