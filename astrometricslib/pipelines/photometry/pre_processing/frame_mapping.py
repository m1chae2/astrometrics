"""Purpose: Say where each reference-frame star sits in another frame.

Description: Photometry puts a measuring circle on every star of every frame.
The circle's starting position comes from the frame's own plate solution: the
sky position of a star, read from the reference frame's solution, is turned
back into a pixel position in the other frame with that frame's solution (the
photometry register entry PHO-24). Siril solves each frame (see
`drivers/siril/astrometry/sequence_solve.py`); this module only reads the
solutions it returns.

`FrameMapping` is the map from one frame's pixels to another's. It also keeps
the three numbers the `registration_drift` gate reads: how far the middle of
the frame moved, how far a corner moves beyond that (the part a single shift
cannot describe, from a turned or rescaled field), and whether the solution
maps the frame at all.

A frame with no usable solution has no `FrameMapping`. The caller leaves such
a frame out and says why; there is no other way to place the circles.
"""

from dataclasses import dataclass

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib.foundation.errors import ProcessingError

# The pixel convention of the positions the photometry measures: the first
# pixel's center is (0, 0), as in numpy arrays and photutils.
_PIXEL_ORIGIN = 0


class FrameMappingError(ProcessingError):
    """A frame's plate solution cannot place stars on the frame."""


def wcs_from_solved_header(header: fits.Header) -> WCS:
    """Read the sky map of a solved header.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The solved header of a frame.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        The map from pixels to sky positions, with its distortion terms.

    Raises
    ------
    FrameMappingError
        If the header holds no sky map.
    """
    try:
        wcs = WCS(header, relax=True)
    except (ValueError, KeyError, TypeError) as read_error:
        raise FrameMappingError(f"the solution cannot be read: {read_error}") from read_error
    if not wcs.has_celestial:
        raise FrameMappingError("the solved header holds no sky map")
    return wcs


@dataclass(frozen=True)
class FrameMapping:
    """How one frame's pixels line up with the reference frame's.

    Attributes
    ----------
    reference_wcs : `astropy.wcs.WCS`
        The reference frame's sky map.
    frame_wcs : `astropy.wcs.WCS`
        This frame's sky map.
    shift_x, shift_y : `float`
        How far the middle of the frame moved against the reference frame,
        in pixels.
    corner_displacement_px : `float`
        The most any corner of the frame moves beyond that shift, in pixels.
        It is the part of the mapping that a single shift cannot describe: a
        turn of the field, a change of scale or a change of distortion.
    """

    reference_wcs: WCS
    frame_wcs: WCS
    shift_x: float
    shift_y: float
    corner_displacement_px: float

    @classmethod
    def from_solutions(cls, reference_wcs: WCS, frame_wcs: WCS, frame_shape: tuple[int, int]) -> FrameMapping:
        """Build the mapping of a frame and check that it places stars.

        Parameters
        ----------
        reference_wcs : `astropy.wcs.WCS`
            The reference frame's sky map.
        frame_wcs : `astropy.wcs.WCS`
            This frame's sky map.
        frame_shape : `tuple` [`int`, `int`]
            The frame's ``(height, width)``, in pixels. Both frames have it.

        Returns
        -------
        mapping : `FrameMapping`
            The mapping, with the shift of the frame's middle and the
            displacement of its corners.

        Raises
        ------
        FrameMappingError
            If the solutions do not map the middle or a corner of the frame
            to finite pixel positions.
        """
        height, width = frame_shape
        middle = np.array([[(width - 1) / 2.0, (height - 1) / 2.0]])
        corners = np.array([[0.0, 0.0], [width - 1.0, 0.0], [0.0, height - 1.0], [width - 1.0, height - 1.0]])
        mapping = cls(reference_wcs, frame_wcs, 0.0, 0.0, 0.0)
        moved_middle = mapping.predict_many(middle)
        moved_corners = mapping.predict_many(corners)
        if not (np.all(np.isfinite(moved_middle)) and np.all(np.isfinite(moved_corners))):
            raise FrameMappingError("the solution does not map the frame onto the reference frame")
        shift = moved_middle[0] - middle[0]
        corner_displacement = float(np.max(np.hypot(*(moved_corners - corners - shift).T)))
        return cls(reference_wcs, frame_wcs, float(shift[0]), float(shift[1]), corner_displacement)

    def predict_many(self, positions: np.ndarray) -> np.ndarray:
        """Say where many reference-frame positions are in this frame.

        Each position is turned into a sky position with the reference
        frame's map and back into pixels with this frame's map.

        Parameters
        ----------
        positions : `numpy.ndarray`
            Shape ``(positions, 2)``: each ``(x, y)`` in the reference frame,
            in pixels.

        Returns
        -------
        moved : `numpy.ndarray`
            The ``(x, y)`` of each position in this frame. A position the
            frame's map cannot place is NaN.
        """
        if len(positions) == 0:
            return np.empty((0, 2))
        sky = self.reference_wcs.all_pix2world(positions, _PIXEL_ORIGIN)
        # `quiet` keeps a position that does not converge from raising; its
        # result is checked below.
        moved = self.frame_wcs.all_world2pix(sky, _PIXEL_ORIGIN, quiet=True)
        return np.asarray(moved, dtype=float)

    def predict(self, x: float, y: float) -> tuple[float, float]:
        """Say where one reference-frame position is in this frame.

        Parameters
        ----------
        x, y : `float`
            The position in the reference frame, in pixels.

        Returns
        -------
        position : `tuple` [`float`, `float`]
            The ``(x, y)`` in this frame, or NaN for both when the frame's
            map cannot place it.
        """
        moved = self.predict_many(np.array([[x, y]], dtype=float))[0]
        return float(moved[0]), float(moved[1])
