"""Measures a stack's noise in empty patches of sky, on a fixed flux scale.

A stack's noise tells how well the frames added up. To compare the noise of
two stacks of the same target, or of the same target before and after more
frames, three things must be the same:

1. The patches of sky. Each patch is a small circle (an "aperture") with no
   star in it, bright or faint. Bright stars are found by the star finder;
   faint ones are found by a mask made from the stack's own pixels
   (`faint_source_distance`) before any patch is drawn, so a patch is chosen
   by a pixel mask made before placement, not by its own sum. The mask is not
   perfect: a 3.5-sigma noise peak near a patch can exclude it, so the
   noise is biased low by a small amount that was measured (validation entry
   STK-36). Different patches can have
   different noise (vignetting, a faint nebula), so every stack of a target
   uses the same patches. The patches are chosen once, stored as sky positions
   (right ascension and declination, not pixels) in a small file beside the
   target's stacks, and found again in each later stack through that stack's
   coordinate solution (WCS). No two patches overlap, so their sky rings share
   no pixels and the sums are independent.
2. The way the noise is measured. Each aperture's value is the sum of its
   pixels minus the sky level taken from a ring around it (the same sum a
   star's brightness uses). The noise is the robust spread of those sums over
   all the apertures.
3. The flux scale. Siril's stacks have their own brightness scale, so the noise
   in "ADU" of one stack cannot be compared with another's. The noise is
   divided by the brightness of a fixed set of reference stars (bright,
   isolated stars of the first stack, also stored as sky positions). Each
   star is measured in a large aperture (3 times the first stack's star
   width, with a sky ring beyond it), so the brightness hardly depends on
   the seeing. On every later stack, each star's brightness is divided by the
   same star's brightness on the first stack. The median of those ratios is
   the stack's zero point, and their scatter is its error. A star that is
   at or above the camera's saturation level in the registration reference
   frame is never used.

Where the coordinate solution comes from: the stack's own header when it has
one. A freshly made stack has none, because the astrometry step writes it
later. Then the registration reference frame is used. Siril lines every
frame up with that frame, so the stack has the reference frame's
geometry. The stack may have had its edges trimmed afterwards (the trim moves
the origin of the pixel grid by a known offset), and the frame's solution is
shifted by the same offset. The claim is checked, not assumed: the brightest
stars of the stack must sit where the reference frame shows stars. If they do
not, no noise is measured.

Even a stack's own header is checked. A frame's own "solution" is often only a
first guess: the camera software (Ekos) writes the position the mount was told
to point at, not a plate solution, and Siril copies it into the stack. The sky
position of a pixel can then be wrong by tens of pixels, and by a different
amount on every night. So when a target already has stored apertures, every
stack, whatever its header says, is first moved onto them: the stored
reference stars are looked for near where the solution predicts them, the shift
that most of them agree on is found, and the solution is moved by that shift.
The stars that agree also give the rotation the solution would need; the stack
is refused when that rotation is larger than `MAXIMUM_ROTATION_DEG` and is
more than three times its own error. The leftover position error (how well the
stars agree after the shift) is reported in the `position_note`. If fewer than
a few stars agree, no noise is measured.

A target keeps one set of apertures for each camera and optic (see
`ApertureSet.setup_key`). A stack made with other equipment starts a new set.
To start over for a target, call `reset_apertures` or delete
``quality_apertures.json`` beside its stacks; the next stack chooses new
apertures. Noise values measured with different sets are not comparable.

The estimator and the numbers (100 apertures, radius 4 px, 10 reference stars)
are declared in `pipelines/stacking/metrics.py`.

The work is split over four modules. `stack_image.py` reads a stack and sums
the light in one aperture. `empty_apertures.py` chooses and stores the
apertures. This module measures a stack's noise in them
(`measure_stack_noise`). `difference_noise.py` measures the noise of the
difference of two stacks and compares two stacks' noise aperture by aperture.
"""

import logging
import math
import os
from dataclasses import dataclass, replace

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from scipy.spatial import cKDTree

