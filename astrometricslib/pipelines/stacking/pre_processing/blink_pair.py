"""Pick a typical frame and a reference star to blink a bad frame against.

A person who opens a flagged or set-aside frame wants to see what is wrong with
it. This module finds what the viewer needs to flip between that frame and a
normal one at the same star:

- The reference star is a bright star, away from its neighbors and below the
  saturation level, near the frame center. It comes from the same star finder
  and the same candidate rules as the frame's star-shape measurement
  (`star_profile_fitting`), so a star the measurement would refuse is never
  chosen.
- The typical frame is the frame of the same session whose stored star
  elongation and sky unevenness lie closest to the session medians. The
  session is the batch the quarantine step judges the frame in
  (`split_into_batches`).
- The star's position in the typical frame comes from matching the stars of
  the two frames (`raw_frame_check.measure_field_shift`), the matching the
  quarantine's pointing check uses. It allows for the drift between frames and
  for the half turn of a meridian flip.

Every position leaving this module is in 1-based pixels, as in FITS and DS9.
"""

import logging
import math
import statistics
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import numpy as np

from astrometricslib._internal.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS, collapse_to_2d, open_primary_hdu
from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.quality.blink_pair import BlinkPair, FramePositionMatch, MatchedPoint
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.shared.quality import raw_frame_check
from astrometricslib.pipelines.shared.quality.metrics import FRAME_STAR_ELONGATION
from astrometricslib.pipelines.shared.quality.star_profile_fitting import (
    BAYER_CELL_SIZE_PX,
    STAR_FINDER_EXPECTED_FWHM_PX,
    detect_stars_without_shape_cut,
    grid_candidates,
    plateau_saturation_level_adu,
    sum_bayer_cells,
    usable_top_level_adu,
)
from astrometricslib.pipelines.stacking.metrics import FRAME_SKY_UNEVENNESS
from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
    split_into_batches,
    stored_frame_values,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS
from astrometricslib.utilities.frame_identity import frame_subject_id
from astrometricslib.utilities.robust_statistics import robust_sigma

logger = logging.getLogger(__name__)

__all__ = [
    "choose_blink_pair",
    "choose_reference_stars",
    "choose_typical_path",
    "match_frame_positions",
]

# How many reference stars are offered, best first. The caller takes the first
# one that also lies inside the typical frame.
REFERENCE_STAR_COUNT = 6

# A reference star must be brighter than this fraction of the brightest
# candidate's flux, so it is clearly above the noise but not the one blown-out
# star of the field. Chosen by eye on the candidates of NGC 7331; not tuned.
REFERENCE_MINIMUM_FLUX_FRACTION = 0.25

# The mapped star must lie at least this far from the frame edge, in pixels, so
# the zoomed view has pixels to show around it.
EDGE_MARGIN_PX = 40


def choose_reference_stars(
    plane: np.ndarray, top_level_adu: float | None, *, bayer_mosaic: bool = False
) -> list[tuple[float, float]]:
    """Pick stars to compare, near the center of the frame.

    Candidates are the stars the star-shape measurement would accept: no other
    star close by, the whole fit box inside the frame and no pixel near the
    saturation level. Of those, the ones with at least
    `REFERENCE_MINIMUM_FLUX_FRACTION` of the brightest candidate's flux are
    kept and sorted by distance from the frame center.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The 2-D pixels.
    top_level_adu : `float` or `None`
        The pixel value above which the sensor is not trusted. `None` falls
        back to the plane's own plateau of equal brightest pixels.
    bayer_mosaic : `bool`, optional
        The plane is a raw color mosaic. Stars are found on 2 x 2 super-pixels
        and the positions given back in original pixels.

    Returns
    -------
    stars : `list` [`tuple` [`float`, `float`]]
        Up to `REFERENCE_STAR_COUNT` ``(x, y)`` positions, 0-based pixels of
        the plane given, nearest the center first. Empty when no star
        qualifies.
    """
    factor = BAYER_CELL_SIZE_PX if bayer_mosaic else 1
    rows, columns = plane.shape
    if bayer_mosaic:
        plane = sum_bayer_cells(plane)
        top_level_adu = None if top_level_adu is None else top_level_adu * factor**2
    sources = detect_stars_without_shape_cut(plane, resolve(STAR_FINDER_EXPECTED_FWHM_PX).value / factor)
    if not sources:
        return []
    if top_level_adu is None:
        top_level_adu = plateau_saturation_level_adu(plane)
    cells, _refusals = grid_candidates(sources, plane.shape, plane, top_level_adu)
    accepted = {(x, y) for cell in cells for x, y in cell}
    flux_of = {(float(s["x_centroid"]), float(s["y_centroid"])): float(s["flux"]) for s in sources}
    candidates = [(position, flux_of.get(position, 0.0)) for position in accepted]
    if not candidates:
        return []
    brightest = max(flux for _, flux in candidates)
    bright = [
        position for position, flux in candidates if flux >= REFERENCE_MINIMUM_FLUX_FRACTION * brightest
    ]
    center_x, center_y = columns / (2 * factor), rows / (2 * factor)
    bright.sort(key=lambda position: math.hypot(position[0] - center_x, position[1] - center_y))
    return [
        (x * factor + (factor - 1) / 2, y * factor + (factor - 1) / 2)
        for x, y in bright[:REFERENCE_STAR_COUNT]
    ]


