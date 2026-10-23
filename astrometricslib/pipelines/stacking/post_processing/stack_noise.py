"""Measures a stack's noise in empty patches of sky, on a fixed flux scale.

A stack's noise tells how well the frames added up. To compare the noise of
two stacks of the same target, or of the same target before and after more
frames, three things must be the same:

1. The patches of sky. Each patch is a small circle (an "aperture") with no
   star in it. Different patches can have different noise (vignetting, a faint
   nebula), so every stack of a target uses the same patches. The patches are
   chosen once, stored as sky positions (right ascension and declination, not
   pixels) in a small file beside the target's stacks, and found again in each
   later stack through that stack's coordinate solution (WCS). No two patches
   overlap, so their sky rings share no pixels and the sums are independent.
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
"""

import hashlib
import json
import logging
import math
import os
import tempfile
from dataclasses import dataclass, replace

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from photutils.aperture import CircularAnnulus, CircularAperture
from scipy.spatial import cKDTree

from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.drivers.fits_access import collapse_to_2d, read_data, read_header
from astrometricslib.foundation.paths import locks_directory
from astrometricslib.foundation.storage.process_locks import file_lock
from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_fwhm_from_data
from astrometricslib.pipelines.shared.applied_camera_profile import camera_name_from_header
from astrometricslib.pipelines.stacking.processing.group_alignment import detect_star_centroids

logger = logging.getLogger(__name__)

__all__ = [
    "APERTURE_FILE_NAME",
    "ApertureSet",
    "StackNoiseOutcome",
    "StackNoiseResult",
    "choose_apertures",
    "choose_apertures_with_reason",
    "load_apertures",
    "load_noise_sums",
    "measure_stack_noise",
    "noise_sums_path",
    "paired_noise_difference",
    "reset_apertures",
    "save_noise_sums",
    "signed_aperture_sum",
    "sky_level_of_plane",
]

# The file beside a target's stacks that keeps its sky apertures.
APERTURE_FILE_NAME = "quality_apertures.json"

# The version of the file's layout. Version 2 holds one set of apertures for
# each camera and optic. The first development version (1) held one set, with
# no per-star reference brightness, and is replaced when a stack is measured.
APERTURE_FILE_VERSION = 2

# The setup key of a stack whose header names neither camera, telescope nor
# focal length.
UNSPECIFIED_SETUP = "unspecified"

# The number of empty apertures. The spread of n samples has a relative error
# of about 1.17 / sqrt(n) (see `MAD_SIGMA_ERROR_FACTOR`): about 12% for 100.
# More would cost little but the stack must have the room; a typical stack
# holds tens of thousands of apertures of this size, so 100 do not crowd it.
# A design choice.
NOISE_APERTURE_COUNT = 100

# The aperture and sky ring are the defaults of `measure_aperture_photometry`
# (the photometry pipeline's star aperture), so the noise is the noise of a
# faint star's brightness measured the way the pipeline measures it.
APERTURE_RADIUS_PX = 4.0
RING_INNER_PX = 7.0
RING_OUTER_PX = 12.0

# The reference stars are measured in a larger aperture than the noise, so
# that their brightness does not change when the seeing changes. The radius is
# 3 times the star width (FWHM) of the stack that chose the stars: a round
# Gaussian star of that width has all but about 1e-11 of its light inside it,
# and a star with wings (a Moffat profile) keeps about 95% inside. The sky ring
# starts 3 px beyond the aperture and is 5 px wide, the same gap and width as
# the noise ring has around the noise aperture. The radius is never below
# 8 px (twice the noise aperture's), which a star width under 2.7 px would
# otherwise give.
REFERENCE_APERTURE_FWHM_MULTIPLE = 3.0
REFERENCE_APERTURE_MINIMUM_RADIUS_PX = 8.0
REFERENCE_RING_GAP_PX = RING_INNER_PX - APERTURE_RADIUS_PX
REFERENCE_RING_WIDTH_PX = RING_OUTER_PX - RING_INNER_PX

# How many reference stars fix the flux scale, and the fewest that must be
# measurable on a stack. The median of 10 stars is steady against one star that
# varies or has a neighbor; 5 is the smallest set that still has a median.
REFERENCE_STAR_COUNT = 10
MINIMUM_USABLE_REFERENCE_STARS = 5

# A stack needs this many usable apertures (20 gives a 28% error on the
# spread).
MINIMUM_USABLE_APERTURES = 20

# An empty aperture stays this far from every detected star, so a star's wings
# cannot reach the aperture or its ring: twice the ring's outer radius.
STAR_CLEARANCE_PX = 2.0 * RING_OUTER_PX