from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.drivers.fits_access import collapse_to_2d, read_data, read_header
from astrometricslib.pipelines.shared.applied_camera_profile import camera_name_from_header
from astrometricslib.pipelines.stacking.post_processing.empty_apertures import (
    DETECTED_STAR_COUNT,
    MINIMUM_USABLE_APERTURES,
    MINIMUM_USABLE_REFERENCE_STARS,
    REPLACEMENT_DEPTH_FACTOR,
    ApertureSet,
    StackDepth,
    _is_much_deeper,
    _setup_key,
    _store_apertures_once,
    choose_apertures_with_reason,
    load_apertures,
    load_apertures_with_reason,
)
from astrometricslib.pipelines.stacking.post_processing.stack_image import (
    MAD_SIGMA_ERROR_FACTOR,
    MAD_TO_SIGMA,
    MEDIAN_ERROR_FACTOR,
    SaturationCheck,
    _celestial_wcs,
    _pixel_scale_arcsec,
    _sky_to_pixels,
    read_stack_plane,
    signed_aperture_sum,
    sky_level_of_plane,
)
from astrometricslib.pipelines.stacking.processing.group_alignment import detect_star_centroids

logger = logging.getLogger(__name__)

__all__ = [
    "StackNoiseOutcome",
    "StackNoiseResult",
    "measure_stack_noise",
]

# The pixel scale of a later stack may differ from the first by this fraction
# before the stored apertures are refused (a different binning or optic).
MAXIMUM_PIXEL_SCALE_CHANGE = 0.10

# The check that a reference frame's solution fits the stack: this many of the
# stack's brightest stars must have a star in the reference frame within this
# many pixels. The registration puts the reference star at the same pixel, so
# a match closer than 3 px is far tighter than chance at these star densities.
WCS_CHECK_STAR_COUNT = 10
WCS_CHECK_MINIMUM_MATCHES = 5
WCS_CHECK_TOLERANCE_PX = 3.0

# Moving a solution onto the stored apertures. The stored reference stars are
# searched for within this many pixels of where the solution predicts them. On
# the M 57 frames of 2026-10-03 the guess was off by up to 82 px between frames
# of one night; 500 px (about 16 arcmin at 1.9 arcsec per pixel) leaves room
# for a night-to-night pointing error, and is still small enough that the
# stars of a field do not line up by chance (4000 star pairs in a window of
# 1,000,000 square pixels, against a 3 px agreement).
SEED_SEARCH_RADIUS_PX = 500.0

# Reference stars agree on a shift when their shifts differ by less than this.
# It is the tolerance of the other star checks in this module.
SEED_AGREEMENT_PX = WCS_CHECK_TOLERANCE_PX

# The largest rotation, in degrees, between a stack's solution and the stored
# stars that is accepted. The apertures are moved by a shift only, so a
# rotation leaves a point at distance d from the field center misplaced by d
# times the angle. At 0.1 degree (0.00175 radian) a point 2000 px from the
# center, a typical corner of a 3000 px stack, is misplaced by 3.5 px: about
# the radius of the 4 px noise aperture, where a star would begin to miss its
# aperture. A design estimate; no stack with a rotation has been measured.
MAXIMUM_ROTATION_DEG = 0.1

# The rotation must also exceed this many times its own error to count.
ROTATION_SIGNIFICANCE = 3.0

NO_WCS_REASON = "no WCS for this stack"


