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

A stack combined from exposure groups may have no named reference frame (its
registration lines can leave the reference unclear). The caller can then give
member frames of the group the stack was lined up with. Such a frame is not the
reference, so it sits a few to a few hundred pixels off the stack. Its solution
is moved onto the stack by the shift most of the stack's brightest stars agree
on, and then checked the same way as the reference frame's. The source is
recorded as `MEMBER_FRAME_WCS_SOURCE`, with the shift, so a stored result
always says which route gave its coordinates.

When none of those gives a solution (the raw frames carry none, which is the
case for a target imaged without a plate-solving mount or camera program), the
last source is the stack itself. The stack is plate-solved in memory by the
same code the astrometry step uses (`_plate_solve_stack_wcs`). The stack's
header is not changed: the astrometry step writes its own solution later, and
a header changed here could make the stack look new to the checks that compare
a stack against its earlier record. The solved grid is the stack's own, so no
trim offset or shift applies and no frame has to line up with it. A solve that
fails (no match, no index files, a time limit) gives the usual "no result",
with a reason that says the solve was tried. The source is recorded as
`PLATE_SOLVE_WCS_SOURCE`.

Even a stack's own header is checked. A frame's own "solution" is often only a
first guess: the camera software (Ekos) writes the position the mount was told
to point at, not a plate solution, and Siril copies it into the stack. The sky
position of a pixel can then be wrong by tens of pixels, and by a different
amount on every night. So when a target already has stored apertures, every
stack, whatever its header says, is first moved onto them: the stored
reference stars are looked for near where the solution predicts them, at
each of a few dozen small trial rotations about the image center (up to
`MAXIMUM_ROTATION_DEG`, in steps that keep the rotation's effect under a pixel
everywhere). The shift and rotation that most of the stars agree on are found,
refined by a least-squares fit to those stars, and the solution is shifted and
turned by them. A rotation that is not significant is dropped, so a pure shift
stays a pure shift. The stack is refused when the fitted rotation is larger
than `MAXIMUM_ROTATION_DEG` and more than three times its own error. The
leftover position error is reported in the `position_note`. If fewer than a few
stars agree, no noise is measured.

