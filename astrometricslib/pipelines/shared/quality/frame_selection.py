"""Purpose: Choose which frames a quality check should measure.

Description: A night's folder mixes luminance, colour and spectroscopy
frames from several targets and times. A quality check that measures the
newest N files by name measures the wrong ones. This module picks frames by
filter, by file-name range and by time, for frames in the library and for
frames in a plain folder. It reads only; nothing is changed.
"""

import glob
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from astrometricslib.drivers import fits_access
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral


@dataclass(frozen=True)
class FrameSelection:
    """The rules a frame must meet to be measured.

    Parameters
    ----------
    filter_name : `str` or `None`
        Keep frames whose filter matches this text, ignoring case. `None`
        keeps every filter.
    first_file, last_file : `str` or `None`
        Keep frames whose file name sorts at or after ``first_file`` and at
        or before ``last_file``. A part of a name works, such as ``"013"``
        for the frame numbered 013: the bound is compared with the file
        name's end when it is short (see `file_in_range`).
    since, until : `float` or `None`
        Keep frames taken at or after ``since`` and at or before
        ``until``, in seconds since the Unix epoch.
    include_spectra : `bool`
        Keep spectroscopy frames. `False` drops them unless ``filter_name``
        asks for one.
    """

    filter_name: str | None = None
    first_file: str | None = None
    last_file: str | None = None
    since: float | None = None
    until: float | None = None
    include_spectra: bool = False

    @property
    def has_bounds(self) -> bool:
        """Whether a file range or time range was given.

        Returns
        -------
        has_bounds : `bool`
            `True` when the first ``limit`` frames of the range, not the
            newest, are the ones a caller should keep.
        """
        return any(value is not None for value in (self.first_file, self.last_file, self.since, self.until))

    @property
    def is_active(self) -> bool:
        """Whether any rule narrows the frames.

        Returns
        -------
        is_active : `bool`
            `True` if a filter, range, time bound or spectra choice was given.
        """
        return self.has_bounds or self.filter_name is not None or self.include_spectra


def parse_iso_time(text: str | None) -> float | None:
    """Turn an ISO 8601 time into seconds since the Unix epoch.

    Parameters
    ----------
    text : `str` or `None`
        A time such as ``"2026-10-03T21:30:00-06:00"``. No offset means UTC.

    Returns
    -------
    seconds : `float` or `None`
        The time, or `None` if ``text`` is empty.

    """
    if not text:
        return None
    moment = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()


def file_in_range(file_name: str, first_file: str | None, last_file: str | None) -> bool:
    """Check a file name against an inclusive name range.

    A bound that is not a whole file name (for example ``"013"``) is matched
    against the number at the end of the name, so ``first_file="013"`` and
    ``last_file="025"`` select frames 013 to 025 of a night whatever the
    prefix. A longer bound is compared as ordinary text.

    Parameters
    ----------
    file_name : `str`
        The file's name, without its folder.
    first_file, last_file : `str` or `None`
        The inclusive bounds, or `None` for no bound.

    Returns
    -------
    in_range : `bool`
        Whether the name is inside the range.
    """
    stem = os.path.splitext(file_name)[0]
    trailing_digits = stem[len(stem.rstrip("0123456789")) :]

    def compare(bound: str) -> tuple[Any, Any]:
        """Pair the file's comparable value with the bound's.

        Parameters
        ----------
        bound : `str`
            One bound of the range.

        Returns
        -------
        pair : `tuple` [`Any`, `Any`]
            The file's value and the bound's, comparable with each other.
        """
        if bound.isdigit() and trailing_digits:
            return int(trailing_digits), int(bound)
        return file_name, bound

    if first_file:
        value, bound = compare(first_file)
        if value < bound:
            return False
    if last_file:
        value, bound = compare(last_file)
        if value > bound:
            return False
    return True


_FILTER_ALIASES = {
    "l": "luminance",
    "lum": "luminance",
    "r": "red",
    "g": "green",
    "b": "blue",
}
"""Short filter names and the long names the library stores them under."""