@dataclass(frozen=True)
class StackNoiseResult:
    """The noise of a stack in empty sky, and the scale it is read against.

    Parameters
    ----------
    noise_adu : `float`
        The robust spread of the empty-aperture sums.
    noise_uncertainty_adu : `float`
        Its 1-sigma uncertainty, 1.1664 x `noise_adu` / sqrt(n) for n
        apertures (see `MAD_SIGMA_ERROR_FACTOR`).
    aperture_count : `int`
        The number of usable apertures behind it.
    reference_flux_adu : `float`
        The brightness of the reference stars in this stack's units: the
        zero point times the median brightness the stars had on the stack
        that chose them.
    reference_uncertainty_adu : `float`
        Its 1-sigma uncertainty, from the scatter of the stars' brightness
        ratios.
    reference_star_count : `int`
        The number of reference stars measured.
    normalized_noise : `float`
        `noise_adu` divided by `reference_flux_adu`.
    normalized_uncertainty : `float`
        Its 1-sigma uncertainty.
    wcs_source : `str`
        Where this stack's coordinate solution came from.
    apertures_created : `bool`
        True when this stack chose the target's apertures.
    reference_zero_point : `float`
        The median ratio of each reference star's brightness here to its
        brightness on the stack that chose the apertures. It is 1 on that
        stack.
    reference_zero_point_uncertainty : `float`
        Its 1-sigma uncertainty as a fraction of itself (0 on the stack that
        chose the apertures).
    reference_aperture_radius_px : `float`
        The radius of the large aperture the reference stars were measured in.
    saturated_reference_stars : `int`
        How many stored reference stars were left out of this stack's zero
        point because they reach the camera's saturation level.
    position_note : `str`
        What was done to put the stored apertures on this stack: the shift
        found, how many stars agreed, how well, and the rotation. Empty for the
        stack that chose the apertures.
    saturation_source : `str`
        How saturated stars were kept out of this measurement.
    aperture_sums : `tuple` [`float`, ...]
        The sky-subtracted sum at every stored aperture, in the stored order,
        NaN where the aperture did not fall on this stack. Two stacks measured
        in the same apertures can be compared aperture by aperture.
    aperture_set_origin : `str`
        The name of the stack that chose the apertures.
    aperture_identity : `str`
        The identity of the set (see `ApertureSet.identity`), which says which
        set `aperture_sums` belongs to. Two results are compared only when
        these match.
    aperture_definition_note : `str`
        Which stack defined the set and how deep it was (see
        `ApertureSet.describe_definition`).
    """

    noise_adu: float
    noise_uncertainty_adu: float
    aperture_count: int
    reference_flux_adu: float
    reference_uncertainty_adu: float
    reference_star_count: int
    normalized_noise: float
    normalized_uncertainty: float
    wcs_source: str
    apertures_created: bool
    reference_zero_point: float = 1.0
    reference_zero_point_uncertainty: float = 0.0
    reference_aperture_radius_px: float = 0.0
    saturated_reference_stars: int = 0
    position_note: str = ""
    saturation_source: str = ""
    aperture_sums: tuple[float, ...] = ()
    aperture_set_origin: str = ""
    aperture_identity: str = ""
    aperture_definition_note: str = ""


@dataclass(frozen=True)
class StackNoiseOutcome:
    """The result of a noise measurement, or the reason there is none.

    Parameters
    ----------
    result : `StackNoiseResult` or `None`
        The measurement, or `None` when it could not be made.
    reason : `str`
        Why there is no result. Empty when there is one.
    stack_sky_level : `float` or `None`
        The median of the stack's usable pixels, in the stack's units, taken
        from the plane this measurement already read (see
        `sky_level_of_plane`).
        It is set even when there is no noise result. `None` when the plane
        was not read or has no usable pixel.
    """

    result: StackNoiseResult | None
    reason: str = ""
    stack_sky_level: float | None = None


@dataclass(frozen=True)
class _Alignment:
    """How a stack's solution was moved onto the stored reference stars.

    Parameters
    ----------
    shift_row, shift_column : `float`
        The shift applied, in pixels.
    matched_stars : `int`
        How many stored reference stars agreed on it.
    rms_px : `float`
        The root mean square distance, after the shift, between those stars
        and the detected stars they matched.
    rotation_deg : `float`
        The rotation the matched stars suggest, in degrees.
    rotation_error_deg : `float`
        Its 1-sigma error.
    """

    shift_row: float
    shift_column: float
    matched_stars: int
    rms_px: float
    rotation_deg: float
    rotation_error_deg: float

    def describe(self) -> str:
        """Say in words how the solution was moved.

        Returns
        -------
        note : `str`
            One sentence with the shift, the stars that agreed, their leftover
            position error and the rotation.
        """
        return (
            f"The stack's sky positions were moved by {self.shift_row:+.1f} rows and "
            f"{self.shift_column:+.1f} columns onto {self.matched_stars} stored reference stars; "
            f"after the move they agree to {self.rms_px:.2f} px rms, and the rotation they suggest is "
            f"{self.rotation_deg:+.3f} +/- {self.rotation_error_deg:.3f} deg."
        )