def choose_typical_path(
    values: Mapping[str, tuple[float | None, float | None]], excluded_path: str | None = None
) -> str | None:
    """Pick the frame whose numbers lie closest to the session's typical ones.

    Each of the two numbers (star elongation, sky unevenness) is measured in
    robust spreads from its session median, so neither one outweighs the other
    because of its units. A frame missing a number is judged on the other one.

    Parameters
    ----------
    values : `Mapping` [`str`, `tuple`]
        For each candidate frame path, its ``(star elongation, sky
        unevenness)``. Either may be `None`.
    excluded_path : `str`, optional
        A path never chosen (the frame being examined).

    Returns
    -------
    path : `str` or `None`
        The closest frame, or `None` when no candidate has a number.
    """
    candidates = {
        path: numbers
        for path, numbers in values.items()
        if path != excluded_path and any(number is not None for number in numbers)
    }
    if not candidates:
        return None
    centers: list[float | None] = []
    spreads: list[float] = []
    for index in range(2):
        column = [numbers[index] for numbers in candidates.values() if numbers[index] is not None]
        if not column:
            centers.append(None)
            spreads.append(1.0)
            continue
        middle = statistics.median(column)
        spread = robust_sigma(column)
        centers.append(middle)
        spreads.append(spread if spread > 0 else 1.0)

    def distance(path: str) -> float:
        """Give the frame's distance from the session medians.

        Returns
        -------
        distance : `float`
            The root-sum-square over the numbers the frame has.
        """
        total = 0.0
        for index, number in enumerate(candidates[path]):
            center = centers[index]
            if number is not None and center is not None:
                total += ((number - center) / spreads[index]) ** 2
        return math.sqrt(total)

    return min(sorted(candidates), key=distance)


def _read_plane(path: str) -> tuple[np.ndarray, bool, str | None]:
    """Read the first plane of a frame for star finding.

    Returns
    -------
    plane : `numpy.ndarray`
        The 2-D pixels.
    is_mosaic : `bool`
        `True` for a raw color mosaic.
    camera : `str` or `None`
        The header's camera name.
    """
    with open_primary_hdu(path) as hdu:
        data = np.asarray(hdu.data, dtype=float)
        header = hdu.header
        camera = header.get("INSTRUME")
        is_mosaic = bool(str(header.get("BAYERPAT", "")).strip())
    plane = data if data.ndim == 2 else collapse_to_2d(data)
    return plane, is_mosaic and data.ndim == 2, str(camera) if camera else None


def _find_stars(path: str) -> tuple[np.ndarray, tuple[int, int]]:
    """Find the bright stars of a frame for matching.

    Returns
    -------
    stars : `numpy.ndarray`
        Star rows from `raw_frame_check.detect_stars`.
    shape : `tuple` [`int`, `int`]
        ``(height, width)``.
    """
    with open_primary_hdu(path) as hdu:
        image = np.asarray(hdu.data, dtype=float)
    plane = image if image.ndim == 2 else collapse_to_2d(image)
    stars, _blobs, _longest, _trail = raw_frame_check.detect_stars(plane)
    return stars, (int(plane.shape[0]), int(plane.shape[1]))