# Two apertures stay at least this far apart: twice the ring's outer radius.
# Then no two rings share a pixel, so the sky level taken for one aperture
# does not feed another, and the sums are independent of each other.
APERTURE_SEPARATION_PX = 2.0 * RING_OUTER_PX

# Apertures stay this far from the edge so the whole ring is on the image.
EDGE_MARGIN_PX = math.ceil(RING_OUTER_PX) + 1

# A ring must be this full of pixels (not off the stack, not empty edge).
MINIMUM_RING_FILL = 0.9

# How many stars to detect when choosing apertures and the reference stars.
DETECTED_STAR_COUNT = 400

# Random positions are drawn from a generator with this seed, so choosing
# apertures twice on the same stack gives the same apertures.
APERTURE_SEED = 20261023

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

# A star is called saturated in the registration reference frame when its
# peak within this many pixels of its position reaches the camera's saturation
# level.
SATURATION_PEAK_HALF_WINDOW_PX = 3

# A robust standard deviation from the median absolute deviation (MAD) of a
# bell curve: sigma = 1.4826 MAD.
MAD_TO_SIGMA = 1.4826

# The standard error of a median is this times the standard error of a mean,
# for a bell curve.
MEDIAN_ERROR_FACTOR = 1.2533

# The standard error of a spread taken as 1.4826 MAD, in units of sigma /
# sqrt(n), for a bell curve. The variance of the MAD-based spread is that of
# the ordinary standard deviation, sigma^2 / (2 n), divided by the asymptotic
# efficiency of the MAD, 0.3675; so the error is sigma / sqrt(2 x 0.3675 x n)
# = 1.1664 sigma / sqrt(n). A formula was chosen over a bootstrap because it
# is exact in the limit, costs nothing and cannot differ between runs. The
# apertures do not overlap, so their sums are independent.
MAD_SIGMA_ERROR_FACTOR = 1.1664

# Two stacks measured in the same apertures can be compared aperture by
# aperture. The library stack's per-aperture sums are kept in a file beside the
# stack (the stack's name, then this ending), so a later job can pair them with
# the sums of a subset stack without measuring the library stack again.
NOISE_SUMS_SUFFIX = "_noise_sums.json"

# The paired comparison resamples the apertures (with replacement, the same
# apertures for both stacks) this many times. The error of a standard error
# from B resamples is 1 / sqrt(2 (B - 1)) of itself: 2.2% for 1000, small
# against the 12% error of the noise itself. The fixed seed makes the number
# repeat exactly.
PAIRED_BOOTSTRAP_COUNT = 1000
PAIRED_BOOTSTRAP_SEED = 20261024

NO_WCS_REASON = "no WCS for this stack"


