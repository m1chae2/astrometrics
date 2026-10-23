"""Measures a stack's noise in empty patches of sky, on a fixed flux scale.

A stack's noise tells how well the frames added up. To compare the noise of
two stacks of the same target, or of the same target before and after more
frames, three things must be the same:

1. The patches of sky. Each patch is a small circle (an "aperture") with no
   star in it. Different patches can have different noise (vignetting, a faint
   nebula), so every stack of a target uses the same patches. The patches are
   chosen once, stored as sky positions (right ascension and declination, not
   pixels) in a small file beside the target's stacks, and found again in each
   later stack through that stack's coordinate solution (WCS).
2. The way the noise is measured. Each aperture's value is the sum of its
   pixels minus the sky level taken from a ring around it (the same sum a
   star's brightness uses). The noise is the robust spread of those sums over
   all the apertures.
3. The flux scale. Siril's stacks have their own brightness scale, so the noise
   in "ADU" of one stack cannot be compared with another's. The noise is
   divided by the brightness of a fixed set of reference stars (the brightest
   unsaturated, isolated stars of the first stack, also stored as sky
   positions). The
   noise, the reference brightness and their ratio are all reported.

Where the coordinate solution comes from: the stack's own header when it has
one. A freshly made stack has none, because the astrometry step writes it
later. Then the registration reference frame is used. Siril lines every
frame up with that frame, so the stack has the reference frame's
geometry. The stack may have had its edges trimmed afterwards (the trim moves
the origin of the pixel grid by a known offset), and the frame's solution is
shifted by the same offset. The claim is checked, not assumed: the brightest
stars of the stack must sit where the reference frame shows stars. If they do
not, no noise is measured.

A frame's own "solution" is only a first guess. The camera software (Ekos)
writes the position the mount was told to point at, not a plate solution, so
the sky position of a pixel can be wrong by tens of pixels, and by a different
amount on every night. The first stack of a target fixes its apertures with
that guess, and that is consistent within the stack. A later stack with a
guess like that must first be moved onto the stored apertures: the stored
reference stars are looked for near where the guess predicts them, the shift
that most of them agree on is found, and the solution is moved by that shift.
If fewer than a few stars agree, no noise is measured.

The estimator and the numbers (100 apertures, radius 4 px, 10 reference stars)
are declared in `pipelines/stacking/metrics.py`.
"""

import json
import logging
import math
import os
import tempfile
from dataclasses import dataclass

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from photutils.aperture import CircularAnnulus, CircularAperture
from scipy.spatial import cKDTree

from astrometricslib.drivers.fits_access import collapse_to_2d, read_data, read_header
from astrometricslib.pipelines.stacking.processing.group_alignment import detect_star_centroids

logger = logging.getLogger(__name__)

__all__ = [
    "APERTURE_FILE_NAME",
    "ApertureSet",
    "StackNoiseOutcome",
    "StackNoiseResult",
    "choose_apertures",
    "load_apertures",
    "measure_stack_noise",
    "signed_aperture_sum",
]

# The file beside a target's stacks that keeps its sky apertures.
APERTURE_FILE_NAME = "quality_apertures.json"

# The number of empty apertures. The spread of n samples has a relative error
# of 1 / sqrt(2 (n - 1)): about 7% for 100. More would cost little but the
# stack must have the room; a typical stack holds tens of thousands of
# apertures of this size, so 100 do not crowd it. A design choice.
NOISE_APERTURE_COUNT = 100

# The aperture and sky ring are the defaults of `measure_aperture_photometry`
# (the photometry pipeline's star aperture), so the noise is the noise of a
# faint star's brightness measured the way the pipeline measures it.
APERTURE_RADIUS_PX = 4.0
RING_INNER_PX = 7.0
RING_OUTER_PX = 12.0

# How many reference stars fix the flux scale, and the fewest that must be
# measurable on a stack. The median of 10 stars is steady against one star that
# varies or has a neighbor; 5 is the smallest set that still has a median.
REFERENCE_STAR_COUNT = 10
MINIMUM_USABLE_REFERENCE_STARS = 5

# A stack needs this many usable apertures (20 gives a 16% error on the
# spread).
MINIMUM_USABLE_APERTURES = 20

# An empty aperture stays this far from every detected star, so a star's wings
# cannot reach the aperture or its ring: twice the ring's outer radius.
STAR_CLEARANCE_PX = 2.0 * RING_OUTER_PX

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