def _saturation_check(
    reference_frame_path: str | None, crop_offset: tuple[int, int]
) -> SaturationCheck | None:
    """Read the registration reference frame and the camera's saturation level.

    Parameters
    ----------
    reference_frame_path : `str` or `None`
        The frame Siril aligned all the others to.
    crop_offset : `tuple` [`int`, `int`]
        The (columns, rows) cut from the stack's left and top edges.

    Returns
    -------
    check : `SaturationCheck` or `None`
        The check, or `None` when there is no readable reference frame.
    """
    if not reference_frame_path:
        return None
    try:
        header = read_header(reference_frame_path)
        data = read_data(reference_frame_path)
    except OSError, ValueError:
        return None
    if data is None:
        return None
    profile = resolve_camera_profile(camera_name_from_header(header))
    limit = float(profile.saturation_threshold_adu.value)
    linearity = profile.photometric_linearity_limit_adu
    if linearity is not None:
        limit = min(limit, float(linearity.value))
    stand_in = " (generic stand-in: the camera has no profile)" if profile.is_generic_fallback else ""
    return SaturationCheck(
        frame_plane=collapse_to_2d(np.asarray(data, dtype=np.float32)),
        crop_offset=crop_offset,
        limit_adu=limit,
        source=f"peak in the registration reference frame at or above {limit:g} ADU of the "
        f"{profile.camera_name} camera profile{stand_in}",
    )


def _reference_frame_wcs(
    reference_frame_path: str, stack_plane: np.ndarray, crop_offset: tuple[int, int]
) -> tuple[WCS | None, str]:
    """Take the registration reference frame's solution, and check it fits.

    Parameters
    ----------
    reference_frame_path : `str`
        The frame Siril aligned all the others to.
    stack_plane : `numpy.ndarray`
        The stack's image.
    crop_offset : `tuple` [`int`, `int`]
        The (columns, rows) cut from the stack's left and top edges when its
        noisy edges were trimmed; (0, 0) when nothing was trimmed.

    Returns
    -------
    wcs : `astropy.wcs.WCS` or `None`
        The frame's solution moved onto the stack's pixel grid, or `None`.
    reason : `str`
        Why there is none. Empty when there is one.
    """
    header = read_header(reference_frame_path)
    frame_wcs = _celestial_wcs(header)
    if frame_wcs is None:
        return None, NO_WCS_REASON
    shifted = frame_wcs.deepcopy()
    shifted.wcs.crpix = [frame_wcs.wcs.crpix[0] - crop_offset[0], frame_wcs.wcs.crpix[1] - crop_offset[1]]
    frame_data = read_data(reference_frame_path)
    if frame_data is None:
        return None, NO_WCS_REASON
    frame_plane = collapse_to_2d(np.asarray(frame_data, dtype=np.float64))
    stack_stars = detect_star_centroids(stack_plane, count=WCS_CHECK_STAR_COUNT)
    # Saturated stars stay in: the stack's brightest stars are bright in
    # the single frame too, and the frame's own 99.99th percentile would cut
    # them.
    frame_stars = detect_star_centroids(frame_plane, count=DETECTED_STAR_COUNT, skip_saturated=False)
    if len(stack_stars) < WCS_CHECK_MINIMUM_MATCHES or len(frame_stars) == 0:
        return None, "the reference frame's solution could not be checked: too few stars were found"
    # Stack pixel (row, column) is frame pixel (row + rows cut,
    # column + columns cut).
    expected = stack_stars + np.array([crop_offset[1], crop_offset[0]], dtype=float)
    distance = cKDTree(frame_stars).query(expected)[0]
    matches = int(np.count_nonzero(distance <= WCS_CHECK_TOLERANCE_PX))
    if matches < WCS_CHECK_MINIMUM_MATCHES:
        return None, (
            f"the reference frame does not line up with the stack ({matches} of {len(stack_stars)} "
            "bright stars match), so its coordinate solution does not apply to the stack"
        )
    return shifted, ""