A target keeps one set of apertures for each camera, optic and reference
exposure length (see `ApertureSet.setup_key`). A stack made with other
equipment, or lined up on a frame of another exposure length (for a stack of
exposure groups, the heaviest group's), starts a new set. A stack that may not
choose apertures (a subset stack or an odd–even difference job) ignores its own
exposure length and uses the set the target's library stack recorded for the
same camera and optic (`record_library_set_key`); an older file with no record
falls back to the deepest set. To start over for a target, call
`reset_apertures` or delete
``quality_apertures.json`` beside its stacks; the next stack chooses new
apertures. Noise values measured with different sets are not comparable.

The same reference stars also give the stack's flux against the registration
reference frame (`ReferenceFrameFluxRatio`). Siril multiplies each frame by a
scale it does not save, and the reference frame's own scale is 1 by Siril's
definition, so the ratio of a star's flux in the stack to its flux in that
frame carries the stack's overall scale. `normalization_scale.py` turns it into
the scale.

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
from collections.abc import Sequence
from dataclasses import dataclass, replace
from subprocess import SubprocessError  # ruff: ignore[suspicious-subprocess-import] -- only to name the solver's time-limit error

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from scipy.spatial import cKDTree

from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.drivers.fits_access import collapse_to_2d, read_data, read_header
from astrometricslib.models.camera_profile import ProvenanceKind
from astrometricslib.pipelines.shared.applied_camera_profile import camera_name_from_header
from astrometricslib.pipelines.stacking.post_processing.aperture_size_noise import (
    ApertureSizeNoiseOutcome,
    measure_aperture_size_noise,
)
from astrometricslib.pipelines.stacking.post_processing.empty_apertures import (
    DETECTED_STAR_COUNT,
    MINIMUM_USABLE_APERTURES,
    MINIMUM_USABLE_REFERENCE_STARS,
    PLATE_SOLVE_WCS_SOURCE,
    REPLACEMENT_DEPTH_FACTOR,
    ApertureSet,
    StackDepth,
    choose_apertures_with_reason,
    find_library_set_key,
    is_much_deeper,
    load_apertures,
    load_apertures_with_reason,
    record_library_set_key,
    setup_key,
    store_apertures_once,
)
from astrometricslib.pipelines.stacking.post_processing.stack_image import (
    MAD_SIGMA_ERROR_FACTOR,
    MAD_TO_SIGMA,
    MEDIAN_ERROR_FACTOR,
    SaturationCheck,
    celestial_wcs,
    pixel_scale_arcsec,
    read_stack_plane,
    robust_sigma,
    signed_aperture_sum,
    sky_level_of_plane,
    sky_to_pixels,
)
from astrometricslib.pipelines.stacking.processing.group_alignment import detect_star_centroids
from astrometricslib.utilities.exceptions import DATA_ERRORS, ONLINE_QUERY_ERRORS, PlateSolveFailedError

logger = logging.getLogger(__name__)

__all__ = [
    "ReferenceFrameFluxRatio",
    "StackNoiseOutcome",
    "StackNoiseResult",
    "measure_noise_in_plane",
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
# stars that is searched for and accepted. The apertures are placed by a shift
# and a turn about the image center, so a rotation is followed rather than
# refused, up to this limit. A header's rotation was off by 0.07 to 0.21 deg on
# M 81's 2026-01 frames, measured against real plate solves of the same frames;
# 0.5 deg is more than twice the largest of those. Beyond it a header is
# probably not a solution at all. Searching a rotation costs chance agreement:
# the number of trial rotations (about 2 * 0.5 deg / 0.027 deg = 37 for a
# 3000 px stack) multiplies the chance that a wrong shift, rotation pair
# finds MINIMUM_USABLE_REFERENCE_STARS agreeing stars. With about 1500 pairs
# inside the search radius, five stars agreeing by chance at one trial is
# unlikely, and the factor of 37 does not change that (not measured). A
# design estimate. The M 81 case (3 of 10 stars agree by shift only, 9 of 10
# with a rotation of 0.16 to 0.24 deg) is the motivating observation, not a
# validation.
MAXIMUM_ROTATION_DEG = 0.5

# Trial rotations are this many pixels apart at the farthest corner of the
# image (the step is this distance divided by the corner's distance from the
# center). The nearest trial is then at most half of it off anywhere in the
# image, well inside SEED_AGREEMENT_PX, so a star at the right rotation always
# votes with the others.
ROTATION_STEP_PX = 1.0

# The rotation must also exceed this many times its own error to count.
ROTATION_SIGNIFICANCE = 3.0

# A fitted move smaller than both of these leaves the solution as it is, so a
# rerun on the same stack puts the apertures on the same pixels. Shift: a
# 0.05 px move shifts an aperture by 1/80 of its 4 px radius. Rotation: 0.001
# deg is 0.026 px at the farthest corner of a 3000 px image. Moves this small
# are inside the star-position error anyway, but they changed which pixels
# each of 99 apertures covered: on M 13, shifts of 0.01 to 0.03 px changed the
# MAD of the 99 apertures by 1.5 to 1.9% rms, and one rerun changed the
# odd-even difference noise by 5% (sampling scatter). Design estimates;
# validated only by that M 13 check.
NEGLIGIBLE_SHIFT_PX = 0.05
NEGLIGIBLE_ROTATION_DEG = 0.001

NO_WCS_REASON = "no WCS for this stack"

# The ``wcs_source`` of a stack whose solution was taken from a member frame
# of the group it was lined up with, because no reference frame was named.
MEMBER_FRAME_WCS_SOURCE = "member frame of the reference exposure group"

# How many member frames are tried, in the order given, before giving up. Each
# try reads one frame and runs the star check, so a few are enough: a frame
# that fails the check is a poor frame (clouds, trails), and the next is
# unlikely to be as bad. A design estimate.
MEMBER_FRAME_ATTEMPTS = 5

# Given when apertures must be chosen on a group stack but the area every
# exposure group covers cannot be laid under the trimmed stack.
UNCOVERED_AREA_MISFIT_REASON = "the area every exposure group covers does not fit under this stack"


@dataclass(frozen=True)
class ReferenceFrameFluxRatio:
    """Compare the reference stars' flux in the stack and the frame.

    For each usable reference star the flux is taken in the stack and in the
    registration reference frame, in the same large aperture with the same sky
    ring. The ratio is in stack units per ADU. It is the product of the unit
    conversion (1 / 65535 stack unit per ADU), the stack's own scale from
    Siril's normalization, and, for a stack of exposure groups, the exposure
    arithmetic of the combine (see `normalization_scale.py`).

    Clouds or haze in some frames lower the stack's flux against a clear
    reference frame, so the ratio, and the scale made from it, read low. A
    reference frame that is clearer or cloudier than the average frame moves
    it the other way.

    Parameters
    ----------
    ratio : `float` or `None`
        The median, over the stars, of the stack's flux over the frame's flux.
        `None` when it could not be measured.
    uncertainty : `float`
        Its 1-sigma uncertainty from the scatter of the stars' ratios (the
        standard error of a median). 0 when there is no ratio.
    star_count : `int`
        How many stars gave a ratio.
    saturated_count : `int`
        How many stored reference stars were left out for being saturated in
        the reference frame.
    reference_exposure_seconds : `float` or `None`
        The exposure length of the reference frame, from its header.
    reason : `str`
        Why there is no ratio. Empty when there is one.
    reference_frame_path : `str` or `None`
        The registration reference frame the ratio was taken against. It is
        used to tell whether that frame is one of the reference group's
        frames (see `normalization_scale.py`).
    """

    ratio: float | None
    uncertainty: float = 0.0
    star_count: int = 0
    saturated_count: int = 0
    reference_exposure_seconds: float | None = None
    reason: str = ""
    reference_frame_path: str | None = None


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
    reference_frame_flux_ratio : `ReferenceFrameFluxRatio` or `None`
        The reference stars' flux in the stack over their flux in the
        registration reference frame, or `None` when the measurement was not
        tried (a difference image).
    aperture_pixel_positions : `tuple` [`tuple` [`float`, `float`], ...]
        The (column, row) of every stored aperture on this stack, in the
        stored order; NaN where the position could not be worked out.
    added_aperture_sums : `tuple` [`float`, ...]
        For a difference image only: the sky-subtracted sum at every stored
        aperture on the sum image A + B of the two stacks (the stacks added,
        not halved), in the stored order, NaN where not usable. Empty
        otherwise.
    target_pixel_position : `tuple` [`float`, `float`] or `None`
        For a difference image only, when the caller gave the target's sky
        position: its (column, row) on this stack.
    size_noise : `ApertureSizeNoiseOutcome` or `None`
        How the noise grows with the aperture size (see
        `aperture_size_noise.py`), for a stack that may choose the apertures
        (a library stack). `None` when it was not tried.
    pixel_scale_arcsec : `float`
        The pixel scale of the stack's solution, in arcseconds per pixel
        (0 when not known). The depth limits turn an area in arcseconds into
        pixels with it.
    reference_star_fluxes : `tuple` [`tuple` [`float`, `float`, `float`], ...]
        For each usable reference star: right ascension and declination in
        degrees, and its sky-subtracted sum in the large reference aperture
        of this stack, in the stack's own units (the units of `noise_adu`).
        The depth limits match these stars to Gaia to find the stack's zero
        point.
    sky_positions_from_plate_solve : `bool`
        Whether the aperture set's stored sky positions (and so
        `reference_star_fluxes`) came from a plate solve of the stack that
        chose it. When `False` the depth limits do not match them to Gaia.
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
    reference_frame_flux_ratio: ReferenceFrameFluxRatio | None = None
    aperture_pixel_positions: tuple[tuple[float, float], ...] = ()
    added_aperture_sums: tuple[float, ...] = ()
    target_pixel_position: tuple[float, float] | None = None
    size_noise: ApertureSizeNoiseOutcome | None = None
    pixel_scale_arcsec: float = 0.0
    reference_star_fluxes: tuple[tuple[float, float, float], ...] = ()
    sky_positions_from_plate_solve: bool = True


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
        The shift applied, in pixels, after the rotation about the image
        center.
    matched_stars : `int`
        How many stored reference stars agreed on it.
    rms_px : `float`
        The root mean square distance, after the shift, between those stars
        and the detected stars they matched.
    rotation_deg : `float`
        The rotation about the image center that was applied, in degrees
        (zero when the stars show none that is significant).
    rotation_error_deg : `float`
        The 1-sigma error of the rotation fitted to the matched stars.
    left_unchanged : `bool`
        True when the fitted move was below `NEGLIGIBLE_SHIFT_PX` and
        `NEGLIGIBLE_ROTATION_DEG`, so the solution was returned as it was. The
        shift and rotation fields then hold the fitted values that were not
        applied.
    """

    shift_row: float
    shift_column: float
    matched_stars: int
    rms_px: float
    rotation_deg: float
    rotation_error_deg: float
    left_unchanged: bool = False

    def describe(self) -> str:
        """Say in words how the solution was moved.

        Returns
        -------
        note : `str`
            One sentence with the shift, the stars that agreed, their leftover
            position error and the rotation.
        """
        if self.left_unchanged:
            return (
                f"The stack's sky positions were left as they were: the fitted move "
                f"({self.shift_row:+.2f} rows, {self.shift_column:+.2f} columns, "
                f"{self.rotation_deg:+.4f} deg) "
                f"is below {NEGLIGIBLE_SHIFT_PX} px and {NEGLIGIBLE_ROTATION_DEG} deg, so reruns place the "
                f"apertures on the same pixels; {self.matched_stars} stored reference stars agree to "
                f"{self.rms_px:.2f} px rms."
            )
        return (
            f"The stack's sky positions were moved by {self.shift_row:+.1f} rows and "
            f"{self.shift_column:+.1f} columns and turned by {self.rotation_deg:+.3f} deg about the image "
            f"center onto {self.matched_stars} stored reference stars; after the move they agree to "
            f"{self.rms_px:.2f} px rms (rotation error {self.rotation_error_deg:.3f} deg)."
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
    usable_peak = profile.usable_peak_level_adu
    limit = float(usable_peak.value)
    stand_in = " (generic stand-in: the camera has no profile)" if profile.is_generic_fallback else ""
    notes = []
    if usable_peak.provenance.kind == ProvenanceKind.ASSUMED:
        notes.append("the limit is assumed, not measured")
    if limit >= float(profile.clip_ceiling_adu.value):
        notes.append("the check fires only at the sensor's clip level itself, on the reference frame only")
    assumed = f" ({'; '.join(notes)})" if notes else ""
    return SaturationCheck(
        frame_plane=collapse_to_2d(np.asarray(data, dtype=np.float32)),
        crop_offset=crop_offset,
        limit_adu=limit,
        source=f"peak in the registration reference frame at or above {limit:g} ADU of the "
        f"{profile.camera_name} camera profile{stand_in}{assumed}",
    )


def _bright_star_matches(
    stack_plane: np.ndarray, frame_plane: np.ndarray, crop_offset: tuple[int, int]
) -> tuple[int, int, int]:
    """Count bright stack stars that sit where the reference frame has stars.

    Parameters
    ----------
    stack_plane : `numpy.ndarray`
        The stack's image.
    frame_plane : `numpy.ndarray`
        The registration reference frame as one 2-D image.
    crop_offset : `tuple` [`int`, `int`]
        The (columns, rows) cut from the stack's left and top edges.

    Returns
    -------
    matches : `int`
        How many of the stack's brightest stars have a frame star within
        `WCS_CHECK_TOLERANCE_PX`.
    stack_star_count : `int`
        How many bright stars were found in the stack (at most
        `WCS_CHECK_STAR_COUNT`).
    frame_star_count : `int`
        How many stars were found in the frame. When it is 0, `matches` is 0.
    """
    stack_stars = detect_star_centroids(stack_plane, count=WCS_CHECK_STAR_COUNT)
    # Saturated stars stay in: the stack's brightest stars are bright in
    # the single frame too, and the frame's own 99.99th percentile would cut
    # them.
    frame_stars = detect_star_centroids(frame_plane, count=DETECTED_STAR_COUNT, skip_saturated=False)
    if len(stack_stars) == 0 or len(frame_stars) == 0:
        return 0, len(stack_stars), len(frame_stars)
    # Stack pixel (row, column) is frame pixel (row + rows cut,
    # column + columns cut).
    expected = stack_stars + np.array([crop_offset[1], crop_offset[0]], dtype=float)
    distance = cKDTree(frame_stars).query(expected)[0]
    return int(np.count_nonzero(distance <= WCS_CHECK_TOLERANCE_PX)), len(stack_stars), len(frame_stars)


def _frame_lines_up(frame_path: str, stack_plane: np.ndarray, crop_offset: tuple[int, int]) -> bool:
    """Say whether a frame shares the stack's pixel grid, by its bright stars.

    Parameters
    ----------
    frame_path : `str`
        The frame, normally the registration reference frame.
    stack_plane : `numpy.ndarray`
        The stack's image.
    crop_offset : `tuple` [`int`, `int`]
        The (columns, rows) cut from the stack's left and top edges.

    Returns
    -------
    lines_up : `bool`
        True when at least `WCS_CHECK_MINIMUM_MATCHES` of the stack's
        brightest stars have a star in the frame (the check
        `_reference_frame_wcs` makes). False when the frame cannot be read.
    """
    try:
        frame_data = read_data(frame_path)
    except OSError, ValueError:
        return False
    if frame_data is None:
        return False
    frame_plane = collapse_to_2d(np.asarray(frame_data, dtype=np.float64))
    matches, stack_star_count, frame_star_count = _bright_star_matches(stack_plane, frame_plane, crop_offset)
    enough_stars = stack_star_count >= WCS_CHECK_MINIMUM_MATCHES and frame_star_count > 0
    return enough_stars and matches >= WCS_CHECK_MINIMUM_MATCHES


def _plate_solve_stack_wcs(stack_path: str) -> tuple[WCS | None, str]:
    """Plate-solve the stack itself, in memory, as the last source.

    This runs the astrometry step's own solver path (`AstrometryPipeline`): the
    same configuration, the same index lookup, the same scale and position
    hints taken from the stack's header, the same Gaia refit and the same
    time limit (`solve_timeout` of 300 s for each of the hinted and the blind
    try, set in `StarIdentifier.process_image`). A solve with a good star
    list normally takes a few seconds to a minute; a failed one can use the
    whole limit. The stack file is only read: the solution is not written to
    its header (see the module description).

    Parameters
    ----------
    stack_path : `str`
        The finished stack.

    Returns
    -------
    wcs : `astropy.wcs.WCS` or `None`
        The solution on the stack's own pixel grid, or `None`.
    reason : `str`
        Why there is none, saying the solve was tried. Empty when there is one.
    """
    # Imported here: the astrometry package is large, and a stack that has a
    # solution never needs it.
    from astrometricslib.pipelines.astrometry.pipeline import AstrometryPipeline

    try:
        context = AstrometryPipeline().process(stack_path, attempt_plate_solving=True)
    except (
        PlateSolveFailedError,
        RuntimeError,
        SubprocessError,
        *DATA_ERRORS,
        *ONLINE_QUERY_ERRORS,
    ) as error:
        # A solve is a by-product here: the errors a solve can raise (no match,
        # a missing index or program, a time limit, an online query) must not
        # fail the stack. Anything else is a bug and is caught, with the stack
        # still recorded, by `record_quality`.
        logger.warning("Plate-solving the stack %s failed: %s", stack_path, error)
        return None, f"plate-solving the stack itself was tried and failed ({type(error).__name__}: {error})"
    if context.wcs is None or context.wcs_is_header_fallback:
        return None, "plate-solving the stack itself was tried and found no solution"
    celestial = context.wcs.celestial if context.wcs.has_celestial else None
    if celestial is None:
        return None, "plate-solving the stack itself was tried and gave no sky solution"
    return celestial, ""


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
    frame_wcs = celestial_wcs(header)
    if frame_wcs is None:
        return None, NO_WCS_REASON
    shifted = frame_wcs.deepcopy()
    shifted.wcs.crpix = [frame_wcs.wcs.crpix[0] - crop_offset[0], frame_wcs.wcs.crpix[1] - crop_offset[1]]
    frame_data = read_data(reference_frame_path)
    if frame_data is None:
        return None, NO_WCS_REASON
    frame_plane = collapse_to_2d(np.asarray(frame_data, dtype=np.float64))
    matches, stack_star_count, frame_star_count = _bright_star_matches(stack_plane, frame_plane, crop_offset)
    if stack_star_count < WCS_CHECK_MINIMUM_MATCHES or frame_star_count == 0:
        return None, "the reference frame's solution could not be checked: too few stars were found"
    if matches < WCS_CHECK_MINIMUM_MATCHES:
        return None, (
            f"the reference frame does not line up with the stack ({matches} of {stack_star_count} "
            "bright stars match), so its coordinate solution does not apply to the stack"
        )
    return shifted, ""


def _member_frame_wcs(
    member_frame_paths: Sequence[str], stack_plane: np.ndarray, crop_offset: tuple[int, int]
) -> tuple[WCS | None, str, str, str | None, tuple[int, int]]:
    """Take the plate solution of a member frame, moved onto the stack.

    A member frame of the group the stack was lined up with is not the
    reference frame, so its stars sit at their own place, off the stack's by
    the frame's shift. For each frame in turn (up to `MEMBER_FRAME_ATTEMPTS`),
    every pair of a bright stack star and a detected frame star suggests a
    shift. The shift most stars agree on is taken, and the frame is accepted
    only when at least `WCS_CHECK_MINIMUM_MATCHES` different stack stars agree
    within `WCS_CHECK_TOLERANCE_PX`. When two shifts tie, the smaller one wins.
    The frame's solution is then moved by that shift, onto the stack's pixel
    grid.

    Parameters
    ----------
    member_frame_paths : `Sequence` of `str`
        Frames of the group the stack was lined up with, best first.
    stack_plane : `numpy.ndarray`
        The stack's image.
    crop_offset : `tuple` [`int`, `int`]
        The (columns, rows) cut from the stack's left and top edges.

    Returns
    -------
    wcs : `astropy.wcs.WCS` or `None`
        The solution on the stack's pixel grid, or `None`.
    reason : `str`
        Why there is none. Empty when there is one.
    wcs_source : `str`
        `MEMBER_FRAME_WCS_SOURCE` with the frame's name and the shift applied.
        Empty when there is no solution.
    frame_path : `str` or `None`
        The frame whose solution was taken, or `None`.
    frame_crop_offset : `tuple` [`int`, `int`]
        The (columns, rows) to add to a stack pixel to reach the same sky
        place in that frame (rounded to whole pixels). Equal to ``crop_offset``
        when there is no solution.
    """
    stack_stars = detect_star_centroids(stack_plane, count=WCS_CHECK_STAR_COUNT)
    if len(stack_stars) < WCS_CHECK_MINIMUM_MATCHES:
        return (
            None,
            "the member frames' solutions could not be checked: too few stars were found",
            "",
            None,
            crop_offset,
        )
    expected = stack_stars + np.array([crop_offset[1], crop_offset[0]], dtype=float)
    reasons = []
    for frame_path in list(member_frame_paths)[:MEMBER_FRAME_ATTEMPTS]:
        try:
            frame_wcs = celestial_wcs(read_header(frame_path))
            frame_data = read_data(frame_path)
        except OSError, ValueError:
            continue
        if frame_wcs is None or frame_data is None:
            continue
        frame_stars = detect_star_centroids(
            collapse_to_2d(np.asarray(frame_data, dtype=np.float64)),
            count=DETECTED_STAR_COUNT,
            skip_saturated=False,
        )
        if len(frame_stars) == 0:
            continue
        shift = _agreed_shift(expected, frame_stars)
        if shift is None:
            reasons.append(os.path.basename(frame_path))
            continue
        # A stack pixel (row, column) is frame pixel + cut - shift.
        moved = frame_wcs.deepcopy()
        moved.wcs.crpix = [
            frame_wcs.wcs.crpix[0] - crop_offset[0] + shift[1],
            frame_wcs.wcs.crpix[1] - crop_offset[1] + shift[0],
        ]
        source = (
            f"{MEMBER_FRAME_WCS_SOURCE} ({os.path.basename(frame_path)}, "
            f"moved by {shift[0]:+.1f} rows and {shift[1]:+.1f} columns onto the stack's stars)"
        )
        frame_offset = (crop_offset[0] - round(shift[1]), crop_offset[1] - round(shift[0]))
        return moved, "", source, frame_path, frame_offset
    tried = f" (tried {', '.join(reasons)})" if reasons else ""
    return (
        None,
        f"no member frame of the reference exposure group lines up with the stack{tried}, "
        "so no coordinate solution applies to the stack",
        "",
        None,
        crop_offset,
    )


def _agreed_shift(expected: np.ndarray, frame_stars: np.ndarray) -> tuple[float, float] | None:
    """Find the shift that puts the most stack stars onto frame stars.

    Parameters
    ----------
    expected : `numpy.ndarray`
        Shape ``(n, 2)``: the (row, column) of the stack's bright stars on the
        frame's pixel grid (the trim offset already added).
    frame_stars : `numpy.ndarray`
        Shape ``(m, 2)``: the (row, column) of the frame's stars.

    Returns
    -------
    shift : `tuple` [`float`, `float`] or `None`
        The (rows, columns) by which the frame's stars lie off the expected
        places (``expected - frame``), averaged over the stars that agree, or
        `None` when fewer than `WCS_CHECK_MINIMUM_MATCHES` stars agree.
    """
    star_index, frame_index = np.meshgrid(
        np.arange(len(expected)), np.arange(len(frame_stars)), indexing="ij"
    )
    shifts = expected[star_index.ravel()] - frame_stars[frame_index.ravel()]
    owner = star_index.ravel()
    near = np.hypot(shifts[:, 0], shifts[:, 1]) <= SEED_SEARCH_RADIUS_PX
    shifts, owner = shifts[near], owner[near]
    if len(shifts) == 0:
        return None
    neighbors = cKDTree(shifts).query_ball_point(shifts, r=WCS_CHECK_TOLERANCE_PX)
    best = None
    for index, members in enumerate(neighbors):
        agreeing_stars = len(set(owner[members].tolist()))
        size = float(np.hypot(*shifts[index]))
        if best is None or (agreeing_stars, -size) > (best[0], -best[1]):
            best = (agreeing_stars, size, members)
    if best is None or best[0] < WCS_CHECK_MINIMUM_MATCHES:
        return None
    median_shift = shifts[best[2]].mean(axis=0)
    return float(median_shift[0]), float(median_shift[1])


def _vote_for_shift(
    predicted: np.ndarray, detected: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int] | None:
    """Vote on the shift that puts most predicted stars onto detected stars.

    Every pair of a predicted star and a detected star suggests a shift. The
    true shift is the one the most different predicted stars suggest together
    (within `SEED_AGREEMENT_PX`); when two shifts tie, the smaller one wins.

    Parameters
    ----------
    predicted : `numpy.ndarray`
        Shape ``(k, 2)``: (row, column) of the stored stars, where the
        solution (and any trial rotation) puts them.
    detected : `numpy.ndarray`
        Shape ``(m, 2)``: (row, column) of the detected stars.

    Returns
    -------
    vote : `tuple` or `None`
        `None` when no shift is within `SEED_SEARCH_RADIUS_PX`. Otherwise
        ``(shifts, owner, pair_detected, members, distinct_count)``: the
        suggested shifts, shape ``(n, 2)``; for each, the index of its
        predicted star and of its detected star; the indices (into
        ``shifts``) of the shifts that agree with the winner; and how many
        different predicted stars take part in that agreement.
    """
    star_index, detected_index = np.meshgrid(
        np.arange(len(predicted)), np.arange(len(detected)), indexing="ij"
    )
    shifts = detected[detected_index.ravel()] - predicted[star_index.ravel()]
    owner = star_index.ravel()
    pair_detected = detected_index.ravel()
    near = np.hypot(shifts[:, 0], shifts[:, 1]) <= SEED_SEARCH_RADIUS_PX
    shifts, owner, pair_detected = shifts[near], owner[near], pair_detected[near]
    if len(shifts) == 0:
        return None
    tree = cKDTree(shifts)
    # Each shift counts the distinct stars among the shifts within the
    # agreement distance of it (itself included). Done on arrays: a list of
    # sets per shift is too slow when it is repeated for every trial rotation.
    pairs = tree.query_pairs(r=SEED_AGREEMENT_PX, output_type="ndarray")
    first = np.concatenate([pairs[:, 0], pairs[:, 1], np.arange(len(shifts))])
    second = np.concatenate([pairs[:, 1], pairs[:, 0], np.arange(len(shifts))])
    star_count = len(predicted)
    distinct_keys = np.unique(first * star_count + owner[second])
    distinct_counts = np.bincount(distinct_keys // star_count, minlength=len(shifts))
    tied = np.flatnonzero(distinct_counts == distinct_counts.max())
    best = int(tied[np.argmin(np.hypot(shifts[tied, 0], shifts[tied, 1]))])
    members = np.asarray(tree.query_ball_point(shifts[best], r=SEED_AGREEMENT_PX))
    return shifts, owner, pair_detected, members, int(distinct_counts[best])


def _rotate_about(points: np.ndarray, center: np.ndarray, angle_rad: float) -> np.ndarray:
    """Turn (row, column) points about a center by an angle.

    The angle has the sense used by `_rotation_between`: with x as the column
    and y as the row, ``x' = x cos - y sin`` and ``y' = x sin + y cos``.

    Parameters
    ----------
    points : `numpy.ndarray`
        Shape ``(n, 2)``, (row, column).
    center : `numpy.ndarray`
        The (row, column) that stays fixed.
    angle_rad : `float`
        The rotation, in radians.

    Returns
    -------
    turned : `numpy.ndarray`
        The turned points, shape ``(n, 2)``, (row, column).
    """
    cosine, sine = math.cos(angle_rad), math.sin(angle_rad)
    offset = points - center
    column = offset[:, 1] * cosine - offset[:, 0] * sine
    row = offset[:, 1] * sine + offset[:, 0] * cosine
    return center + np.column_stack([row, column])


def _turn_and_shift_wcs(wcs: WCS, center_xy: np.ndarray, angle_rad: float, shift_xy: np.ndarray) -> WCS:
    """Turn a solution about a point and shift it.

    A star the old solution predicts at pixel ``p`` is really at
    ``R(p - c) + c + s``, so the new solution must give that pixel
    the sky position the old one gave ``p``. In FITS terms, with ``u`` the
    offset from the reference pixel: the new reference pixel is
    ``c + s + R(crpix - c)`` and the linear part (the CD matrix, or the PC
    matrix when the solution has one) is multiplied by ``R`` inverse.

    A SIP distortion, if the solution has one, is kept as it is. It is a
    polynomial in ``u`` of the old pixel grid, and a rotation of the grid
    would turn it too. The error of not turning it is the rotation (at most
    `MAXIMUM_ROTATION_DEG`, 0.009 radian) times the size of the distortion
    term, which is a few pixels at the corners of a typical SIP fit: about
    0.03 px, far below the 3 px agreement the stars are matched to.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The solution to move. It is not changed.
    center_xy : `numpy.ndarray`
        The fixed point ``c`` as (column, row), zero-based pixel coordinates.
    angle_rad : `float`
        The rotation ``R``, in radians.
    shift_xy : `numpy.ndarray`
        The shift ``s`` as (column, row), in pixels.

    Returns
    -------
    moved : `astropy.wcs.WCS`
        The moved solution.
    """
    cosine, sine = math.cos(angle_rad), math.sin(angle_rad)
    rotation = np.array([[cosine, -sine], [sine, cosine]])
    moved = wcs.deepcopy()
    # FITS reference pixels count from 1, the other coordinates here from 0.
    old_crpix_zero_based = np.asarray(wcs.wcs.crpix, dtype=float) - 1.0
    new_crpix_zero_based = center_xy + shift_xy + rotation @ (old_crpix_zero_based - center_xy)
    moved.wcs.crpix = new_crpix_zero_based + 1.0
    if abs(angle_rad) > 0.0:
        if wcs.wcs.has_cd():
            moved.wcs.cd = np.asarray(wcs.wcs.cd, dtype=float) @ rotation.T
        else:
            moved.wcs.pc = np.asarray(wcs.wcs.get_pc(), dtype=float) @ rotation.T
    return moved


def _move_guess_onto_reference_stars(
    plane: np.ndarray, wcs: WCS, reference_sky: np.ndarray
) -> tuple[WCS | None, str, _Alignment | None]:
    """Shift and turn a solution so it lands on the stored reference stars.

    For each trial rotation about the image center (from minus to plus
    `MAXIMUM_ROTATION_DEG`, in steps that keep the misplacement at the
    farthest corner below one pixel), the predicted star positions are turned
    and every pair of a stored star and a detected star suggests a shift. The
    trial and shift that the most different stars agree on win; ties go to the
    smaller rotation and then the smaller shift, because a good solution needs
    little moving. A rotation and a shift are then fitted to the stars that
    agree (least squares), and the solution is turned and shifted by them.
    The fitted rotation is dropped when it is not significant, so a pure
    shift stays a pure shift.

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
        The solution moved so the reference stars sit on stars of the
        stack, or `None` when too few stars agree or the rotation is too large.
    reason : `str`
        Why there is none. Empty when there is one.
    alignment : `_Alignment` or `None`
        What was found (also given when the rotation was refused).
    """
    predicted = sky_to_pixels(wcs, reference_sky)[:, ::-1]
    detected = detect_star_centroids(plane, count=DETECTED_STAR_COUNT, skip_saturated=False)
    if len(predicted) == 0 or len(detected) == 0:
        return None, "the stored reference stars could not be found: no stars were detected", None
    center = np.array([(plane.shape[0] - 1) / 2.0, (plane.shape[1] - 1) / 2.0])
    farthest_corner = float(np.hypot(*center))
    maximum_rad = math.radians(MAXIMUM_ROTATION_DEG)
    # The step times the farthest distance is ROTATION_STEP_PX, so the nearest
    # trial leaves at most half of that misplaced anywhere in the image.
    step_rad = ROTATION_STEP_PX / max(farthest_corner, 1.0)
    half_steps = max(math.ceil(maximum_rad / step_rad), 1)
    trial_angles = np.linspace(-maximum_rad, maximum_rad, 2 * half_steps + 1)

    best = None
    best_key = None
    for angle_rad in trial_angles:
        vote = _vote_for_shift(_rotate_about(predicted, center, float(angle_rad)), detected)
        if vote is None:
            continue
        shifts, _owner, _pair_detected, members, count = vote
        size = float(np.hypot(*np.median(shifts[members], axis=0)))
        key = (count, -abs(float(angle_rad)), -size)
        if best_key is None or key > best_key:
            best_key, best = key, (float(angle_rad), vote)
    if best is None:
        return (
            None,
            "the stored reference stars were not found near where the stack's solution puts them",
            None,
        )
    trial_rad, (shifts, owner, pair_detected, members, count) = best
    if count < MINIMUM_USABLE_REFERENCE_STARS:
        return (
            None,
            (
                f"only {count} of {len(predicted)} stored reference stars agree on a shift and a rotation "
                f"(up to {MAXIMUM_ROTATION_DEG} deg) from the stack's solution, so the stack's sky "
                "positions cannot be trusted"
            ),
            None,
        )
    shift_row, shift_column = np.median(shifts[members], axis=0)
    # One detected star for each stored star: the one nearest the median shift.
    chosen: dict[int, tuple[float, int]] = {}
    for member in members:
        star = int(owner[member])
        miss = float(np.hypot(*(shifts[member] - (shift_row, shift_column))))
        if star not in chosen or miss < chosen[star][0]:
            chosen[star] = (miss, int(pair_detected[member]))
    stars = np.array(sorted(chosen))
    # The fit: turn about the center by the angle that best carries the
    # predicted stars onto the detected ones, then shift. The angle comes from
    # the stars as the trial rotation left them; the trial adds to it.
    trial_points = _rotate_about(predicted[stars], center, trial_rad)
    to_points = detected[[chosen[int(star)][1] for star in stars]]
    trial_shifted = trial_points + np.array([shift_row, shift_column])
    trial_rms = float(np.sqrt(np.mean(np.sum((to_points - trial_shifted) ** 2, axis=1))))
    extra_deg, rotation_error_deg = _rotation_between(trial_shifted, to_points, trial_rms)
    if abs(extra_deg) <= ROTATION_SIGNIFICANCE * rotation_error_deg:
        extra_deg = 0.0
    rotation_rad = trial_rad + math.radians(extra_deg)
    rotation_deg = math.degrees(rotation_rad)
    turned = _rotate_about(predicted[stars], center, rotation_rad)
    # The median, not the mean, so a few mismatched stars do not move it.
    median_shift = np.median(to_points - turned, axis=0)
    leftover = to_points - (turned + median_shift)
    rms = float(np.sqrt(np.mean(np.sum(leftover**2, axis=1))))
    alignment = _Alignment(
        shift_row=float(median_shift[0]),
        shift_column=float(median_shift[1]),
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
                f"stars, more than the {MAXIMUM_ROTATION_DEG} deg the apertures can follow"
            ),
            alignment,
        )
    if float(np.hypot(*median_shift)) < NEGLIGIBLE_SHIFT_PX and abs(rotation_deg) < NEGLIGIBLE_ROTATION_DEG:
        return wcs.deepcopy(), "", replace(alignment, left_unchanged=True)
    moved = _turn_and_shift_wcs(
        wcs,
        np.array([center[1], center[0]]),
        rotation_rad,
        np.array([median_shift[1], median_shift[0]]),
    )
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
    covered_mask: np.ndarray | None = None,
    member_frame_paths: Sequence[str] = (),
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
    covered_mask : `numpy.ndarray`, optional
        For a stack combined from exposure groups, `True` where every group
        has data, on the grid the stack had before its edges were trimmed.
        Apertures this stack chooses keep out of the pixels it marks `False`
        (see `empty_apertures.UNCOVERED_CLEARANCE_PX`). Left out for a stack
        of one group.
    member_frame_paths : `Sequence` of `str`, optional
        Frames of the group the stack was lined up with, best first. Used only
        when the stack has no solution and no reference frame is named: the
        first one that lines up with the stack gives the solution (see
        `_member_frame_wcs`), and the result's ``wcs_source`` says so.

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
    outcome = measure_noise_in_plane(
        plane,
        stack_path,
        reference_frame_path,
        apertures_path,
        crop_offset,
        may_choose_apertures,
        depth,
        covered_mask=covered_mask,
        member_frame_paths=member_frame_paths,
    )
    return replace(outcome, stack_sky_level=sky_level_of_plane(plane))


def _covered_window(
    covered_mask: np.ndarray, plane_shape: tuple[int, int], crop_offset: tuple[int, int]
) -> np.ndarray | None:
    """Cut the part of a covered mask that lies under a trimmed stack.

    Parameters
    ----------
    covered_mask : `numpy.ndarray`
        `True` where every exposure group has data, on the untrimmed grid.
    plane_shape : `tuple` [`int`, `int`]
        The (rows, columns) of the trimmed stack.
    crop_offset : `tuple` [`int`, `int`]
        The (columns, rows) trimmed from the stack's left and top edges.

    Returns
    -------
    window : `numpy.ndarray` or `None`
        The mask over the stack's own pixels, or `None` when the mask is too
        small to lie under the stack.
    """
    column_offset, row_offset = crop_offset
    rows, columns = plane_shape
    if row_offset + rows > covered_mask.shape[0] or column_offset + columns > covered_mask.shape[1]:
        return None
    return covered_mask[row_offset : row_offset + rows, column_offset : column_offset + columns]


def _reference_exposure_seconds(
    reference_header: fits.Header, member_frame_paths: Sequence[str]
) -> float | None:
    """Find the exposure length of the frame the stack was lined up on.

    Parameters
    ----------
    reference_header : `astropy.io.fits.Header`
        The registration reference frame's header, empty when there is none.
    member_frame_paths : `Sequence` of `str`
        Frames of the group the stack was lined up with, best first. For a
        stack of exposure groups that is the heaviest group, so its frames
        have the reference exposure length. Used only when the reference
        header gives no exposure length.

    Returns
    -------
    exposure_seconds : `float` or `None`
        The ``EXPTIME`` (or ``EXPOSURE``) of the reference frame, or of the
        first readable member frame, or `None` when none is known.
    """
    headers = [reference_header]
    for frame_path in list(member_frame_paths)[:MEMBER_FRAME_ATTEMPTS]:
        try:
            headers.append(read_header(frame_path))
        except OSError, ValueError:
            continue
    for header in headers:
        for keyword in ("EXPTIME", "EXPOSURE"):
            value = header.get(keyword)
            if isinstance(value, int | float) and math.isfinite(value) and value > 0:
                return float(value)
    return None


def measure_noise_in_plane(
    plane: np.ndarray,
    stack_path: str,
    reference_frame_path: str | None,
    apertures_path: str,
    crop_offset: tuple[int, int],
    may_choose_apertures: bool,
    depth: StackDepth | None,
    subtracted_plane: np.ndarray | None = None,
    covered_mask: np.ndarray | None = None,
    member_frame_paths: Sequence[str] = (),
    target_sky_deg: tuple[float, float] | None = None,
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
    covered_mask : `numpy.ndarray`, optional
        See `measure_stack_noise`. Used only when apertures are chosen.
    member_frame_paths : `Sequence` of `str`, optional
        See `measure_stack_noise`.
    target_sky_deg : `tuple` [`float`, `float`], optional
        The target's (RA, Dec) in degrees. Used only for a difference image,
        to give the target's pixel position on the stack.

    Returns
    -------
    outcome : `StackNoiseOutcome`
        The measurement, or the reason there is none.
    """
    window = None
    if covered_mask is not None:
        window = _covered_window(covered_mask, plane.shape, crop_offset)
    stack_header = read_header(stack_path)
    wcs = celestial_wcs(stack_header)
    wcs_source = "stack header"
    # The frame that stands in for the reference frame in the header and
    # saturation checks, and where it sits relative to the stack.
    check_frame_path = reference_frame_path
    check_offset = crop_offset
    if wcs is None:
        if reference_frame_path:
            wcs, reason = _reference_frame_wcs(reference_frame_path, plane, crop_offset)
            wcs_source = "registration reference frame"
        elif member_frame_paths:
            wcs, reason, wcs_source, check_frame_path, check_offset = _member_frame_wcs(
                member_frame_paths, plane, crop_offset
            )
        else:
            reason = NO_WCS_REASON
        if wcs is None:
            # Last source: solve the stack itself. Its solution is on the
            # stack's own grid, so the trim offset is not applied to it.
            wcs, solve_reason = _plate_solve_stack_wcs(stack_path)
            if wcs is None:
                return StackNoiseOutcome(None, f"{reason}; {solve_reason}")
            wcs_source = PLATE_SOLVE_WCS_SOURCE
            check_offset = crop_offset
            # The reference frame still gives the saturation limit, but only
            # when it shares the stack's pixel grid. Its solution was not
            # used, so nothing has checked that yet.
            if not reference_frame_path or not _frame_lines_up(reference_frame_path, plane, crop_offset):
                check_frame_path = None
            else:
                check_frame_path = reference_frame_path
    reference_header = read_header(check_frame_path) if check_frame_path else fits.Header()
    stack_setup_key = setup_key(
        stack_header,
        reference_header,
        reference_exposure_seconds=_reference_exposure_seconds(reference_header, member_frame_paths),
    )
    saturation = _saturation_check(check_frame_path, check_offset)

    if may_choose_apertures:
        aperture_set = load_apertures(apertures_path, stack_setup_key)
    else:
        # A stack that may not choose is read in the library stack's set,
        # whatever the exposure length of its own reference frame.
        library_key = find_library_set_key(apertures_path, stack_setup_key)
        stack_setup_key = library_key or stack_setup_key
        aperture_set = load_apertures(apertures_path, stack_setup_key)
    created = False
    keep_note = ""
    mask_does_not_fit = covered_mask is not None and window is None
    # The stack is plate-solved at most once, and only when a set is chosen or
    # replaced: the stored sky positions must be true positions, which only a
    # solve of this stack gives (the header and frame solutions can be tens of
    # arcseconds off its pixel grid). A solve takes seconds to minutes.
    solve_attempts: list[tuple[WCS | None, str]] = []

    def plate_solution() -> tuple[WCS | None, str]:
        """Plate-solve the stack, or give the earlier answer.

        Returns
        -------
        wcs : `astropy.wcs.WCS` or `None`
            The solution on the stack's pixel grid, or `None`.
        reason : `str`
            Why there is none. Empty when there is one.
        """
        if not solve_attempts:
            solve_attempts.append(
                (wcs, "") if wcs_source == PLATE_SOLVE_WCS_SOURCE else _plate_solve_stack_wcs(stack_path)
            )
        return solve_attempts[0]

    if aperture_set is None:
        if not may_choose_apertures:
            return StackNoiseOutcome(None, load_apertures_with_reason(apertures_path, stack_setup_key)[1])
        if mask_does_not_fit:
            return StackNoiseOutcome(None, UNCOVERED_AREA_MISFIT_REASON)
        # A set is always created, so the noise measurement is not lost; when
        # the solve fails its positions are marked as not trusted.
        solved_wcs, _ = plate_solution()
        trusted = solved_wcs is not None
        chosen, choice_reason = choose_apertures_with_reason(
            plane,
            solved_wcs if solved_wcs is not None else wcs,
            PLATE_SOLVE_WCS_SOURCE if trusted else wcs_source,
            os.path.basename(stack_path),
            stack_setup_key,
            saturation,
            depth,
            window,
            trusted,
        )
        if chosen is None:
            return StackNoiseOutcome(None, choice_reason)
        aperture_set = store_apertures_once(apertures_path, chosen)
        created = aperture_set is chosen
        if created and solved_wcs is not None:
            wcs, wcs_source = solved_wcs, PLATE_SOLVE_WCS_SOURCE
    elif may_choose_apertures and (
        not aperture_set.sky_positions_from_plate_solve or is_much_deeper(depth, aperture_set)
    ):
        # Replace a set whose positions are not trusted (the next library stack
        # makes a plate-solved one) or a much deeper stack's. A replacement is
        # always plate-solved: if the solve or the choice fails, the stored
        # set is kept.
        replaced_for_trust = not aperture_set.sky_positions_from_plate_solve
        solved_wcs, solve_reason = plate_solution()
        if mask_does_not_fit:
            chosen, choice_reason = None, UNCOVERED_AREA_MISFIT_REASON
        elif solved_wcs is None:
            chosen, choice_reason = None, solve_reason
        else:
            chosen, choice_reason = choose_apertures_with_reason(
                plane,
                solved_wcs,
                PLATE_SOLVE_WCS_SOURCE,
                os.path.basename(stack_path),
                stack_setup_key,
                saturation,
                depth,
                window,
                True,
            )
        if chosen is None:
            keep_note = (
                f"A plate-solved set could not be chosen ({choice_reason}); the older set, whose sky "
                "positions are not from a plate solve, was kept."
                if replaced_for_trust
                else f"A set from this deeper stack could not be chosen ({choice_reason}); "
                "the older set was kept."
            )
        else:
            replaced = aperture_set
            aperture_set = store_apertures_once(apertures_path, chosen, replace_identity=replaced.identity)
            created = aperture_set is chosen
            if created:
                wcs, wcs_source = solved_wcs, PLATE_SOLVE_WCS_SOURCE
                logger.info(
                    "Replaced the noise apertures %s of %s by %s: %s.",
                    replaced.identity,
                    apertures_path,
                    chosen.identity,
                    "the old sky positions were not from a plate solve"
                    if replaced_for_trust
                    else f"this stack is at least {REPLACEMENT_DEPTH_FACTOR:.0f} times as deep",
                )
    if may_choose_apertures:
        # Tell the stacks that may not choose which set is the library
        # stack's, so they do not pick a deeper but stale one.
        record_library_set_key(apertures_path, aperture_set.setup_key)
    scale = pixel_scale_arcsec(wcs)
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
        if wcs is None and wcs_source != PLATE_SOLVE_WCS_SOURCE:
            # A frame's own solution can be off (a pointing solve written by
            # the capture program, or a frame that does not share the stack's
            # grid). Before giving up, solve the stack itself and try again.
            solved_wcs, solve_reason = plate_solution()
            if solved_wcs is None:
                return StackNoiseOutcome(None, f"{reason}; {solve_reason}")
            wcs, retry_reason, alignment = _move_guess_onto_reference_stars(
                plane, solved_wcs, aperture_set.reference_sky
            )
            if wcs is None:
                return StackNoiseOutcome(
                    None, f"{reason}; with the plate solve of the stack itself: {retry_reason}"
                )
            wcs_source = PLATE_SOLVE_WCS_SOURCE
        if wcs is None:
            return StackNoiseOutcome(None, reason)
        position_note = alignment.describe() if alignment is not None else ""
    position_note = " ".join(part for part in (position_note, keep_note) if part)
    summed_plane = plane
    if subtracted_plane is not None:
        summed_plane = plane - subtracted_plane
        summed_plane[(plane == 0) | (subtracted_plane == 0)] = 0.0
    all_sums = _sums_at(summed_plane, wcs, aperture_set.aperture_sky)
    pixel_positions = sky_to_pixels(wcs, aperture_set.aperture_sky)
    added_sums: tuple[float, ...] = ()
    target_pixel: tuple[float, float] | None = None
    size_noise: ApertureSizeNoiseOutcome | None = None
    if subtracted_plane is not None:
        added_plane = plane + subtracted_plane
        added_plane[(plane == 0) | (subtracted_plane == 0)] = 0.0
        added_sums = tuple(float(value) for value in _sums_at(added_plane, wcs, aperture_set.aperture_sky))
        del added_plane
        if target_sky_deg is not None:
            column, row = sky_to_pixels(wcs, np.array([target_sky_deg], dtype=float))[0]
            if math.isfinite(column) and math.isfinite(row):
                target_pixel = (float(column), float(row))
    elif may_choose_apertures:
        # One extra pass over the same apertures at other sizes. It is a
        # by-product: a failure is a reason on the size rows, not a lost noise.
        try:
            size_noise = measure_aperture_size_noise(plane, pixel_positions)
        except Exception as error:
            logger.exception("The noise against aperture size of %s could not be measured", stack_path)
            size_noise = ApertureSizeNoiseOutcome(None, f"the measurement failed: {type(error).__name__}")
    sums = all_sums[np.isfinite(all_sums)]
    if sums.size < MINIMUM_USABLE_APERTURES:
        return StackNoiseOutcome(None, f"only {sums.size} of the stored apertures fall on this stack")
    ratios, saturated_count, star_fluxes = _reference_ratios(plane, wcs, aperture_set, saturation)
    flux_ratio = (
        None
        if subtracted_plane is not None
        else _flux_ratio_to_reference_frame(
            plane, wcs, aperture_set, saturation, reference_frame_path, crop_offset, reference_header
        )
    )
    if ratios.size < MINIMUM_USABLE_REFERENCE_STARS:
        return StackNoiseOutcome(
            None,
            f"only {ratios.size} of the reference stars can be used on this stack "
            f"({saturated_count} are saturated in its reference frame)",
        )

    spread = float(robust_sigma(sums))
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
            reference_frame_flux_ratio=flux_ratio,
            aperture_pixel_positions=tuple((float(c), float(r)) for c, r in pixel_positions),
            added_aperture_sums=added_sums,
            target_pixel_position=target_pixel,
            size_noise=size_noise,
            pixel_scale_arcsec=scale,
            reference_star_fluxes=tuple(star_fluxes),
            sky_positions_from_plate_solve=aperture_set.sky_positions_from_plate_solve,
            saturation_source=aperture_set.saturation_source
            if created
            else (saturation.source if saturation is not None else aperture_set.saturation_source),
        )
    )


