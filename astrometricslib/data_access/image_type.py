"""One place that knows where a FITS file's real image data lives.

A FITS file's pixel data almost always sits in the primary HDU -- the
first block in the file, at index 0. But some tools write a bare
primary HDU carrying only administrative keywords, with the actual
image one HDU later, at index 1. A reader that only ever looks at
index 0 sees an empty or near-empty header on those files: missing
dimensions, a missing Bayer pattern, sometimes no data at all.

We have already fixed this exact bug once, in `frame_scanning.py`. This
module exists so every other reader shares the same rule instead of
reimplementing (or forgetting to implement) it. `AstrometricsImage`
(`utilities/image.py`) is the other place this rule is already correctly
handled -- it predates this module and is not rebuilt on top of it here,
since its lazy-loading and auto-repair behaviour is tightly coupled to
its own caching, but any *new* file-reading code should use this module
instead of adding a fifth copy of the rule.

`collapse_to_2d` handles a related but separate question: once you have
an image's pixel array, is it already a single 2D plane, or a stack of
colour channels that needs averaging down to one? Eight call sites
across the library each answered that question themselves, and five of
them got it subtly wrong -- see the function's own docstring.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import numpy as np
from astropy.io import fits

logger = logging.getLogger(__name__)


@contextmanager
def open_primary_hdu(path: str) -> Iterator[fits.hdu.base._BaseHDU]:
    """Open a FITS file and yield the HDU that actually holds pixel data.

    Parameters
    ----------
    path : `str`
        Path to the FITS file.

    Yields
    ------
    hdu : `astropy.io.fits.hdu.base._BaseHDU`
        HDU 0, or HDU 1 when HDU 0 carries no data and a second HDU
        exists to fall back to.
    """
    with fits.open(path, memmap=False) as hdul:
        if len(hdul) > 1 and hdul[0].data is None:
            yield hdul[1]
        else:
            yield hdul[0]


def read_header(path: str) -> fits.Header:
    """Read the header of whichever HDU holds a FITS file's pixel data.

    Parameters
    ----------
    path : `str`
        Path to the FITS file.

    Returns
    -------
    header : `astropy.io.fits.Header`
        A detached copy of the header, safe to use after this call
        returns.
    """
    with open_primary_hdu(path) as hdu:
        return hdu.header.copy()


def read_data(path: str):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Read the pixel data of whichever HDU holds a FITS file's image.

    Parameters
    ----------
    path : `str`
        Path to the FITS file.

    Returns
    -------
    data : `numpy.ndarray` or `None`
        The raw pixel array, exactly as astropy returns it -- not
        collapsed to 2D or cast to a particular dtype. `None` if the
        HDU declares no data at all.
    """
    with open_primary_hdu(path) as hdu:
        return hdu.data


def frame_dimensions(path: str) -> tuple[int, int] | None:
    """Read a frame's (NAXIS1, NAXIS2) from whichever HDU holds its data.

    Parameters
    ----------
    path : `str`
        Path to the FITS file.

    Returns
    -------
    dimensions : `tuple` [`int`, `int`] or `None`
        ``(NAXIS1, NAXIS2)``, or `None` if the header could not be read
        or does not declare both axes.
    """
    try:
        header = read_header(path)
        return (int(header["NAXIS1"]), int(header["NAXIS2"]))
    except Exception as read_error:
        logger.debug("Could not read dimensions of %s: %s", path, read_error)
        return None


def frame_uses_color_filter_array(path: str) -> bool | None:
    """Report whether one frame's header declares a Bayer color filter array.

    ``BAYERPAT`` is the authoritative marker Siril itself looks for: a
    CFA sensor writes it (e.g. "RGGB"), a mono sensor does not. Frame
    dimensionality is deliberately not used as a signal, because an
    undebayered CFA frame and a mono frame are both 2D and
    indistinguishable by shape alone. Absence of the keyword is treated
    as monochrome -- the conservative reading, since guessing a pattern
    is exactly the failure this function exists to help callers avoid.

    Parameters
    ----------
    path : `str`
        Path to the FITS file.

    Returns
    -------
    uses_color_filter_array : `bool` or `None`
        `True` if ``BAYERPAT`` is present and non-empty, `False` if the
        header was read successfully and has no ``BAYERPAT``, or `None`
        if the header could not be read at all -- callers that scan
        several frames for a definitive answer should treat `None` as
        "try the next frame", not as "mono".
    """
    try:
        header = read_header(path)
    except Exception as read_error:
        logger.debug("Could not read header of %s for CFA detection: %s", path, read_error)
        return None
    bayer_pattern = header.get("BAYERPAT")
    return bool(bayer_pattern is not None and str(bayer_pattern).strip())