def _move_guess_onto_reference_stars(
    plane: np.ndarray, wcs: WCS, reference_sky: np.ndarray
) -> tuple[WCS | None, str, _Alignment | None]:
    """Shift a solution so it lands on the stored reference stars.

    Every pair of a stored star and a detected star suggests a shift. The true
    shift is the one the most different stars suggest together; when two
    shifts tie, the smaller one wins, because a good solution needs little
    moving. The stars that agree also give a rotation (a least-squares fit of
    their positions about their own center) and the leftover position error.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack's image.
    wcs : `astropy.wcs.WCS`
        The solution: a plate solution, or a first guess that points where the
        mount was told to.
    reference_sky : `numpy.ndarray`
        Shape ``(k, 2)``: right ascension and declination of the stored
        reference stars, in degrees.

    Returns
    -------
    moved : `astropy.wcs.WCS` or `None`
        The solution shifted so the reference stars sit on stars of the
        stack, or `None` when too few stars agree or the rotation is too large.
    reason : `str`
        Why there is none. Empty when there is one.
    alignment : `_Alignment` or `None`
        What was found (also given when the rotation was refused).
    """
    predicted = _sky_to_pixels(wcs, reference_sky)[:, ::-1]
    detected = detect_star_centroids(plane, count=DETECTED_STAR_COUNT, skip_saturated=False)
    if len(predicted) == 0 or len(detected) == 0:
        return None, "the stored reference stars could not be found: no stars were detected", None
    star_index, detected_index = np.meshgrid(
        np.arange(len(predicted)), np.arange(len(detected)), indexing="ij"
    )
    shifts = detected[detected_index.ravel()] - predicted[star_index.ravel()]
    owner = star_index.ravel()
    pair_detected = detected_index.ravel()
    near = np.hypot(shifts[:, 0], shifts[:, 1]) <= SEED_SEARCH_RADIUS_PX
    shifts, owner, pair_detected = shifts[near], owner[near], pair_detected[near]
    if len(shifts) == 0:
        return (
            None,
            "the stored reference stars were not found near where the stack's solution puts them",
            None,
        )
    tree = cKDTree(shifts)
    neighbors = tree.query_ball_point(shifts, r=SEED_AGREEMENT_PX)
    distinct_counts = np.array([len(set(owner[members])) for members in neighbors])
    tied = np.flatnonzero(distinct_counts == distinct_counts.max())
    best = int(tied[np.argmin(np.hypot(shifts[tied, 0], shifts[tied, 1]))])
    if distinct_counts[best] < MINIMUM_USABLE_REFERENCE_STARS:
        return (
            None,
            (
                f"only {distinct_counts[best]} of {len(predicted)} stored reference stars agree on a shift "
                "from the stack's solution, so the stack's sky positions cannot be trusted"
            ),
            None,
        )
    members = np.asarray(neighbors[best])
    shift_row, shift_column = np.median(shifts[members], axis=0)
    # One detected star for each stored star: the one nearest the median shift.
    chosen: dict[int, tuple[float, int]] = {}
    for member in members:
        star = int(owner[member])
        miss = float(np.hypot(*(shifts[member] - (shift_row, shift_column))))
        if star not in chosen or miss < chosen[star][0]:
            chosen[star] = (miss, int(pair_detected[member]))
    stars = np.array(sorted(chosen))
    from_points = predicted[stars] + (shift_row, shift_column)
    to_points = detected[[chosen[int(star)][1] for star in stars]]
    leftover = to_points - from_points
    rms = float(np.sqrt(np.mean(np.sum(leftover**2, axis=1))))
    rotation_deg, rotation_error_deg = _rotation_between(from_points, to_points, rms)
    alignment = _Alignment(
        shift_row=float(shift_row),
        shift_column=float(shift_column),
        matched_stars=len(stars),
        rms_px=rms,
        rotation_deg=rotation_deg,
        rotation_error_deg=rotation_error_deg,
    )
    if (
        abs(rotation_deg) > MAXIMUM_ROTATION_DEG
        and abs(rotation_deg) > ROTATION_SIGNIFICANCE * rotation_error_deg
    ):
        return (
            None,
            (
                f"the stack's solution is rotated by {rotation_deg:+.3f} deg against the stored reference "
                f"stars, more than the {MAXIMUM_ROTATION_DEG} deg the apertures can follow by a shift alone"
            ),
            alignment,
        )
    moved = wcs.deepcopy()
    # A star predicted at pixel p that is really at p + shift has the sky
    # position the guess gives for p, so the reference pixel moves by the
    # shift.
    moved.wcs.crpix = [wcs.wcs.crpix[0] + shift_column, wcs.wcs.crpix[1] + shift_row]
    return moved, "", alignment


