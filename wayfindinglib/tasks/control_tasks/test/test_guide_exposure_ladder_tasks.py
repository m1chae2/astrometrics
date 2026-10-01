"""Purpose: Unit tests for taking the guide frames of a guide exposure test.

Description: Uses a stand-in guide camera to check that frames are read from
the forms an INDI camera returns them in, that a new frame is waited for, that
a camera that never answers is reported, and that an exposure the camera
refuses is reported. It does not use the real guide camera.
"""

import io
from typing import Any

import numpy as np
import pytest
from astropy.io import fits

from wayfindinglib.tasks.control_tasks.guide_exposure_ladder_tasks import (
    capture_guide_ladder,
    frame_from_blob,
)


def _fits_bytes(array: np.ndarray) -> bytes:
    """Write an array as the FITS bytes a camera would send.

    Returns
    -------
    data : `bytes`
        The FITS file.
    """
    buffer = io.BytesIO()
    fits.PrimaryHDU(array).writeto(buffer)
    return buffer.getvalue()


class _Property:
    """A stand-in for an INDI BLOB property holding one blob."""

    def __init__(self, data: bytes) -> None:
        """Hold the blob's bytes."""
        self._data = data

    def __getitem__(self, index: int) -> _Blob:
        """Return the blob.

        Returns
        -------
        blob : `_Blob`
            The one blob in the property.
        """
        return _Blob(self._data)


class _Blob:
    """A stand-in for one INDI blob."""

    def __init__(self, data: bytes) -> None:
        """Hold the blob's bytes."""
        self._data = data

    def getblobdata(self) -> bytes:
        """Return the raw bytes.

        Returns
        -------
        data : `bytes`
            The blob's data.
        """
        return self._data


def test_an_array_is_used_as_it_is() -> None:
    """Verify a numpy frame passes straight through."""
    frame = np.arange(12.0).reshape(3, 4)

    assert np.array_equal(frame_from_blob(frame), frame)


def test_fits_bytes_become_an_array() -> None:
    """Verify FITS data is decoded."""
    frame = np.arange(12, dtype=np.uint16).reshape(3, 4)

    assert np.array_equal(frame_from_blob(_fits_bytes(frame)), frame)


def test_an_indi_blob_property_is_read() -> None:
    """Verify the property form, indexed, is decoded."""
    frame = np.arange(12, dtype=np.uint16).reshape(3, 4)

    assert np.array_equal(frame_from_blob(_Property(_fits_bytes(frame))), frame)


def test_a_single_blob_object_is_read() -> None:
    """Verify an object with its own data accessor is decoded."""
    frame = np.arange(12, dtype=np.uint16).reshape(3, 4)

    assert np.array_equal(frame_from_blob(_Blob(_fits_bytes(frame))), frame)


def test_a_colour_cube_is_reduced_to_its_first_plane() -> None:
    """Verify leading axes are removed to give a two-dimensional frame."""
    cube = np.stack([np.full((3, 4), 7.0), np.zeros((3, 4))])

    assert frame_from_blob(cube).shape == (3, 4)


@pytest.mark.parametrize("bad", [None, 42, object()])
def test_something_else_is_reported_with_what_was_received(bad: Any) -> None:
    """Verify an unreadable frame raises a clear error."""
    with pytest.raises(ValueError, match="guide"):
        frame_from_blob(bad)


class _Camera:
    """A stand-in guide camera that delivers a new frame per exposure."""

    def __init__(self, delivers: bool = True, accepts: bool = True) -> None:
        """Start with one frame on the camera and no exposures taken."""
        self.delivers = delivers
        self.accepts = accepts
        self.exposures: list[tuple[float, float | None]] = []
        self._frame = np.zeros((4, 4))

    def expose(self, seconds: float, gain: float | None) -> bool:
        """Take an exposure, replacing the frame if the camera delivers.

        Returns
        -------
        sent : `bool`
            Whether the command was accepted.
        """
        self.exposures.append((seconds, gain))
        if self.accepts and self.delivers:
            self._frame = np.full((4, 4), float(len(self.exposures)))
        return self.accepts

    def read(self) -> np.ndarray:
        """Return the latest frame.

        Returns
        -------
        frame : `numpy.ndarray`
            The camera's latest frame.
        """
        return self._frame


def test_each_exposure_length_gets_its_own_series_of_new_frames() -> None:
    """Verify every frame is a new one and the series are kept apart."""
    camera = _Camera()

    ladder = capture_guide_ladder(camera.expose, camera.read, [0.5, 1.0], 3, gain=50.0, sleep=lambda _: None)

    assert sorted(ladder) == [0.5, 1.0]
    assert [frame[0, 0] for frame in ladder[0.5]] == [1.0, 2.0, 3.0]
    assert [frame[0, 0] for frame in ladder[1.0]] == [4.0, 5.0, 6.0]
    assert all(gain == pytest.approx(50.0) for _, gain in camera.exposures)


def test_a_camera_that_never_delivers_is_reported() -> None:
    """Verify waiting for a frame has an end."""
    camera = _Camera(delivers=False)

    with pytest.raises(RuntimeError, match="No new guide frame"):
        capture_guide_ladder(camera.expose, camera.read, [1.0], 2, sleep=lambda _: None)


def test_an_exposure_the_camera_refuses_is_reported() -> None:
    """Verify a refused command stops the test with a clear error."""
    camera = _Camera(accepts=False)

    with pytest.raises(RuntimeError, match="did not accept"):
        capture_guide_ladder(camera.expose, camera.read, [1.0], 2, sleep=lambda _: None)
