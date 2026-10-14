"""Purpose: Take the guide frames for a guide exposure test.

Description: Commands the guide camera to take a short series of frames at each
of several exposure lengths and turns each result into an array. It does not
move the mount and does not guide. The analysis itself is in
`wayfindinglib.analytics.guide_exposure_ladder`.

The guide camera reports a finished frame as an INDI BLOB property (the raw
data the camera sent, usually a FITS file in memory). `frame_from_blob`
turns the forms that property can take into a two-dimensional array. The
capture step has been tested against a stand-in camera. It has not yet been
run against the real guide camera, so a failure to read a frame raises an
error that says what was received.
"""

import hashlib
import io
import time
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
from astropy.io import fits

from astrometricslib import HardwareError

FRAME_WAIT_MARGIN_SECONDS = 10.0
"""How long past the exposure to wait for a new frame before giving up."""

_POLL_SECONDS = 0.25
"""Time between checks for a new frame."""


def frame_from_blob(blob: Any) -> np.ndarray:
    """Turn what the guide camera returned into a two-dimensional array.

    Parameters
    ----------
    blob : `Any`
        A `numpy.ndarray`, FITS bytes, or an INDI BLOB property holding FITS
        bytes (read as ``blob[0].getblobdata()``, ``blob.getblobdata()`` or
        ``blob.blob``).

    Returns
    -------
    frame : `numpy.ndarray`
        The image, in camera counts, with any leading axes removed.

    Raises
    ------
    HardwareError
        If `blob` is none of those, or holds no image.
    """
    data = _blob_bytes(blob)
    if isinstance(data, np.ndarray):
        array = data
    else:
        with fits.open(io.BytesIO(data), memmap=False) as hdus:
            array = next((hdu.data for hdu in hdus if hdu.data is not None), None)
        if array is None:
            raise HardwareError("The guide camera's frame holds no image data.")
    array = np.asarray(array, dtype=float)
    while array.ndim > 2:
        array = array[0]
    if array.ndim != 2:
        raise HardwareError(f"Expected a two-dimensional guide frame, got {array.ndim} dimensions.")
    return array


def _blob_bytes(blob: Any) -> Any:
    """Pull the raw data out of whatever form the camera returned it in.

    Returns
    -------
    data : `bytes` or `numpy.ndarray`
        The frame as FITS bytes, or as an array if it already was one.

    Raises
    ------
    HardwareError
        If there is no frame, or the object is not a form the camera returns.
    """
    if blob is None:
        raise HardwareError("The guide camera returned no frame.")
    if isinstance(blob, np.ndarray | bytes | bytearray | memoryview):
        return blob if isinstance(blob, np.ndarray) else bytes(blob)
    candidate = blob
    if not hasattr(candidate, "getblobdata") and not hasattr(candidate, "blob"):
        try:
            candidate = blob[0]
        except (TypeError, IndexError, KeyError) as error:
            raise HardwareError(f"Cannot read a guide frame from a {type(blob).__name__}.") from error
    data = candidate.getblobdata() if hasattr(candidate, "getblobdata") else getattr(candidate, "blob", None)
    if data is None:
        raise HardwareError(f"The guide camera's {type(blob).__name__} holds no data.")
    return data if isinstance(data, np.ndarray) else bytes(data)


def capture_guide_ladder(
    take_exposure: Callable[[float, float | None], Any],
    read_frame: Callable[[], Any],
    exposure_seconds: Sequence[float],
    frames_per_exposure: int,
    gain: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[float, list[np.ndarray]]:
    """Take a series of guide frames at each exposure length.

    Parameters
    ----------
    take_exposure : `Callable`
        Starts one guide exposure: ``take_exposure(seconds, gain)``. It returns
        a false value if the command was not sent.
    read_frame : `Callable`
        Returns the guide camera's latest frame, in any form `frame_from_blob`
        accepts.
    exposure_seconds : `Sequence` [`float`]
        The exposure lengths to try.
    frames_per_exposure : `int`
        Frames to take at each length.
    gain : `float` or `None`, optional
        Guide camera gain to set, or `None` to leave it alone.
    sleep : `Callable`, optional
        Waits for a number of seconds. Replaceable for testing.

    Returns
    -------
    frames : `dict` [`float`, `list` [`numpy.ndarray`]]
        The frames taken at each exposure length, in time order.

    Raises
    ------
    HardwareError
        If an exposure command is not sent, or no new frame arrives in time.
    """
    ladder: dict[float, list[np.ndarray]] = {}
    previous_digest = _digest(read_frame())
    for exposure in exposure_seconds:
        frames = []
        for _ in range(frames_per_exposure):
            if not take_exposure(exposure, gain):
                raise HardwareError(f"The guide camera did not accept a {exposure:g} s exposure.")
            waited = 0.0
            while True:
                sleep(_POLL_SECONDS)
                waited += _POLL_SECONDS
                blob = read_frame()
                digest = _digest(blob)
                if digest != previous_digest:
                    break
                if waited > exposure + FRAME_WAIT_MARGIN_SECONDS:
                    raise HardwareError(
                        f"No new guide frame arrived within {exposure + FRAME_WAIT_MARGIN_SECONDS:g} s of "
                        f"a {exposure:g} s exposure."
                    )
            previous_digest = digest
            frames.append(frame_from_blob(blob))
        ladder[float(exposure)] = frames
    return ladder


def _digest(blob: Any) -> str | None:
    """Fingerprint a frame, so a new one can be told from the last.

    Returns
    -------
    digest : `str` or `None`
        A hash of the frame's data, or `None` if there is no frame.
    """
    if blob is None:
        return None
    try:
        data = _blob_bytes(blob)
    except HardwareError:
        return None
    raw = data.tobytes() if isinstance(data, np.ndarray) else data
    return hashlib.sha1(raw, usedforsecurity=False).hexdigest()