def _rotation_between(from_points: np.ndarray, to_points: np.ndarray, rms_px: float) -> tuple[float, float]:
    """Fit the rotation that turns one set of star positions into another.

    Parameters
    ----------
    from_points, to_points : `numpy.ndarray`
        Shape ``(m, 2)``, (row, column) of the same stars in two solutions.
    rms_px : `float`
        The root mean square distance between the two sets after the shift;
        it sets the error of the angle.

    Returns
    -------
    rotation_deg : `float`
        The angle, in degrees, that best turns `from_points` about their
        center into `to_points` (small-angle, least squares).
    error_deg : `float`
        Its 1-sigma error: the rms per axis divided by the root of the summed
        squared distances of the stars from their center.
    """
    start = from_points - from_points.mean(axis=0)
    end = to_points - to_points.mean(axis=0)
    # With x = column and y = row, the angle is atan2(sum(x1 y2 - y1 x2),
    # sum(x1 x2 + y1 y2)).
    cross = float(np.sum(start[:, 1] * end[:, 0] - start[:, 0] * end[:, 1]))
    dot = float(np.sum(start[:, 1] * end[:, 1] + start[:, 0] * end[:, 0]))
    angle = math.degrees(math.atan2(cross, dot))
    lever = math.sqrt(float(np.sum(start**2)))
    error = math.degrees((rms_px / math.sqrt(2.0)) / lever) if lever > 0 else 180.0
    return angle, error


def measure_stack_noise(
    stack_path: str,
    reference_frame_path: str | None,
    apertures_path: str,
    crop_offset: tuple[int, int] = (0, 0),
    *,
    may_choose_apertures: bool = True,
    depth: StackDepth | None = None,
) -> StackNoiseOutcome:
    """Measure a stack's noise in the target's fixed empty apertures.

    Parameters
    ----------
    stack_path : `str`
        The finished stack.
    reference_frame_path : `str` or `None`
        The registration reference frame, used for the coordinate solution
        when the stack's header has none, and for the saturation check.
    apertures_path : `str`
        Where the target's apertures are kept. They are created on the first
        stack and reused on every later one made with the same camera and
        optic.
    crop_offset : `tuple` [`int`, `int`], optional
        The (columns, rows) cut from the stack's left and top edges when its
        edges were trimmed.
    may_choose_apertures : `bool`, optional
        True for a library stack, which chooses the target's apertures when
        there are none and replaces them when it is much deeper (see
        `REPLACEMENT_DEPTH_FACTOR`). False for a subset stack, which only
        measures in the apertures a library stack chose and gives a reason
        when there are none or they are of an older version.
    depth : `StackDepth`, optional
        This stack's frame counts. They are kept with a set this stack
        chooses, and decide whether a stored set is replaced. Without them a
        stored set is never replaced.

    Returns
    -------
    outcome : `StackNoiseOutcome`
        The measurement, or the reason there is none (no solution, an
        unreadable file, too few apertures or reference stars, a solution that
        does not fit the stored stars or is rotated too far, or a pixel scale
        unlike the one the apertures were chosen at).
    """
    plane = read_stack_plane(stack_path)
    if plane is None:
        return StackNoiseOutcome(None, "the stack file holds no pixels")
    # The plane is read once; the sky level is taken from it here so no later
    # step has to read the stack again.
    outcome = _measure_noise_in_plane(
        plane, stack_path, reference_frame_path, apertures_path, crop_offset, may_choose_apertures, depth
    )
    return replace(outcome, stack_sky_level=sky_level_of_plane(plane))