def _flux_ratio_to_reference_frame(
    plane: np.ndarray,
    wcs: WCS,
    aperture_set: ApertureSet,
    saturation: SaturationCheck | None,
    reference_frame_path: str | None,
    crop_offset: tuple[int, int],
    reference_header: fits.Header,
) -> ReferenceFrameFluxRatio:
    """Compare each reference star's flux in the stack and the reference frame.

    Siril's stack scale is defined against the registration reference frame
    (its scale is 1), so the ratio of a star's flux in the stack to its flux
    in that frame, with both in the same aperture and sky ring, carries the
    stack's overall scale. The stars, the large aperture, the saturation
    check and the sky positions are those of `_reference_ratios`. The
    reference frame's pixel for a stack pixel is found with the trim offset,
    and the claim that the two share a pixel grid is checked with the same
    bright-star match as `_reference_frame_wcs`.

    Only the registration reference frame is used. A member frame that stands
    in for it is a registered (resampled) frame with a scale of its own, so no
    ratio is measured against it.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack as a 2-D image.
    wcs : `astropy.wcs.WCS`
        The stack's sky solution, already moved onto the stored stars.
    aperture_set : `ApertureSet`
        The stored set, with the reference stars and the large aperture.
    saturation : `SaturationCheck` or `None`
        Holds the reference frame's pixels and the saturation limit. With
        `None` there is no readable reference frame.
    reference_frame_path : `str` or `None`
        The registration reference frame named by the stack's record. `None`
        when none is named.
    crop_offset : `tuple` [`int`, `int`]
        The (columns, rows) cut from the stack's left and top edges.
    reference_header : `astropy.io.fits.Header`
        The reference frame's header, for its exposure length.

    Returns
    -------
    ratio : `ReferenceFrameFluxRatio`
        The median ratio in stack units per ADU with its uncertainty, or the
        reason there is none (no named reference frame, a frame that does not
        line up with the stack, or fewer than `MINIMUM_USABLE_REFERENCE_STARS`
        stars with a positive flux in both).
    """
    if not reference_frame_path:
        return ReferenceFrameFluxRatio(
            None,
            reason="no registration reference frame is named, and a member frame is not the "
            "reference (it has a scale of its own)",
        )
    if saturation is None:
        return ReferenceFrameFluxRatio(None, reason="the registration reference frame could not be read")
    exposure = _reference_exposure_seconds(reference_header, ())
    frame_plane = saturation.frame_plane
    matches, stack_star_count, _ = _bright_star_matches(
        plane, np.asarray(frame_plane, dtype=np.float64), crop_offset
    )
    if matches < WCS_CHECK_MINIMUM_MATCHES:
        return ReferenceFrameFluxRatio(
            None,
            reference_exposure_seconds=exposure,
            reference_frame_path=reference_frame_path,
            reason=f"the registration reference frame does not line up with the stack ({matches} of "
            f"{stack_star_count} bright stars match)",
        )
    ratios = []
    saturated_count = 0
    for (column, row), baseline in zip(
        sky_to_pixels(wcs, aperture_set.reference_sky), aperture_set.reference_baseline_flux, strict=True
    ):
        if not (math.isfinite(column) and math.isfinite(row)) or not baseline > 0:
            continue
        if saturation.is_saturated(float(row), float(column)):
            saturated_count += 1
            continue
        flux_arguments = (
            aperture_set.reference_radius_px,
            aperture_set.reference_ring_inner_px,
            aperture_set.reference_ring_outer_px,
        )
        stack_flux = signed_aperture_sum(plane, float(column), float(row), *flux_arguments)
        # The reference frame's pixel is the stack pixel plus the trimmed edge.
        frame_flux = signed_aperture_sum(
            frame_plane, float(column) + crop_offset[0], float(row) + crop_offset[1], *flux_arguments
        )
        if stack_flux is not None and frame_flux is not None and stack_flux > 0 and frame_flux > 0:
            ratios.append(stack_flux / frame_flux)
    if len(ratios) < MINIMUM_USABLE_REFERENCE_STARS:
        return ReferenceFrameFluxRatio(
            None,
            star_count=len(ratios),
            saturated_count=saturated_count,
            reference_exposure_seconds=exposure,
            reference_frame_path=reference_frame_path,
            reason=f"only {len(ratios)} reference stars have a flux in both the stack and the reference "
            f"frame ({saturated_count} are saturated in the reference frame)",
        )
    values = np.asarray(ratios, dtype=float)
    median = float(np.median(values))
    error = (
        MEDIAN_ERROR_FACTOR
        * MAD_TO_SIGMA
        * float(np.median(np.abs(values - median)))
        / math.sqrt(values.size)
    )
    return ReferenceFrameFluxRatio(
        median,
        uncertainty=error,
        star_count=int(values.size),
        saturated_count=saturated_count,
        reference_exposure_seconds=exposure,
        reference_frame_path=reference_frame_path,
    )


def _reference_ratios(
    plane: np.ndarray, wcs: WCS, aperture_set: ApertureSet, saturation: SaturationCheck | None
) -> tuple[np.ndarray, int, list[tuple[float, float, float]]]:
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
    star_fluxes : `list` [`tuple` [`float`, `float`, `float`]]
        For the same stars as `ratios`, in the same order: right ascension
        and declination in degrees, and the brightness in this stack's units.
    """
    ratios = []
    star_fluxes = []
    saturated_count = 0
    for (column, row), baseline, (right_ascension, declination) in zip(
        sky_to_pixels(wcs, aperture_set.reference_sky),
        aperture_set.reference_baseline_flux,
        aperture_set.reference_sky,
        strict=True,
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
            star_fluxes.append((float(right_ascension), float(declination), float(flux)))
    return np.asarray(ratios, dtype=float), saturated_count, star_fluxes


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
    for index, (column, row) in enumerate(sky_to_pixels(wcs, sky)):
        if not (math.isfinite(column) and math.isfinite(row)):
            continue
        value = signed_aperture_sum(plane, float(column), float(row))
        if value is not None:
            values[index] = value
    return values