# Moving a first-guess solution onto the stored apertures. The stored
# reference stars are searched for within this many pixels of where the guess
# predicts them. On the M 57 frames of 2026-10-03 the guess was off by up to
# 82 px between frames of one night; 500 px (about 16 arcmin at 1.9 arcsec per
# pixel) leaves room for a night-to-night pointing error, and is still small
# enough that the stars of a field do not line up by chance (4000 star pairs in
# a window of 1,000,000 square pixels, against a 3 px agreement).
SEED_SEARCH_RADIUS_PX = 500.0

# Reference stars agree on a shift when their shifts differ by less than this.
# It is the tolerance of the other star checks in this module.
SEED_AGREEMENT_PX = WCS_CHECK_TOLERANCE_PX

# A robust standard deviation from the median absolute deviation (MAD) of a
# bell curve: sigma = 1.4826 MAD.
MAD_TO_SIGMA = 1.4826

# The standard error of a median is this times the standard error of a mean,
# for a bell curve.
MEDIAN_ERROR_FACTOR = 1.2533

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
    """

    aperture_sky: np.ndarray
    reference_sky: np.ndarray
    wcs_source: str
    pixel_scale_arcsec: float
    created_from: str

    def to_json(self) -> dict:
        """Turn the set into plain JSON data.

        Returns
        -------
        data : `dict`
            The set, with every number a plain `float`.
        """
        return {
            "version": 1,
            "aperture_radius_px": APERTURE_RADIUS_PX,
            "ring_inner_px": RING_INNER_PX,
            "ring_outer_px": RING_OUTER_PX,
            "wcs_source": self.wcs_source,
            "pixel_scale_arcsec": self.pixel_scale_arcsec,
            "created_from": self.created_from,
            "apertures_ra_dec_deg": self.aperture_sky.tolist(),
            "reference_stars_ra_dec_deg": self.reference_sky.tolist(),
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
        )


@dataclass(frozen=True)
class StackNoiseResult:
    """The noise of a stack in empty sky, and the scale it is read against.

    Parameters
    ----------
    noise_adu : `float`
        The robust spread of the empty-aperture sums.
    noise_uncertainty_adu : `float`
        Its 1-sigma uncertainty, `noise_adu` / sqrt(2 (n - 1)).
    aperture_count : `int`
        The number of usable apertures behind it.
    reference_flux_adu : `float`
        The median brightness of the reference stars.
    reference_uncertainty_adu : `float`
        The standard error of that median.
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


@dataclass(frozen=True)
class StackNoiseOutcome:
    """The result of a noise measurement, or the reason there is none.

    Parameters
    ----------
    result : `StackNoiseResult` or `None`
        The measurement, or `None` when it could not be made.
    reason : `str`
        Why there is no result. Empty when there is one.
    """

    result: StackNoiseResult | None
    reason: str = ""