def _measure_noise_in_plane(
    plane: np.ndarray,
    stack_path: str,
    reference_frame_path: str | None,
    apertures_path: str,
    crop_offset: tuple[int, int],
    may_choose_apertures: bool,
    depth: StackDepth | None,
    subtracted_plane: np.ndarray | None = None,
) -> StackNoiseOutcome:
    """Measure a stack's noise in the target's fixed apertures on its plane.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack as one 2-D image (see `measure_stack_noise`).
    stack_path : `str`
        The finished stack, for its header and name.
    reference_frame_path : `str` or `None`
        The registration reference frame.
    apertures_path : `str`
        Where the target's apertures are kept.
    crop_offset : `tuple` [`int`, `int`]
        The (columns, rows) cut from the stack's left and top edges.
    may_choose_apertures : `bool`
        See `measure_stack_noise`.
    depth : `StackDepth` or `None`
        See `measure_stack_noise`.
    subtracted_plane : `numpy.ndarray`, optional
        A second stack on the same pixel grid. When given, the apertures are
        put on ``plane`` (which holds the stars the positions are checked
        against) and the sums are taken on ``plane`` minus this image, with
        every pixel that is exactly zero in either image left at zero (the
        empty edge). See `measure_difference_noise`.

    Returns
    -------
    outcome : `StackNoiseOutcome`
        The measurement, or the reason there is none.
    """
    stack_header = read_header(stack_path)
    wcs = _celestial_wcs(stack_header)
    wcs_source = "stack header"
    if wcs is None:
        if not reference_frame_path:
            return StackNoiseOutcome(None, NO_WCS_REASON)
        wcs, reason = _reference_frame_wcs(reference_frame_path, plane, crop_offset)
        wcs_source = "registration reference frame"
        if wcs is None:
            return StackNoiseOutcome(None, reason)
    reference_header = read_header(reference_frame_path) if reference_frame_path else fits.Header()
    setup_key = _setup_key(stack_header, reference_header)
    saturation = _saturation_check(reference_frame_path, crop_offset)

    aperture_set = load_apertures(apertures_path, setup_key)
    created = False
    keep_note = ""
    if aperture_set is None:
        if not may_choose_apertures:
            return StackNoiseOutcome(None, load_apertures_with_reason(apertures_path, setup_key)[1])
        chosen, choice_reason = choose_apertures_with_reason(
            plane, wcs, wcs_source, os.path.basename(stack_path), setup_key, saturation, depth
        )
        if chosen is None:
            return StackNoiseOutcome(None, choice_reason)
        aperture_set = _store_apertures_once(apertures_path, chosen)
        created = aperture_set is chosen
    elif may_choose_apertures and _is_much_deeper(depth, aperture_set):
        chosen, choice_reason = choose_apertures_with_reason(
            plane, wcs, wcs_source, os.path.basename(stack_path), setup_key, saturation, depth
        )
        if chosen is None:
            keep_note = (
                f"A set from this deeper stack could not be chosen ({choice_reason}); the older set was kept."
            )
        else:
            replaced = aperture_set
            aperture_set = _store_apertures_once(apertures_path, chosen, replace_identity=replaced.identity)
            created = aperture_set is chosen
            if created:
                logger.info(
                    "Replaced the noise apertures %s of %s by %s: this stack is at least %.0f times as deep.",
                    replaced.identity,
                    apertures_path,
                    chosen.identity,
                    REPLACEMENT_DEPTH_FACTOR,
                )
    scale = _pixel_scale_arcsec(wcs)
    if (
        abs(scale - aperture_set.pixel_scale_arcsec)
        > MAXIMUM_PIXEL_SCALE_CHANGE * aperture_set.pixel_scale_arcsec
    ):
        return StackNoiseOutcome(
            None,
            f"this stack's pixel scale ({scale:.2f} arcsec) differs from the "
            f"{aperture_set.pixel_scale_arcsec:.2f} arcsec the stored apertures were chosen at",
        )

    position_note = ""
    if not created:
        # A header's solution can be a first guess even when it exists, and
        # these apertures were chosen on another stack's solution. Put them
        # on this stack's stars, whatever the header says.
        wcs, reason, alignment = _move_guess_onto_reference_stars(plane, wcs, aperture_set.reference_sky)
        if wcs is None:
            return StackNoiseOutcome(None, reason)
        position_note = alignment.describe() if alignment is not None else ""
    position_note = " ".join(part for part in (position_note, keep_note) if part)
    summed_plane = plane
    if subtracted_plane is not None:
        summed_plane = plane - subtracted_plane
        summed_plane[(plane == 0) | (subtracted_plane == 0)] = 0.0
    all_sums = _sums_at(summed_plane, wcs, aperture_set.aperture_sky)
    sums = all_sums[np.isfinite(all_sums)]
    if sums.size < MINIMUM_USABLE_APERTURES:
        return StackNoiseOutcome(None, f"only {sums.size} of the stored apertures fall on this stack")
    ratios, saturated_count = _reference_ratios(plane, wcs, aperture_set, saturation)
    if ratios.size < MINIMUM_USABLE_REFERENCE_STARS:
        return StackNoiseOutcome(
            None,
            f"only {ratios.size} of the reference stars can be used on this stack "
            f"({saturated_count} are saturated in its reference frame)",
        )

    spread = MAD_TO_SIGMA * float(np.median(np.abs(sums - np.median(sums))))
    spread_error = MAD_SIGMA_ERROR_FACTOR * spread / math.sqrt(sums.size)
    zero_point = float(np.median(ratios))
    zero_point_error = (
        MEDIAN_ERROR_FACTOR
        * MAD_TO_SIGMA
        * float(np.median(np.abs(ratios - zero_point)))
        / math.sqrt(ratios.size)
    )
    baseline_flux = float(np.median(aperture_set.reference_baseline_flux))
    reference_flux = zero_point * baseline_flux
    if spread <= 0 or reference_flux <= 0:
        return StackNoiseOutcome(None, "the apertures or the reference stars gave no usable spread or flux")
    relative_zero_point_error = zero_point_error / zero_point
    normalized = spread / reference_flux
    relative_error = math.hypot(spread_error / spread, relative_zero_point_error)
    return StackNoiseOutcome(
        StackNoiseResult(
            noise_adu=spread,
            noise_uncertainty_adu=spread_error,
            aperture_count=int(sums.size),
            reference_flux_adu=reference_flux,
            reference_uncertainty_adu=baseline_flux * zero_point_error,
            reference_star_count=int(ratios.size),
            normalized_noise=normalized,
            normalized_uncertainty=normalized * relative_error,
            wcs_source=wcs_source,
            apertures_created=created,
            reference_zero_point=zero_point,
            reference_zero_point_uncertainty=relative_zero_point_error,
            reference_aperture_radius_px=aperture_set.reference_radius_px,
            saturated_reference_stars=saturated_count,
            position_note=position_note,
            aperture_sums=tuple(float(value) for value in all_sums),
            aperture_set_origin=aperture_set.created_from,
            aperture_identity=aperture_set.identity,
            aperture_definition_note=aperture_set.describe_definition(),
            saturation_source=aperture_set.saturation_source
            if created
            else (saturation.source if saturation is not None else aperture_set.saturation_source),
        )
    )