@dataclass(frozen=True)
class ApertureSet:
    """The sky positions a target's stack noise is measured at.

    Parameters
    ----------
    aperture_sky : `numpy.ndarray`
        Shape ``(n, 2)``: right ascension and declination of each empty
        aperture, in degrees.
    reference_sky : `numpy.ndarray`
        Shape ``(k, 2)``: right ascension and declination of each reference
        star, in degrees, brightest first.
    wcs_source : `str`
        Where the coordinate solution came from when the set was chosen:
        ``"stack header"`` or ``"registration reference frame"``.
    pixel_scale_arcsec : `float`
        The pixel scale of the stack the set was chosen on.
    created_from : `str`
        The name of that stack's file.
    reference_baseline_flux : `numpy.ndarray`
        Shape ``(k,)``: each reference star's brightness (sky-subtracted sum
        in the large reference aperture) on the stack that chose it. Later
        stacks are compared with these numbers.
    reference_radius_px : `float`
        The radius of the large reference aperture.
    reference_fwhm_px : `float`
        The star width of the stack that chose the set, which sized the
        reference aperture.
    saturation_source : `str`
        How saturated stars were kept out when the set was chosen.
    setup_key : `str`
        The camera, telescope and focal length the set belongs to.
    """

    aperture_sky: np.ndarray
    reference_sky: np.ndarray
    wcs_source: str
    pixel_scale_arcsec: float
    created_from: str
    reference_baseline_flux: np.ndarray
    reference_radius_px: float
    reference_fwhm_px: float
    saturation_source: str
    setup_key: str = UNSPECIFIED_SETUP

    @property
    def reference_ring_inner_px(self) -> float:
        """Give the inner radius of the reference stars' sky ring.

        Returns
        -------
        radius : `float`
            Pixels.
        """
        return self.reference_radius_px + REFERENCE_RING_GAP_PX

    @property
    def reference_ring_outer_px(self) -> float:
        """Give the outer radius of the reference stars' sky ring.

        Returns
        -------
        radius : `float`
            Pixels.
        """
        return self.reference_ring_inner_px + REFERENCE_RING_WIDTH_PX

    def to_json(self) -> dict:
        """Turn the set into plain JSON data.

        Returns
        -------
        data : `dict`
            The set, with every number a plain `float`.
        """
        return {
            "aperture_radius_px": APERTURE_RADIUS_PX,
            "ring_inner_px": RING_INNER_PX,
            "ring_outer_px": RING_OUTER_PX,
            "wcs_source": self.wcs_source,
            "pixel_scale_arcsec": self.pixel_scale_arcsec,
            "created_from": self.created_from,
            "setup_key": self.setup_key,
            "apertures_ra_dec_deg": self.aperture_sky.tolist(),
            "reference_stars_ra_dec_deg": self.reference_sky.tolist(),
            "reference_baseline_flux": self.reference_baseline_flux.tolist(),
            "reference_radius_px": self.reference_radius_px,
            "reference_fwhm_px": self.reference_fwhm_px,
            "saturation_source": self.saturation_source,
        }

    @classmethod
    def from_json(cls, data: dict) -> ApertureSet:
        """Rebuild a set from `to_json` data.

        Parameters
        ----------
        data : `dict`
            What `to_json` returned.

        Returns
        -------
        aperture_set : `ApertureSet`
            The set.
        """
        return cls(
            aperture_sky=np.asarray(data["apertures_ra_dec_deg"], dtype=float).reshape(-1, 2),
            reference_sky=np.asarray(data["reference_stars_ra_dec_deg"], dtype=float).reshape(-1, 2),
            wcs_source=str(data["wcs_source"]),
            pixel_scale_arcsec=float(data["pixel_scale_arcsec"]),
            created_from=str(data["created_from"]),
            reference_baseline_flux=np.asarray(data["reference_baseline_flux"], dtype=float).ravel(),
            reference_radius_px=float(data["reference_radius_px"]),
            reference_fwhm_px=float(data["reference_fwhm_px"]),
            saturation_source=str(data["saturation_source"]),
            setup_key=str(data.get("setup_key", UNSPECIFIED_SETUP)),
        )


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
        The name of the stack that chose the apertures, which says which set
        `aperture_sums` belongs to.
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
class _SaturationCheck:
    """Tells whether a star is saturated in the registration reference frame.

    A star saturated in the single frames that made the stack has a flux that
    does not grow in step with the others. The stack's own pixels cannot say:
    an average of many saturated frames looks smooth. So the check reads the
    registration reference frame, which has the stack's geometry (shifted by
    the trim offset).

    Parameters
    ----------
    frame_plane : `numpy.ndarray`
        The reference frame as one 2-D image, in ADU.
    crop_offset : `tuple` [`int`, `int`]
        The (columns, rows) cut from the stack's left and top edges.
    limit_adu : `float`
        A pixel at or above this value is saturated: the camera profile's
        saturation threshold, or its linearity limit when that is lower.
    source : `str`
        Words that say where the limit came from.
    """

    frame_plane: np.ndarray
    crop_offset: tuple[int, int]
    limit_adu: float
    source: str

    def peak_at(self, row: float, column: float) -> float | None:
        """Give the highest pixel value near a stack position.

        Parameters
        ----------
        row, column : `float`
            A position in the stack's pixels.

        Returns
        -------
        peak : `float` or `None`
            The highest value within `SATURATION_PEAK_HALF_WINDOW_PX` of the
            position in the reference frame, or `None` when the position is
            outside the frame.
        """
        frame_row = round(row) + self.crop_offset[1]
        frame_column = round(column) + self.crop_offset[0]
        half = SATURATION_PEAK_HALF_WINDOW_PX
        height, width = self.frame_plane.shape
        if not (half <= frame_row < height - half and half <= frame_column < width - half):
            return None
        window = self.frame_plane[
            frame_row - half : frame_row + half + 1, frame_column - half : frame_column + half + 1
        ]
        return float(np.max(window))

    def is_saturated(self, row: float, column: float) -> bool:
        """Say whether a star at a stack position reaches the saturation level.

        Parameters
        ----------
        row, column : `float`
            A position in the stack's pixels.

        Returns
        -------
        saturated : `bool`
            `True` when the peak is at or above the limit. A position outside
            the frame is not called saturated.
        """
        peak = self.peak_at(row, column)
        return peak is not None and peak >= self.limit_adu


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