def signed_aperture_sum(plane: np.ndarray, x: float, y: float) -> float | None:
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

    Returns
    -------
    value : `float` or `None`
        The sky-subtracted sum, or `None` when the circle or its ring reaches
        outside the image, or the ring holds non-finite pixels or exactly-zero
        pixels (the empty edge of a stack).
    """
    height, width = plane.shape
    reach = RING_OUTER_PX + 1.0
    if x < reach or y < reach or x > width - 1 - reach or y > height - 1 - reach:
        return None
    aperture = CircularAperture((x, y), r=APERTURE_RADIUS_PX)
    annulus = CircularAnnulus((x, y), r_in=RING_INNER_PX, r_out=RING_OUTER_PX)
    ring_values = annulus.to_mask(method="center").get_values(plane)
    expected_ring_pixels = math.pi * (RING_OUTER_PX**2 - RING_INNER_PX**2)
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
) -> tuple[WCS | None, str]:
    """Shift a first-guess solution so it lands on the stored reference stars.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack's image.
    wcs : `astropy.wcs.WCS`
        The first-guess solution (it points where the mount was told to).
    reference_sky : `numpy.ndarray`
        Shape ``(k, 2)``: right ascension and declination of the stored
        reference stars, in degrees.

    Returns
    -------
    moved : `astropy.wcs.WCS` or `None`
        The solution shifted so the reference stars sit on stars of the
        stack, or `None` when too few stars agree.
    reason : `str`
        Why there is none. Empty when there is one.
    """
    predicted = _sky_to_pixels(wcs, reference_sky)[:, ::-1]
    detected = detect_star_centroids(plane, count=DETECTED_STAR_COUNT, skip_saturated=False)
    if len(predicted) == 0 or len(detected) == 0:
        return None, "the stored reference stars could not be found: no stars were detected"
    # Every (reference star, detected star) pair suggests a shift. The true
    # shift is the one many different reference stars suggest together.
    star_index, detected_index = np.meshgrid(
        np.arange(len(predicted)), np.arange(len(detected)), indexing="ij"
    )
    shifts = detected[detected_index.ravel()] - predicted[star_index.ravel()]
    owner = star_index.ravel()
    near = np.hypot(shifts[:, 0], shifts[:, 1]) <= SEED_SEARCH_RADIUS_PX
    shifts, owner = shifts[near], owner[near]
    if len(shifts) == 0:
        return None, "the stored reference stars were not found near where the stack's solution puts them"
    tree = cKDTree(shifts)
    neighbors = tree.query_ball_point(shifts, r=SEED_AGREEMENT_PX)
    distinct_counts = [len(set(owner[members])) for members in neighbors]
    best = int(np.argmax(distinct_counts))
    if distinct_counts[best] < MINIMUM_USABLE_REFERENCE_STARS:
        return None, (
            f"only {distinct_counts[best]} of {len(predicted)} stored reference stars agree on a shift "
            "from the stack's first-guess solution, so the stack's sky positions cannot be trusted"
        )
    shift_row, shift_column = np.median(shifts[neighbors[best]], axis=0)
    moved = wcs.deepcopy()
    # A star predicted at pixel p that is really at p + shift has the sky
    # position the guess gives for p, so the reference pixel moves by the
    # shift.
    moved.wcs.crpix = [wcs.wcs.crpix[0] + shift_column, wcs.wcs.crpix[1] + shift_row]
    return moved, ""


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


def choose_apertures(plane: np.ndarray, wcs: WCS, wcs_source: str, created_from: str) -> ApertureSet | None:
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

    Returns
    -------
    aperture_set : `ApertureSet` or `None`
        The apertures (positions drawn from a generator with a fixed seed,
        kept only where `signed_aperture_sum` works and at least
        `STAR_CLEARANCE_PX` from every detected star) and the brightest
        `REFERENCE_STAR_COUNT` isolated unsaturated stars. `None` when fewer
        than `MINIMUM_USABLE_APERTURES` apertures or
        `MINIMUM_USABLE_REFERENCE_STARS` stars could be found.
    """
    stars = detect_star_centroids(plane, count=DETECTED_STAR_COUNT)
    tree = cKDTree(stars) if len(stars) else None
    height, width = plane.shape
    generator = np.random.default_rng(APERTURE_SEED)
    positions: list[tuple[float, float]] = []
    for _ in range(40 * NOISE_APERTURE_COUNT):
        row = generator.uniform(EDGE_MARGIN_PX, height - EDGE_MARGIN_PX)
        column = generator.uniform(EDGE_MARGIN_PX, width - EDGE_MARGIN_PX)
        if tree is not None and tree.query([row, column])[0] <= STAR_CLEARANCE_PX:
            continue
        if signed_aperture_sum(plane, column, row) is None:
            continue
        positions.append((row, column))
        if len(positions) >= NOISE_APERTURE_COUNT:
            break
    isolated = _isolated_stars(stars)
    reference = isolated[:REFERENCE_STAR_COUNT]
    if len(positions) < MINIMUM_USABLE_APERTURES or len(reference) < MINIMUM_USABLE_REFERENCE_STARS:
        return None
    aperture_pixels = np.array(positions, dtype=float)
    return ApertureSet(
        aperture_sky=_pixels_to_sky(wcs, aperture_pixels),
        reference_sky=_pixels_to_sky(wcs, reference),
        wcs_source=wcs_source,
        pixel_scale_arcsec=_pixel_scale_arcsec(wcs),
        created_from=created_from,
    )


def _isolated_stars(stars: np.ndarray) -> np.ndarray:
    """Keep the stars that have no detected neighbor within the clearance.

    Parameters
    ----------
    stars : `numpy.ndarray`
        Star centers, shape ``(n, 2)`` as (row, column), brightest first.

    Returns
    -------
    isolated : `numpy.ndarray`
        The stars whose nearest neighbor is farther than `STAR_CLEARANCE_PX`,
        in the same order.
    """
    if len(stars) < 2:
        return stars
    nearest = cKDTree(stars).query(stars, k=2)[0][:, 1]
    return stars[nearest > STAR_CLEARANCE_PX]


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


def load_apertures(path: str) -> ApertureSet | None:
    """Read a target's stored apertures.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.

    Returns
    -------
    aperture_set : `ApertureSet` or `None`
        The stored set, or `None` when the file is missing or cannot be read
        (a warning says so in the second case).
    """
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return ApertureSet.from_json(json.load(handle))
    except (OSError, ValueError, KeyError) as error:
        logger.warning("The stored noise apertures at %s could not be read: %s", path, error)
        return None