def _normalized_filter_name(name: Any) -> str:
    """Lower-case a filter name and replace a short alias by its long name.

    Parameters
    ----------
    name : `Any`
        An enum member or text.

    Returns
    -------
    normalized : `str`
        The comparable name.
    """
    text = str(getattr(name, "value", name)).strip().lower()
    return _FILTER_ALIASES.get(text, text)


def filter_matches(frame_filter: Any, wanted: str | None) -> bool:
    """Check a frame's filter against the wanted text.

    Parameters
    ----------
    frame_filter : `Any`
        The filter as stored: an enum member or text.
    wanted : `str` or `None`
        The filter to keep, ignoring case. A short name such as ``"L"`` or
        ``"R"`` means ``Luminance`` or ``Red``. `None` accepts every filter.

    Returns
    -------
    matches : `bool`
        Whether the two names are the same filter. Parts of a name do not
        match, so ``"L"`` never matches ``"Star Analyzer 200"``.
    """
    if not wanted:
        return True
    return _normalized_filter_name(frame_filter) == _normalized_filter_name(wanted)


def frame_passes(
    file_name: str, frame_filter: Any, timestamp: float | None, selection: FrameSelection
) -> bool:
    """Apply a selection to one frame's file name, filter and time.

    Parameters
    ----------
    file_name : `str`
        The frame's file name.
    frame_filter : `Any`
        The frame's filter.
    timestamp : `float` or `None`
        When the frame began, in seconds since the Unix epoch.
    selection : `FrameSelection`
        The rules.

    Returns
    -------
    passes : `bool`
        Whether the frame is kept. A frame with no time fails a time bound.
    """
    if not selection.include_spectra and frame_is_spectral({"filter": frame_filter}):
        if not (selection.filter_name and filter_matches(frame_filter, selection.filter_name)):
            return False
    if not filter_matches(frame_filter, selection.filter_name):
        return False
    if not file_in_range(file_name, selection.first_file, selection.last_file):
        return False
    if selection.since is not None and (timestamp is None or timestamp < selection.since):
        return False
    return not (selection.until is not None and (timestamp is None or timestamp > selection.until))


def select_library_frames(frames: list[Any], selection: FrameSelection) -> list[Any]:
    """Pick the library light frames a selection keeps, oldest first.

    Parameters
    ----------
    frames : `list` [`Any`]
        Frame records with ``path``, ``filter`` and ``timestamp``.
    selection : `FrameSelection`
        The rules.

    Returns
    -------
    chosen : `list` [`Any`]
        The kept frames in time order.
    """
    kept = [
        frame
        for frame in frames
        if frame_passes(os.path.basename(frame.path), frame.filter, frame.timestamp, selection)
    ]
    kept.sort(key=lambda frame: frame.timestamp or 0.0)
    return kept


def select_folder_paths(folder: str, selection: FrameSelection) -> list[str]:
    """Pick the FITS files of a folder that a selection keeps.

    The filter and the time come from each file's header (``FILTER`` and
    ``DATE-OBS``), so a folder that was never added to the library works.

    Parameters
    ----------
    folder : `str`
        A folder of ``*.fits`` frames.
    selection : `FrameSelection`
        The rules.

    Returns
    -------
    paths : `list` [`str`]
        The kept files in file-name order.
    """
    kept = []
    for path in sorted(glob.glob(os.path.join(folder, "*.fits"))):
        name = os.path.basename(path)
        if not file_in_range(name, selection.first_file, selection.last_file):
            continue
        needs_header = (
            selection.filter_name is not None
            or not selection.include_spectra
            or selection.since is not None
            or selection.until is not None
        )
        frame_filter, timestamp = None, None
        if needs_header:
            try:
                header = fits_access.read_header(path)
            except OSError:
                continue
            frame_filter = header.get("FILTER")
            observed = header.get("DATE-OBS")
            try:
                timestamp = parse_iso_time(str(observed)) if observed else None
            except ValueError:
                timestamp = None
        if frame_passes(name, frame_filter, timestamp, selection):
            kept.append(path)
    return kept