def signed_aperture_sum(
    plane: np.ndarray,
    x: float,
    y: float,
    radius: float = APERTURE_RADIUS_PX,
    ring_inner: float = RING_INNER_PX,
    ring_outer: float = RING_OUTER_PX,
) -> float | None:
    """Sum the light in one circle after subtracting the sky from its ring.

    The sum is the same as the star brightness in `measure_aperture_photometry`
    (pixels weighted by their exact overlap with the circle, minus the ring's
    median times the circle's area) but it is not cut off at zero, because
    the spread of empty apertures needs the negative sums too.

    Parameters
    ----------
    plane : `numpy.ndarray`
        A 2-D image.
    x, y : `float`
        The center, in pixels (column, row), counted from 0.
    radius : `float`, optional
        The circle's radius in pixels. The noise aperture's by default.
    ring_inner, ring_outer : `float`, optional
        The sky ring's inner and outer radius in pixels.

    Returns
    -------
    value : `float` or `None`
        The sky-subtracted sum, or `None` when the circle or its ring reaches
        outside the image, or the ring holds non-finite pixels or exactly-zero
        pixels (the empty edge of a stack).
    """
    height, width = plane.shape
    reach = ring_outer + 1.0
    if x < reach or y < reach or x > width - 1 - reach or y > height - 1 - reach:
        return None
    aperture = CircularAperture((x, y), r=radius)
    annulus = CircularAnnulus((x, y), r_in=ring_inner, r_out=ring_outer)
    ring_values = annulus.to_mask(method="center").get_values(plane)
    expected_ring_pixels = math.pi * (ring_outer**2 - ring_inner**2)
    if ring_values.size < MINIMUM_RING_FILL * expected_ring_pixels:
        return None
    if not np.all(np.isfinite(ring_values)) or np.any(ring_values == 0):
        return None
    aperture_values = aperture.to_mask(method="exact").get_values(plane)
    if not np.all(np.isfinite(aperture_values)):
        return None
    return float(aperture_values.sum() - aperture.area * np.median(ring_values))


def _stack_plane(stack_path: str) -> np.ndarray | None:
    """Read a stack as one 2-D float image.

    Parameters
    ----------
    stack_path : `str`
        The stack file.

    Returns
    -------
    plane : `numpy.ndarray` or `None`
        The image (color channels averaged), or `None` when the file holds no
        pixels.
    """
    data = read_data(stack_path)
    if data is None:
        return None
    return collapse_to_2d(np.asarray(data, dtype=np.float64))


def sky_level_of_plane(plane: np.ndarray) -> float | None:
    """Give the sky level of a stack image: the median of its usable pixels.

    Pixels that are exactly zero (the empty edge of a registered stack) and
    pixels that are not finite are left out. Stars cover a small share of the
    stack, so the median is the sky.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack as one 2-D image (the noise step reads it once and passes
        it on).

    Returns
    -------
    level : `float` or `None`
        The median in the stack's own units, or `None` when the image holds no
        usable pixel or the median is not above zero.
    """
    usable = plane[np.isfinite(plane) & (plane != 0)]
    if usable.size == 0:
        return None
    level = float(np.median(usable))
    return level if level > 0 else None


def _celestial_wcs(header: fits.Header) -> WCS | None:
    """Read a header's sky coordinate solution, if it has one.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        A FITS header.

    Returns
    -------
    wcs : `astropy.wcs.WCS` or `None`
        The two sky axes, or `None` when the header has no usable solution.
    """
    try:
        wcs = WCS(header)
    except ValueError, KeyError:
        return None
    if not wcs.has_celestial:
        return None
    return wcs.celestial


def _setup_key(*headers: fits.Header) -> str:
    """Name the camera and optic a stack was made with.

    Parameters
    ----------
    *headers : `astropy.io.fits.Header`
        The stack's header, then the registration reference frame's, if any.
        The first one that names a camera, a telescope or a focal length is
        used.

    Returns
    -------
    key : `str`
        ``camera|telescope|focal length`` in lower case, with ``?`` for a
        missing part, or `UNSPECIFIED_SETUP` when no header names any.
    """
    for header in headers:
        camera = str(header.get("INSTRUME", "")).strip().lower()
        telescope = str(header.get("TELESCOP", "")).strip().lower()
        focal_length = header.get("FOCALLEN")
        if camera or telescope or focal_length is not None:
            focal_text = f"{float(focal_length):g}" if isinstance(focal_length, int | float) else "?"
            return f"{camera or '?'}|{telescope or '?'}|{focal_text}"
    return UNSPECIFIED_SETUP