def select_dominant_frame_dimensions(frame_paths: list[str]) -> tuple[set[str], tuple[int, int] | None]:
    """Keep only the frames sharing the most common image dimensions.

    Siril builds a sequence from frames of identical geometry and
    refuses anything else outright -- "Cannot add an image with
    different properties to an existing sequence" -- which fails the
    *whole* conversion, not just the offending frame. A handful of
    odd-sized files therefore costs a target its entire stack.

    Observed on the 2026-08-24 run: Sun had 4 stray frames (2402x1753,
    2286x2122, 1905x1689, 2233x1761) among 446 at 6000x4000 and lost all
    450; M 27 had 45 frames at 6016x4016 against 81 at 6000x4000 and
    lost all 126. Keeping the majority geometry costs those 4 frames of
    Sun's 450 and recovers everything else.

    Ties are resolved toward the larger frame count first and then the
    larger pixel area, so a genuinely split set prefers the more
    detailed geometry rather than whichever happened to be read first.

    Parameters
    ----------
    frame_paths : `list` [`str`]
        Candidate light-frame paths, already known to be readable.

    Returns
    -------
    kept_paths : `set` [`str`]
        Paths whose dimensions match the dominant geometry. Frames
        whose header cannot be read are kept, so this filter never
        removes a frame on the strength of a failed read.
    dominant_dimensions : `tuple` [`int`, `int`] or `None`
        The winning ``(NAXIS1, NAXIS2)``, or `None` when no header
        could be read at all.
    """
    paths_by_dimensions: dict[tuple[int, int], list[str]] = {}
    unreadable_paths: list[str] = []
    for frame_path in frame_paths:
        dimensions = frame_dimensions(frame_path)
        if dimensions is None:
            unreadable_paths.append(frame_path)
            continue
        paths_by_dimensions.setdefault(dimensions, []).append(frame_path)

    if not paths_by_dimensions:
        return set(frame_paths), None

    dominant_dimensions = max(
        paths_by_dimensions,
        key=lambda dimensions: (
            len(paths_by_dimensions[dimensions]),
            dimensions[0] * dimensions[1],
        ),
    )
    return set(paths_by_dimensions[dominant_dimensions]) | set(unreadable_paths), dominant_dimensions


def collapse_to_2d(data: np.ndarray) -> np.ndarray:
    """Average a multi-channel image down to a single 2D plane.

    A 3D image array can have its channel axis first
    (``(channels, height, width)``, the usual layout for a debayered
    RGB/RGBA stack, or a degenerate single-channel cube) or last
    (``(height, width, channels)``). Nothing in a FITS file's NAXIS
    keywords says which, so channel count is used as the signal: a
    leading axis of length 1, 3, or 4 is treated as the channel axis;
    anything else is assumed to be a trailing channel axis instead.

    Checking only for {3, 4} -- what every call site did before this
    function existed -- misses the degenerate ``(1, height, width)``
    case. On that shape the check fails, so the *trailing* axis (the
    image's own width) gets averaged away instead of the leading
    channel axis, producing a garbage ``(1, height)`` result fed
    straight into star detection or a quality metric downstream. This
    is that fix, unified across every site that used the narrower
    check.

    Parameters
    ----------
    data : `numpy.ndarray`
        A 2D or 3D image array. Returned unchanged if already 2D.

    Returns
    -------
    collapsed : `numpy.ndarray`
        `data` if it was already 2D; otherwise `data` averaged down to
        2D across its channel axis.
    """
    if data.ndim != 3:
        return data
    if data.shape[0] in (1, 3, 4):
        return np.mean(data, axis=0)
    return np.mean(data, axis=-1)