def _store_apertures_once(path: str, aperture_set: ApertureSet) -> ApertureSet:
    """Save the set unless another run saved one first.

    The set is written to a temporary file and linked into place, which fails
    if the file exists. Two stacks of one target that finish together then
    cannot overwrite each other's apertures; the loser uses the winner's.

    Parameters
    ----------
    path : `str`
        The ``quality_apertures.json`` file.
    aperture_set : `ApertureSet`
        The set to save.

    Returns
    -------
    stored : `ApertureSet`
        The set now in the file: this one, or the other run's.
    """
    directory = os.path.dirname(path) or "."
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".quality_apertures_", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(aperture_set.to_json(), handle)
        try:
            os.link(temporary, path)
        except FileExistsError:
            return load_apertures(path) or aperture_set
        return aperture_set
    finally:
        os.unlink(temporary)


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
        when the stack's header has none.
    apertures_path : `str`
        Where the target's apertures are kept. They are created on the first
        stack and reused on every later one.
    crop_offset : `tuple` [`int`, `int`], optional
        The (columns, rows) cut from the stack's left and top edges when its
        edges were trimmed.

    Returns
    -------
    outcome : `StackNoiseOutcome`
        The measurement, or the reason there is none (no solution, an
        unreadable file, too few apertures or reference stars, or a pixel
        scale unlike the one the apertures were chosen at).
    """
    plane = _stack_plane(stack_path)
    if plane is None:
        return StackNoiseOutcome(None, "the stack file holds no pixels")
    wcs = _celestial_wcs(read_header(stack_path))
    wcs_source = "stack header"
    if wcs is None:
        if not reference_frame_path:
            return StackNoiseOutcome(None, NO_WCS_REASON)
        wcs, reason = _reference_frame_wcs(reference_frame_path, plane, crop_offset)
        wcs_source = "registration reference frame"
        if wcs is None:
            return StackNoiseOutcome(None, reason)

    aperture_set = load_apertures(apertures_path)
    created = False
    if aperture_set is None:
        chosen = choose_apertures(plane, wcs, wcs_source, os.path.basename(stack_path))
        if chosen is None:
            return StackNoiseOutcome(
                None, "too few empty apertures or reference stars could be found on this stack"
            )
        aperture_set = _store_apertures_once(apertures_path, chosen)
        created = aperture_set is chosen
    if wcs_source != "stack header" and not created:
        # The first guess is only good to the pointing error of the mount, and
        # this stack's apertures were chosen on another stack's guess.
        wcs, reason = _move_guess_onto_reference_stars(plane, wcs, aperture_set.reference_sky)
        if wcs is None:
            return StackNoiseOutcome(None, reason)
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

    sums = _sums_at(plane, wcs, aperture_set.aperture_sky)
    stars = _sums_at(plane, wcs, aperture_set.reference_sky)
    if sums.size < MINIMUM_USABLE_APERTURES:
        return StackNoiseOutcome(None, f"only {sums.size} of the stored apertures fall on this stack")
    if stars.size < MINIMUM_USABLE_REFERENCE_STARS:
        return StackNoiseOutcome(None, f"only {stars.size} of the reference stars fall on this stack")

    spread = MAD_TO_SIGMA * float(np.median(np.abs(sums - np.median(sums))))
    spread_error = spread / math.sqrt(2.0 * (sums.size - 1))
    reference_flux = float(np.median(stars))
    reference_error = (
        MEDIAN_ERROR_FACTOR
        * MAD_TO_SIGMA
        * float(np.median(np.abs(stars - reference_flux)))
        / math.sqrt(stars.size)
    )
    if spread <= 0 or reference_flux <= 0:
        return StackNoiseOutcome(None, "the apertures or the reference stars gave no usable spread or flux")
    normalized = spread / reference_flux
    relative_error = math.hypot(spread_error / spread, reference_error / reference_flux)
    return StackNoiseOutcome(
        StackNoiseResult(
            noise_adu=spread,
            noise_uncertainty_adu=spread_error,
            aperture_count=int(sums.size),
            reference_flux_adu=reference_flux,
            reference_uncertainty_adu=reference_error,
            reference_star_count=int(stars.size),
            normalized_noise=normalized,
            normalized_uncertainty=normalized * relative_error,
            wcs_source=wcs_source,
            apertures_created=created,
        )
    )


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
        The sums of the positions where `signed_aperture_sum` worked.
    """
    values = []
    for column, row in _sky_to_pixels(wcs, sky):
        if not (math.isfinite(column) and math.isfinite(row)):
            continue
        value = signed_aperture_sum(plane, float(column), float(row))
        if value is not None:
            values.append(value)
    return np.asarray(values, dtype=float)