def _saturation_check(
    reference_frame_path: str | None, crop_offset: tuple[int, int]
) -> _SaturationCheck | None:
    """Read the registration reference frame and the camera's saturation level.

    Parameters
    ----------
    reference_frame_path : `str` or `None`
        The frame Siril aligned all the others to.
    crop_offset : `tuple` [`int`, `int`]
        The (columns, rows) cut from the stack's left and top edges.

    Returns
    -------
    check : `_SaturationCheck` or `None`
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
    return _SaturationCheck(
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


def _pixel_scale_arcsec(wcs: WCS) -> float:
    """Give the mean pixel scale of a solution.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        A sky solution.

    Returns
    -------
    scale : `float`
        Arcseconds per pixel.
    """
    return float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)


def choose_apertures(
    plane: np.ndarray,
    wcs: WCS,
    wcs_source: str,
    created_from: str,
    setup_key: str = UNSPECIFIED_SETUP,
    saturation: _SaturationCheck | None = None,
) -> ApertureSet | None:
    """Choose empty-sky apertures and reference stars on one stack.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack as a 2-D image.
    wcs : `astropy.wcs.WCS`
        The stack's sky solution.
    wcs_source : `str`
        Where the solution came from, kept with the set.
    created_from : `str`
        The stack's file name, kept with the set.
    setup_key : `str`, optional
        The camera and optic the set belongs to.
    saturation : `_SaturationCheck`, optional
        Keeps stars that are saturated in the registration reference frame
        out of the reference stars. Left out, the stack's own 99.99th
        percentile screens the brightest stars instead.

    Returns
    -------
    aperture_set : `ApertureSet` or `None`
        See `choose_apertures_with_reason`.
    """
    return choose_apertures_with_reason(plane, wcs, wcs_source, created_from, setup_key, saturation)[0]


def choose_apertures_with_reason(
    plane: np.ndarray,
    wcs: WCS,
    wcs_source: str,
    created_from: str,
    setup_key: str = UNSPECIFIED_SETUP,
    saturation: _SaturationCheck | None = None,
) -> tuple[ApertureSet | None, str]:
    """Choose empty-sky apertures and reference stars, and say why not if none.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack as a 2-D image.
    wcs : `astropy.wcs.WCS`
        The stack's sky solution.
    wcs_source : `str`
        Where the solution came from, kept with the set.
    created_from : `str`
        The stack's file name, kept with the set.
    setup_key : `str`, optional
        The camera and optic the set belongs to.
    saturation : `_SaturationCheck`, optional
        Keeps stars that are saturated in the registration reference frame
        out of the reference stars. Left out, the stack's own 99.99th
        percentile screens the brightest stars instead.

    Returns
    -------
    aperture_set : `ApertureSet` or `None`
        The apertures (positions drawn from a generator with a fixed seed, at
        least `APERTURE_SEPARATION_PX` apart, at least `STAR_CLEARANCE_PX`
        from every detected star, and kept only where `signed_aperture_sum`
        works) and the brightest `REFERENCE_STAR_COUNT` isolated stars that are
        not saturated. `None` when fewer than `MINIMUM_USABLE_APERTURES`
        apertures or `MINIMUM_USABLE_REFERENCE_STARS` stars could be found.
    reason : `str`
        Why there is no set. Empty when there is one.
    """
    fwhm = measure_fwhm_from_data(plane)
    if fwhm is None or not math.isfinite(fwhm) or fwhm <= 0:
        return None, "the stack's star width could not be measured, so the reference aperture has no size"
    radius = max(REFERENCE_APERTURE_FWHM_MULTIPLE * fwhm, REFERENCE_APERTURE_MINIMUM_RADIUS_PX)
    ring_inner = radius + REFERENCE_RING_GAP_PX
    ring_outer = ring_inner + REFERENCE_RING_WIDTH_PX
    # Every bright star counts for the clearance and the isolation, saturated
    # or not: a saturated star has the widest wings.
    stars = detect_star_centroids(plane, count=DETECTED_STAR_COUNT, skip_saturated=False)
    star_tree = cKDTree(stars) if len(stars) else None
    height, width = plane.shape
    generator = np.random.default_rng(APERTURE_SEED)
    positions: list[tuple[float, float]] = []
    for _ in range(40 * NOISE_APERTURE_COUNT):
        row = generator.uniform(EDGE_MARGIN_PX, height - EDGE_MARGIN_PX)
        column = generator.uniform(EDGE_MARGIN_PX, width - EDGE_MARGIN_PX)
        if star_tree is not None and star_tree.query([row, column])[0] <= STAR_CLEARANCE_PX:
            continue
        if positions and np.min(np.hypot(*(np.array(positions) - (row, column)).T)) < APERTURE_SEPARATION_PX:
            continue
        if signed_aperture_sum(plane, column, row) is None:
            continue
        positions.append((row, column))
        if len(positions) >= NOISE_APERTURE_COUNT:
            break
    if len(positions) < MINIMUM_USABLE_APERTURES:
        return None, f"only {len(positions)} empty apertures could be placed on this stack"
    percentile_screen = None
    if saturation is None:
        percentile_screen = detect_star_centroids(plane, count=DETECTED_STAR_COUNT, skip_saturated=True)
    reference: list[tuple[float, float]] = []
    baseline: list[float] = []
    for row, column in _isolated_stars(stars, clearance=max(STAR_CLEARANCE_PX, ring_outer + 1.0)):
        if saturation is not None and saturation.is_saturated(row, column):
            continue
        if percentile_screen is not None and (
            len(percentile_screen) == 0 or np.min(np.hypot(*(percentile_screen - (row, column)).T)) > 1.0
        ):
            continue
        flux = signed_aperture_sum(plane, column, row, radius, ring_inner, ring_outer)
        if flux is None or flux <= 0:
            continue
        reference.append((row, column))
        baseline.append(flux)
        if len(reference) >= REFERENCE_STAR_COUNT:
            break
    if len(reference) < MINIMUM_USABLE_REFERENCE_STARS:
        return (
            None,
            f"only {len(reference)} isolated, unsaturated reference stars could be found on this stack",
        )
    return (
        ApertureSet(
            aperture_sky=_pixels_to_sky(wcs, np.array(positions, dtype=float)),
            reference_sky=_pixels_to_sky(wcs, np.array(reference, dtype=float)),
            wcs_source=wcs_source,
            pixel_scale_arcsec=_pixel_scale_arcsec(wcs),
            created_from=created_from,
            reference_baseline_flux=np.array(baseline, dtype=float),
            reference_radius_px=float(radius),
            reference_fwhm_px=float(fwhm),
            saturation_source=(
                saturation.source
                if saturation is not None
                else "the stack's own 99.99th percentile (no registration reference frame was available)"
            ),
            setup_key=setup_key,
        ),
        "",
    )


def _isolated_stars(stars: np.ndarray, clearance: float = STAR_CLEARANCE_PX) -> np.ndarray:
    """Keep the stars that have no detected neighbor within the clearance.

    Parameters
    ----------
    stars : `numpy.ndarray`
        Star centers, shape ``(n, 2)`` as (row, column), brightest first.
    clearance : `float`, optional
        How far the nearest neighbor must be.

    Returns
    -------
    isolated : `numpy.ndarray`
        The stars whose nearest neighbor is farther than the clearance, in
        the same order.
    """
    if len(stars) < 2:
        return stars
    nearest = cKDTree(stars).query(stars, k=2)[0][:, 1]
    return stars[nearest > clearance]


def _pixels_to_sky(wcs: WCS, pixels: np.ndarray) -> np.ndarray:
    """Convert (row, column) pixel positions to (RA, Dec) in degrees.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The sky solution.
    pixels : `numpy.ndarray`
        Shape ``(n, 2)``, (row, column).

    Returns
    -------
    sky : `numpy.ndarray`
        Shape ``(n, 2)``, (RA, Dec) in degrees.
    """
    if len(pixels) == 0:
        return np.empty((0, 2))
    ra, dec = wcs.pixel_to_world_values(pixels[:, 1], pixels[:, 0])
    return np.column_stack([ra, dec]).astype(float)


def _sky_to_pixels(wcs: WCS, sky: np.ndarray) -> np.ndarray:
    """Convert (RA, Dec) in degrees to (column, row) pixel positions.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The sky solution.
    sky : `numpy.ndarray`
        Shape ``(n, 2)``, (RA, Dec) in degrees.

    Returns
    -------
    pixels : `numpy.ndarray`
        Shape ``(n, 2)``, (column, row).
    """
    if len(sky) == 0:
        return np.empty((0, 2))
    column, row = wcs.world_to_pixel_values(sky[:, 0], sky[:, 1])
    return np.column_stack([column, row]).astype(float)


def _read_aperture_file(path: str) -> dict[str, dict]:
    """Read the sets stored in a target's aperture file.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.

    Returns
    -------
    sets : `dict` [`str`, `dict`]
        The JSON data of each set, by setup key. Empty when the file is
        missing, cannot be read, or has the layout of the first development
        version (those sets lack the reference brightnesses and are not
        used).
    """
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("version") != APERTURE_FILE_VERSION:
            logger.warning("The stored noise apertures at %s have an old layout and will be replaced.", path)
            return {}
        return dict(data["sets"])
    except (OSError, ValueError, KeyError, AttributeError, TypeError) as error:
        logger.warning("The stored noise apertures at %s could not be read: %s", path, error)
        return {}


def load_apertures(path: str, setup_key: str | None = None) -> ApertureSet | None:
    """Read a target's stored apertures.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.
    setup_key : `str`, optional
        The camera and optic wanted. Left out, the first stored set.

    Returns
    -------
    aperture_set : `ApertureSet` or `None`
        The stored set, or `None` when the file is missing or cannot be read
        (a warning says so in the second case), or holds no set for the key.
    """
    sets = _read_aperture_file(path)
    if setup_key is None:
        data = next(iter(sets.values()), None)
    else:
        data = sets.get(setup_key)
    if data is None:
        return None
    try:
        return ApertureSet.from_json(data)
    except (ValueError, KeyError, TypeError) as error:
        logger.warning("The stored noise apertures at %s could not be read: %s", path, error)
        return None


def _aperture_lock_path(path: str) -> str:
    """Name the lock file that guards one aperture file.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.

    Returns
    -------
    lock_path : `str`
        A file in the locks folder, named after the aperture file's path.
    """
    digest = hashlib.sha1(os.path.abspath(path).encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return os.path.join(locks_directory(), f"quality_apertures_{digest}.lock")


def _write_aperture_file(path: str, sets: dict[str, dict]) -> None:
    """Write the aperture file whole, in one step.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.
    sets : `dict` [`str`, `dict`]
        The JSON data of each set, by setup key.
    """
    directory = os.path.dirname(path) or "."
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".quality_apertures_", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"version": APERTURE_FILE_VERSION, "sets": sets}, handle)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _store_apertures_once(path: str, aperture_set: ApertureSet) -> ApertureSet:
    """Save the set unless another run saved one for the same equipment first.

    The file is read, changed and replaced while holding a lock, so two
    stacks of one target that finish together cannot overwrite each other's
    apertures; the loser uses the winner's.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.
    aperture_set : `ApertureSet`
        The set to save.

    Returns
    -------
    stored : `ApertureSet`
        The set now in the file for this equipment: this one, or the other
        run's.
    """
    with file_lock(_aperture_lock_path(path), blocking=True):
        sets = _read_aperture_file(path)
        existing = sets.get(aperture_set.setup_key)
        if existing is not None:
            try:
                return ApertureSet.from_json(existing)
            except ValueError, KeyError, TypeError:
                logger.warning("A stored set of noise apertures at %s was unreadable and is replaced.", path)
        sets[aperture_set.setup_key] = aperture_set.to_json()
        _write_aperture_file(path, sets)
    return aperture_set


def reset_apertures(path: str, setup_key: str | None = None) -> bool:
    """Forget a target's stored apertures, so the next stack chooses new ones.

    Use this when the stored stars or patches are no longer good (a nebula
    grew, a star was misjudged) or the equipment's key changed by accident.
    Noise values measured before the reset and after it are not comparable.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.
    setup_key : `str`, optional
        The one camera and optic to forget. Left out, every set is forgotten
        and the file is deleted.

    Returns
    -------
    removed : `bool`
        `True` when something was removed.
    """
    with file_lock(_aperture_lock_path(path), blocking=True):
        if setup_key is None:
            if not os.path.isfile(path):
                return False
            os.unlink(path)
            return True
        sets = _read_aperture_file(path)
        if setup_key not in sets:
            return False
        del sets[setup_key]
        if sets:
            _write_aperture_file(path, sets)
        else:
            os.unlink(path)
        return True


def measure_stack_noise(
    stack_path: str,
    reference_frame_path: str | None,
    apertures_path: str,
    crop_offset: tuple[int, int] = (0, 0),
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

    Returns
    -------
    outcome : `StackNoiseOutcome`
        The measurement, or the reason there is none (no solution, an
        unreadable file, too few apertures or reference stars, a solution that
        does not fit the stored stars or is rotated too far, or a pixel scale
        unlike the one the apertures were chosen at).
    """
    plane = _stack_plane(stack_path)
    if plane is None:
        return StackNoiseOutcome(None, "the stack file holds no pixels")
    # The plane is read once; the sky level is taken from it here so no later
    # step has to read the stack again.
    outcome = _measure_noise_in_plane(plane, stack_path, reference_frame_path, apertures_path, crop_offset)
    return replace(outcome, stack_sky_level=sky_level_of_plane(plane))


