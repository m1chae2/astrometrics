"""Purpose: Give a synthetic drifting sequence its exact plate solutions.

Description: Photometry places each star through its frame's own plate
solution. `make_drifted_sequence` builds frames whose stars move by a known
shift and rotation (see `drifted_stars`), so the true solution of every frame
is known too. `drifted_frame_solves` builds it: the solved header that a plate
solver would return if it were exact. A test passes the result to
`VariabilityAnalyzer.process` in place of a Siril run.
"""

from collections.abc import Sequence

import numpy as np

from astrometricslib._internal.drivers.interfaces.plate_solve_driver import FrameSolve
from astrometricslib.test.synthetic.sky_map import tan_sky_map

__all__ = ["drifted_frame_solves"]

# The sky position of the middle of the reference frame, and the pixel size on
# the sky. Any values work; these make a field of about 0.2 degrees at the
# size of the usual test frames.
_CENTER_RA_DEG = 250.0
_CENTER_DEC_DEG = 36.0
_DEGREES_PER_PIXEL = 2.0 / 3600.0


def drifted_frame_solves(
    paths: Sequence[str],
    shape: tuple[int, int],
    drift_px_per_frame: tuple[float, float] = (0.3, -0.2),
    rotation_deg_per_frame: float = 0.0,
    scale_change_per_frame: float = 0.0,
) -> dict[str, FrameSolve]:
    """Build the exact plate solution of every frame of a drifting sequence.

    Frame ``k`` follows `drifted_stars`: a position ``p0`` of the first frame
    is at ``c + (1 + k * scale_change_per_frame) * R(k * rotation) @ (p0 - c)
    + k * drift`` in frame ``k``, where ``c`` is the frame center. The
    solution of frame ``k`` turns that position back into the sky position
    that ``p0`` has in the first frame.

    Parameters
    ----------
    paths : `Sequence` [`str`]
        The frame files, the first being the reference frame.
    shape : `tuple` [`int`, `int`]
        The frame shape ``(ny, nx)``.
    drift_px_per_frame : `tuple` [`float`, `float`], optional
        The shift ``(dx, dy)`` added for each frame.
    rotation_deg_per_frame : `float`, optional
        The rotation about the frame center for each frame, in degrees.
    scale_change_per_frame : `float`, optional
        The change of scale for each frame, as a fraction.

    Returns
    -------
    solves : `dict` [`str`, `FrameSolve`]
        The solved header of each frame, keyed by path.
    """
    ny, nx = shape
    center = np.array([(nx - 1) / 2.0, (ny - 1) / 2.0])
    reference_cd = np.array([[-_DEGREES_PER_PIXEL, 0.0], [0.0, _DEGREES_PER_PIXEL]])
    solves: dict[str, FrameSolve] = {}
    for index, path in enumerate(paths):
        angle = np.radians(index * rotation_deg_per_frame)
        stretch = 1.0 + index * scale_change_per_frame
        moves = stretch * np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        reference_pixel = center + index * np.asarray(drift_px_per_frame, dtype=float) + 1.0
        sky_map = tan_sky_map(
            _CENTER_RA_DEG, _CENTER_DEC_DEG, reference_pixel, cd=reference_cd @ np.linalg.inv(moves)
        )
        solves[path] = FrameSolve(sky_map.to_header())
    return solves
