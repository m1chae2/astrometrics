"""Purpose: Match frame exposure windows to guiding samples (a pure join).

Description: To say whether wind or a bad dither spoiled a frame, one needs
the guide error during that frame's exposure. A frame carries the time it
began and its exposure length. The guiding records carry one error
measurement every few seconds. This module cuts the guiding samples to each
frame's window and summarizes the error inside it.

The join is pure: the caller reads the frames and the guiding samples and
passes them in, and nothing is read or stored here. The Control task
`frame_guiding.link_frames_to_guiding` reads the data and calls this
module, and the target report calls it with frames and samples it reads
itself.
"""

from __future__ import annotations

import statistics
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from itertools import pairwise
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

_EDGE_MARGIN_SECONDS = 5.0
"""How far before the first frame and after the last frame the caller should
read
guiding samples, in seconds. A sample just outside a window can still be the
nearest one to its edge. Design choice of the original frame-guiding tool, kept
as it was."""


class FrameWindow(BaseModel):
    """The time span of one frame's exposure.

    Parameters
    ----------
    frame_id : `str`
        A label for the frame (a file name or a frame id).
    start_epoch_seconds : `float`
        When the exposure began, in seconds since the Unix epoch.
    exposure_seconds : `float`
        The exposure length, in seconds. 0 gives an empty window.
    """

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    frame_id: str
    start_epoch_seconds: float
    exposure_seconds: float = Field(ge=0.0)

    @property
    def end_epoch_seconds(self) -> float:
        """Give the time the exposure ended.

        Returns
        -------
        end_epoch_seconds : `float`
            Start plus exposure, in seconds since the Unix epoch.
        """
        return self.start_epoch_seconds + self.exposure_seconds


def window_statistics(samples: Sequence[Mapping[str, Any]], exposure_seconds: float) -> dict[str, Any]:
    """Summarize the guide error of the samples inside one window.

    Parameters
    ----------
    samples : `Sequence` [`Mapping`]
        The guiding rows inside the window, in time order. Each needs
        ``timestamp`` (seconds since the Unix epoch), ``dra`` and ``ddec``
        (the error in right ascension and declination, in arcseconds).
    exposure_seconds : `float`
        The window length, used to say how much of it the samples cover.

    Returns
    -------
    summary : `dict` [`str`, `Any`]
        ``guide_samples`` (the count); when it is above 0, also
        ``coverage_fraction`` (the share of the window the samples cover,
        or ``None`` with fewer than two samples), ``rms_ra_arcsec``,
        ``rms_dec_arcsec``, ``rms_total_arcsec`` and ``peak_total_arcsec``.
        With no samples only ``guide_samples`` (0) is given: missing data is
        not turned into a number.
    """
    if not samples:
        return {"guide_samples": 0}
    ra_errors = [float(row["dra"]) for row in samples]
    dec_errors = [float(row["ddec"]) for row in samples]
    total_errors = [(ra**2 + dec**2) ** 0.5 for ra, dec in zip(ra_errors, dec_errors, strict=True)]
    gaps = [later["timestamp"] - earlier["timestamp"] for earlier, later in pairwise(samples)]
    cadence = statistics.median(gaps) if gaps else None
    coverage = min(1.0, len(samples) * cadence / exposure_seconds) if cadence and exposure_seconds else None
    return {
        "guide_samples": len(samples),
        "coverage_fraction": round(coverage, 2) if coverage is not None else None,
        "rms_ra_arcsec": round((sum(value**2 for value in ra_errors) / len(ra_errors)) ** 0.5, 2),
        "rms_dec_arcsec": round((sum(value**2 for value in dec_errors) / len(dec_errors)) ** 0.5, 2),
        "rms_total_arcsec": round((sum(value**2 for value in total_errors) / len(total_errors)) ** 0.5, 2),
        "peak_total_arcsec": round(max(total_errors), 2),
    }


def samples_inside_window(
    sorted_samples: Sequence[Mapping[str, Any]], timestamps: Sequence[float], window: FrameWindow
) -> list[Mapping[str, Any]]:
    """Pick the guiding samples that fall inside one window.

    A sample belongs to the window when ``start <= timestamp < end``.

    Parameters
    ----------
    sorted_samples : `Sequence` [`Mapping`]
        All guiding rows, sorted by ``timestamp``.
    timestamps : `Sequence` [`float`]
        The ``timestamp`` of each row, in the same order (so the search does
        not rebuild it for every window).
    window : `FrameWindow`
        The window.

    Returns
    -------
    inside : `list` [`Mapping`]
        The rows in the window, in time order.
    """
    first = bisect_left(timestamps, window.start_epoch_seconds)
    last = bisect_left(timestamps, window.end_epoch_seconds)
    return list(sorted_samples[first:last])


def join_frames_to_guiding(
    windows: Sequence[FrameWindow], samples: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Report the guide error during each frame's exposure.

    Parameters
    ----------
    windows : `Sequence` [`FrameWindow`]
        The frames' exposure windows.
    samples : `Sequence` [`Mapping`]
        Guiding rows with ``timestamp``, ``dra`` and ``ddec`` (arcseconds).
        They need not be sorted. Read them from a few seconds before the
        first window to a few seconds after the last (`guiding_read_range`).

    Returns
    -------
    rows : `list` [`dict`]
        One per window, in the order of `windows`: the statistics of
        `window_statistics`. A frame with no guiding samples has
        ``guide_samples`` of 0 and no error values.
    """
    ordered = sorted(samples, key=lambda row: row["timestamp"])
    timestamps = [row["timestamp"] for row in ordered]
    return [
        window_statistics(samples_inside_window(ordered, timestamps, window), window.exposure_seconds)
        for window in windows
    ]


def guiding_read_range(windows: Sequence[FrameWindow]) -> tuple[float, float] | None:
    """Give the time range of guiding samples worth reading for some frames.

    Parameters
    ----------
    windows : `Sequence` [`FrameWindow`]
        The frames' windows.

    Returns
    -------
    start_end : `tuple` [`float`, `float`] or `None`
        The first window start minus 5 seconds and the last window end plus
        5 seconds, in seconds since the Unix epoch. ``None`` for no windows.
    """
    if not windows:
        return None
    return (
        min(window.start_epoch_seconds for window in windows) - _EDGE_MARGIN_SECONDS,
        max(window.end_epoch_seconds for window in windows) + _EDGE_MARGIN_SECONDS,
    )