def _measure_noise_in_plane(
    plane: np.ndarray,
    stack_path: str,
    reference_frame_path: str | None,
    apertures_path: str,
    crop_offset: tuple[int, int],
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
    if aperture_set is None:
        chosen, choice_reason = choose_apertures_with_reason(
            plane, wcs, wcs_source, os.path.basename(stack_path), setup_key, saturation
        )
        if chosen is None:
            return StackNoiseOutcome(None, choice_reason)
        aperture_set = _store_apertures_once(apertures_path, chosen)
        created = aperture_set is chosen
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
    all_sums = _sums_at(plane, wcs, aperture_set.aperture_sky)
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
            saturation_source=aperture_set.saturation_source
            if created
            else (saturation.source if saturation is not None else aperture_set.saturation_source),
        )
    )


def noise_sums_path(stack_path: str) -> str:
    """Name the file that keeps a stack's per-aperture sums.

    Parameters
    ----------
    stack_path : `str`
        The stack.

    Returns
    -------
    path : `str`
        The stack's path without its extension, then `NOISE_SUMS_SUFFIX`.
    """
    return os.path.splitext(stack_path)[0] + NOISE_SUMS_SUFFIX


def save_noise_sums(stack_path: str, result: StackNoiseResult) -> None:
    """Keep a stack's per-aperture sums beside it.

    A failure to write is logged and ignored: the file only makes a later
    paired comparison possible.

    Parameters
    ----------
    stack_path : `str`
        The stack the sums were measured on.
    result : `StackNoiseResult`
        Its noise measurement, holding the sums.
    """
    data = {
        "stack": os.path.basename(stack_path),
        "aperture_set_origin": result.aperture_set_origin,
        "reference_flux_adu": result.reference_flux_adu,
        "aperture_sums": [value if math.isfinite(value) else None for value in result.aperture_sums],
    }
    try:
        with open(noise_sums_path(stack_path), "w", encoding="utf-8") as handle:
            json.dump(data, handle)
    except OSError as error:
        logger.warning("The per-aperture noise sums of %s could not be saved: %s", stack_path, error)