def match_positions_between_stars(
    reference_stars: np.ndarray,
    stars: np.ndarray,
    shape: tuple[int, int],
    points: Sequence[tuple[float, float]],
) -> FramePositionMatch:
    """Map positions from a reference frame into another frame.

    Parameters
    ----------
    reference_stars, stars : `numpy.ndarray`
        Star rows from `raw_frame_check.detect_stars` for the two frames.
    shape : `tuple` [`int`, `int`]
        ``(height, width)`` of the frames (they share one size).
    points : `Sequence` [`tuple` [`float`, `float`]]
        ``(x, y)`` positions in the reference frame, 1-based pixels.

    Returns
    -------
    match : `FramePositionMatch`
        The mapped positions, or the reason the frames could not be matched.
    """
    found = raw_frame_check.measure_field_shift(reference_stars, stars, shape)
    if found is None:
        return FramePositionMatch(
            matched=False, reason="Too few stars agree between the two frames to line them up."
        )
    shift_y, shift_x, votes, turned = found
    height, width = shape
    mapped: list[MatchedPoint] = []
    for x, y in points:
        row, column = y - 1.0, x - 1.0
        if turned:
            row, column = height - 1 - row, width - 1 - column
        new_row, new_column = row + shift_y, column + shift_x
        inside = 0 <= new_row <= height - 1 and 0 <= new_column <= width - 1
        mapped.append(
            MatchedPoint(
                x=x, y=y, mapped_x=float(new_column + 1.0), mapped_y=float(new_row + 1.0), inside=inside
            )
        )
    return FramePositionMatch(matched=True, turned=turned, votes=int(votes), points=mapped)


def match_frame_positions(
    path_a: str, path_b: str, points: Sequence[tuple[float, float]]
) -> FramePositionMatch:
    """Say where positions of one frame lie in another frame of the same field.

    Parameters
    ----------
    path_a, path_b : `str`
        Two FITS frames of one size.
    points : `Sequence` [`tuple` [`float`, `float`]]
        ``(x, y)`` positions in ``path_a``, 1-based pixels.

    Returns
    -------
    match : `FramePositionMatch`
        The positions mapped into ``path_b``.
    """
    stars_a, shape_a = _find_stars(path_a)
    stars_b, shape_b = _find_stars(path_b)
    if shape_a != shape_b:
        return FramePositionMatch(matched=False, reason="The two frames are not the same size.")
    return match_positions_between_stars(stars_a, stars_b, shape_a, points)