def _reference_ratios(
    plane: np.ndarray, wcs: WCS, aperture_set: ApertureSet, saturation: SaturationCheck | None
) -> tuple[np.ndarray, int]:
    """Compare each reference star's brightness with its first-stack value.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack as a 2-D image.
    wcs : `astropy.wcs.WCS`
        The stack's sky solution, already moved onto the stored stars.
    aperture_set : `ApertureSet`
        The stored set, with the reference stars and their first-stack
        brightness.
    saturation : `SaturationCheck` or `None`
        Tells which stars reach the saturation level in the reference frame.
        With `None`, no star is left out for saturation.

    Returns
    -------
    ratios : `numpy.ndarray`
        For each star that could be measured and is not saturated, its
        brightness in the large reference aperture divided by its brightness
        on the stack that chose the apertures.
    saturated_count : `int`
        How many stored stars were left out for being saturated.
    """
    ratios = []
    saturated_count = 0
    for (column, row), baseline in zip(
        _sky_to_pixels(wcs, aperture_set.reference_sky), aperture_set.reference_baseline_flux, strict=True
    ):
        if not (math.isfinite(column) and math.isfinite(row)):
            continue
        if saturation is not None and saturation.is_saturated(float(row), float(column)):
            saturated_count += 1
            continue
        flux = signed_aperture_sum(
            plane,
            float(column),
            float(row),
            aperture_set.reference_radius_px,
            aperture_set.reference_ring_inner_px,
            aperture_set.reference_ring_outer_px,
        )
        if flux is not None and flux > 0 and baseline > 0:
            ratios.append(flux / baseline)
    return np.asarray(ratios, dtype=float), saturated_count


def _sums_at(plane: np.ndarray, wcs: WCS, sky: np.ndarray) -> np.ndarray:
    """Measure the sky-subtracted sum at each stored sky position.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack as a 2-D image.
    wcs : `astropy.wcs.WCS`
        The stack's sky solution.
    sky : `numpy.ndarray`
        Shape ``(n, 2)``, (RA, Dec) in degrees.

    Returns
    -------
    sums : `numpy.ndarray`
        Shape ``(n,)``: the sum at each position, in the order of `sky`, and
        NaN where `signed_aperture_sum` did not work.
    """
    values = np.full(len(sky), np.nan)
    for index, (column, row) in enumerate(_sky_to_pixels(wcs, sky)):
        if not (math.isfinite(column) and math.isfinite(row)):
            continue
        value = signed_aperture_sum(plane, float(column), float(row))
        if value is not None:
            values[index] = value
    return values