def load_noise_sums(stack_path: str) -> tuple[np.ndarray, float, str] | None:
    """Read the per-aperture sums that `save_noise_sums` kept for a stack.

    Parameters
    ----------
    stack_path : `str`
        The stack.

    Returns
    -------
    sums : `tuple` or `None`
        The sums (NaN where an aperture was not usable), the reference flux
        they are read against, and the name of the stack that chose the
        apertures; or `None` when the file is missing or cannot be read.
    """
    try:
        with open(noise_sums_path(stack_path), encoding="utf-8") as handle:
            data = json.load(handle)
        sums = np.array([np.nan if value is None else value for value in data["aperture_sums"]], dtype=float)
        return sums, float(data["reference_flux_adu"]), str(data["aperture_set_origin"])
    except OSError, ValueError, KeyError, TypeError:
        return None


def paired_noise_difference(
    full_sums: np.ndarray, full_flux: float, other_sums: np.ndarray, other_flux: float
) -> tuple[float, float, int] | None:
    """Compare the noise of two stacks measured in the same apertures.

    The two stacks share most of their frames, so the same apertures are high
    or low in both. Comparing their separate errors would overstate how
    uncertain the difference is. The apertures are resampled together
    (the same draw for both stacks), the difference of the two noises is found
    for each draw, and the spread of those differences is the error.

    Parameters
    ----------
    full_sums, other_sums : `numpy.ndarray`
        Shape ``(n,)``: each stack's sum at every stored aperture, NaN where
        it could not be measured.
    full_flux, other_flux : `float`
        The reference fluxes that put each stack on the common scale.

    Returns
    -------
    comparison : `tuple` or `None`
        The noise of the other stack minus the noise of the full stack, both
        as fractions of their reference flux; its error; and the number of
        apertures usable in both. `None` when the arrays differ in length,
        fewer than `MINIMUM_USABLE_APERTURES` apertures are usable in both, or
        a flux is not positive.
    """
    if full_sums.shape != other_sums.shape or full_flux <= 0 or other_flux <= 0:
        return None
    usable = np.isfinite(full_sums) & np.isfinite(other_sums)
    if int(np.count_nonzero(usable)) < MINIMUM_USABLE_APERTURES:
        return None
    first = full_sums[usable] / full_flux
    second = other_sums[usable] / other_flux

    def spread(values: np.ndarray) -> np.ndarray:
        """Give the robust spread of each row of a table of resampled values.

        Parameters
        ----------
        values : `numpy.ndarray`
            Shape ``(rows, n)``.

        Returns
        -------
        spreads : `numpy.ndarray`
            1.4826 times the median absolute deviation of each row.
        """
        centers = np.median(values, axis=-1, keepdims=True)
        return MAD_TO_SIGMA * np.median(np.abs(values - centers), axis=-1)

    difference = float(spread(second) - spread(first))
    draws = np.random.default_rng(PAIRED_BOOTSTRAP_SEED).integers(
        0, first.size, size=(PAIRED_BOOTSTRAP_COUNT, first.size)
    )
    error = float(np.std(spread(second[draws]) - spread(first[draws]), ddof=1))
    return difference, error, int(first.size)


def _reference_ratios(
    plane: np.ndarray, wcs: WCS, aperture_set: ApertureSet, saturation: _SaturationCheck | None
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
    saturation : `_SaturationCheck` or `None`
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