def _timestamp_from_date(date: str) -> float | None:
    """Read a ``YYYY-MM-DD HH:MM:SS`` capture time as seconds since 1970 (UTC).

    Returns
    -------
    timestamp : `float` or `None`
        `None` when the text is not in that form.
    """
    try:
        return datetime.strptime(date, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp()
    except ValueError:
        return None


def choose_blink_pair(
    target_id: str,
    path: str,
    frames: Sequence[FrameRecord],
    clicked: FrameRecord | None = None,
) -> BlinkPair:
    """Pick a typical frame of the session and one star to compare in both.

    Parameters
    ----------
    target_id : `str`
        The target's id, for the stored measurements.
    path : `str`
        The frame being examined (flagged or set aside).
    frames : `Sequence` [`FrameRecord`]
        The target's frames that are in the stack.
    clicked : `FrameRecord`, optional
        Camera, filter, exposure and time of ``path`` when it is not one of
        ``frames`` (a set-aside frame), so its session can be found.

    Returns
    -------
    pair : `BlinkPair`
        The typical frame and the star in both frames, or the reason none was
        found.
    """
    lights = [frame for frame in frames if frame.role == "LIGHT"]
    own = next((frame for frame in lights if frame.path == path), clicked)
    if own is None:
        return BlinkPair(found=False, path=path, reason="This frame is not known to the target.")
    pool = [frame for frame in lights if frame.path != path] + [own]
    session = next((batch for batch in split_into_batches(pool) if any(f.path == path for f in batch)), [])
    others = [frame for frame in session if frame.path != path]
    if not others:
        return BlinkPair(found=False, path=path, reason="No other frame of this session is in the stack.")
    elongations = stored_frame_values(target_id, FRAME_STAR_ELONGATION)
    unevenness = stored_frame_values(target_id, FRAME_SKY_UNEVENNESS)
    numbers = {
        frame.path: (
            elongations.get(frame_subject_id(frame.path, target_id)),
            unevenness.get(frame_subject_id(frame.path, target_id)),
        )
        for frame in others
    }
    typical = choose_typical_path(numbers, path)
    if typical is None:
        return BlinkPair(
            found=False,
            path=path,
            session_frame_count=len(others),
            reason="No frame of this session has a stored star shape or sky measurement yet.",
        )
    try:
        plane, is_mosaic, camera = _read_plane(path)
        try:
            top_level = usable_top_level_adu(resolve_camera_profile(camera))
        except AstrometricsError:
            top_level = None
        stars_here = choose_reference_stars(plane, top_level, bayer_mosaic=is_mosaic)
        if not stars_here:
            return BlinkPair(
                found=False,
                path=path,
                typical_path=typical,
                session_frame_count=len(others),
                reason="No bright, isolated, unsaturated star was found in this frame.",
            )
        points = [(x + 1.0, y + 1.0) for x, y in stars_here]
        match = match_frame_positions(path, typical, points)
    except (*FITS_READ_ERRORS, *DATA_ERRORS) as error:
        logger.debug("Blink pair for %s failed: %s", path, error)
        return BlinkPair(
            found=False,
            path=path,
            typical_path=typical,
            session_frame_count=len(others),
            reason=f"A frame could not be read: {error}",
        )
    if not match.matched:
        return BlinkPair(
            found=False, path=path, typical_path=typical, session_frame_count=len(others), reason=match.reason
        )
    height, width = plane.shape
    for point in match.points:
        margin = EDGE_MARGIN_PX
        if (
            point.inside
            and margin <= point.mapped_x <= width - margin
            and margin <= point.mapped_y <= height - margin
        ):
            return BlinkPair(
                found=True,
                path=path,
                typical_path=typical,
                star_x=point.x,
                star_y=point.y,
                typical_star_x=point.mapped_x,
                typical_star_y=point.mapped_y,
                turned=match.turned,
                votes=match.votes,
                session_frame_count=len(others),
                width=int(width),
                height=int(height),
            )
    return BlinkPair(
        found=False,
        path=path,
        typical_path=typical,
        session_frame_count=len(others),
        reason="No reference star of this frame lies well inside the typical frame.",
    )


def frame_record_for_set_aside(
    lights: Sequence[FrameRecord], path: str, camera: str, filter_name: str, exposure: float | None, date: str
) -> FrameRecord | None:
    """Build a frame record for a set-aside frame, so its session can be found.

    A set-aside frame is no longer in the target's frame list. Its camera,
    filter, exposure and capture time (read from its header) are matched to a
    frame still in the stack, and that frame's record is copied with the
    set-aside frame's path and time. Matching by the stack's own records keeps
    the spelling of the camera, filter and exposure the same, so the frame
    lands in the same batch as its neighbors.

    Parameters
    ----------
    lights : `Sequence` [`FrameRecord`]
        The target's light frames that are in the stack.
    path : `str`
        The set-aside frame's path.
    camera, filter_name : `str`
        The names read from the frame's header. Either may be empty.
    exposure : `float` or `None`
        The exposure in seconds, or `None` when unknown.
    date : `str`
        The capture time, ``YYYY-MM-DD HH:MM:SS`` (UTC).

    Returns
    -------
    frame : `FrameRecord` or `None`
        The record, or `None` when no frame in the stack has the same setup or
        the capture time cannot be read.
    """
    timestamp = _timestamp_from_date(date)
    if timestamp is None:
        return None

    def same_exposure(frame: FrameRecord) -> bool:
        """Tell whether the frame's exposure equals the set-aside frame's.

        Returns
        -------
        same : `bool`
            `True` when both are known and equal.
        """
        try:
            return exposure is not None and math.isclose(float(frame.exposure), exposure, abs_tol=1e-6)
        except ValueError:
            return False

    def same_filter(frame: FrameRecord) -> bool:
        """Tell whether the frame's filter equals the set-aside frame's.

        Returns
        -------
        same : `bool`
            `True` when the names agree, or the set-aside frame names none.
        """
        return (
            not filter_name
            or str(getattr(frame.filter, "value", frame.filter)).lower() == filter_name.lower()
        )

    template = next(
        (
            frame
            for frame in lights
            if same_exposure(frame) and same_filter(frame) and (not camera or frame.camera == camera)
        ),
        None,
    )
    if template is None:
        return None
    return template.model_copy(update={"path": path, "timestamp": timestamp, "date": date})
