"""Tell whether an exposure length saturates the brightest star.

A camera records only so much light per pixel. A star bright enough to reach
that ceiling is clipped: its true brightness is lost, and in a spectrum the
clipped part of the trail reads too faint. This module answers two questions
about one raw frame, so a user can adjust an imaging session:

* is a star saturated at this exposure length (a yes or no per exposure), and
* what exposure length would keep the brightest star below the ceiling.

Everything here works on arrays. Reading frames, and finding the camera's
clipping value (its `CameraProfile.clip_ceiling_adu`), is left to the caller.

The older whole-image check (`is_saturation_significant`, a fixed 65000 ADU and
0.1% of pixels) is not used here: it never fires on a 14-bit camera such as
the Nikon D5300, whose frames top out at 16383, and a few blown stars are far
less than 0.1% of the pixels.
"""

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import binary_dilation, label, minimum_filter

from astrometricslib.foundation.camera_names import normalize_camera_name
from astrometricslib.pipelines.shared.quality.saturation import CLIP_CEILING_TOLERANCE_FRACTION

# A star counts as saturated when at least this many connected pixels sit at
# the frame's ceiling. Single pixels at the ceiling are common and are not
# stars: a Nikon D5300 frame had 188 to 481 isolated ones, hot pixels. The
# saturated stars in the same frames had cores of 9 to over 1700 pixels
# (ASI533 frames of Vega, NGC 2403 and Arcturus; D5300 frames of NGC 2244 and
# M 42). Four pixels (2 x 2) is the smallest blob that is not one hot pixel.
SATURATED_BLOB_MINIMUM_PIXELS = 4

# A pixel is at the ceiling when it is within this fraction of it. Saturated
# frames pile up at exactly one value, so a small tolerance only allows for the
# last count or two.
CEILING_TOLERANCE_FRACTION = CLIP_CEILING_TOLERANCE_FRACTION

# A frame is described as saturated when a star clips in at least this share of
# the frames sampled from an exposure group. One clipped frame in three can be
# a cosmic ray or a satellite trail; it is not a property of the exposure.
SATURATED_FRAME_FRACTION = 0.5

# The exposure recommended is the one that puts the brightest star's peak at
# this fraction of the room between the sky and the ceiling. It is the same
# 80% used when planning spectroscopy exposures (2026-09-21): far enough below
# the ceiling that seeing changes do not clip the star, near enough that the
# star is well exposed.
TARGET_PEAK_FRACTION = 0.8

# A star's peak is a real star, not a hot pixel, when its four nearest
# neighbours are at least this fraction of the way from the sky to the peak.
# A star with a FWHM of 2 px has neighbours at 50% of its peak; a hot pixel
# has neighbours at the sky level.
STAR_NEIGHBOUR_FRACTION = 0.2

# When the brightest star is clipped, its peak is estimated from the unclipped
# pixels around the clipped core. The star's profile is taken to be a Moffat
# function, I(r) = A (1 + (r / alpha)^2)^-beta, the usual model of a star
# blurred by the atmosphere: it has the broad wings a Gaussian lacks (a
# Gaussian extrapolated from a wide clipped core gave peaks a million times too
# high in a first attempt on real frames). beta = 3 is a typical seeing value
# (2.5 to 4); the estimate is not sensitive to it for a small clipped core and
# very sensitive for a large one, so the estimate is only an indication.
MOFFAT_BETA = 3.0

# FWHM in pixels of the stars in these stacks: 2.8 to 3.8 (2026 ASI533
# sessions), used when the frame's own FWHM is not known.
DEFAULT_STAR_FWHM_PIXELS = 3.0

# An estimate from the wings is not believed when it puts the star's peak more
# than this many times above the room between the sky and the ceiling. Checked
# on frames of the same field at two exposure lengths, where the peak must grow
# in proportion to the exposure: estimates up to 63 times the room grew within
# 66% of the exposure ratio (NGC 2403, NGC 2244, NGC 2903, M 31 and Vega, 2026
# sessions), while estimates above 100 times were off by 80% to more than a
# hundred times (M 42 and NGC 7023 on the D5300). 100 separates the two sets;
# it rests on those nine comparisons.
MAXIMUM_RELIABLE_PEAK_TO_ROOM_RATIO = 100.0

# What the quality rows state about an estimated peak (register entry STK-41).
# Two exposure lengths of one field were compared, star by star: the peak of
# the same star should grow in proportion to the exposure. The check counts a
# pair as good when the ratio of the two estimated peaks is within this
# fraction of the exposure ratio (the criterion of the earlier nine
# comparisons, 2026-09-21; the same star's unclipped peaks at two exposures
# differ by a median of 14% and a 90th percentile of 38% because of seeing,
# pixel phase and transparency, so 66% leaves room for that and little more).
CHECKED_ESTIMATE_ERROR_FRACTION = 0.66

# The share of cases within that fraction, over the ZWO ASI 533MM Pro cases
# below the range that follows (1,009 star pairs from 10 targets, 2026-10-23).
CHECKED_ESTIMATE_SHARE_WITHIN_ERROR = 0.97

# The same cases measured against the truth: a star unclipped at the shorter
# exposure has a measured peak, which times the exposure ratio is what the
# clipped star's peak should be. For the ZWO ASI 533MM Pro below 80 times the
# room (156 star pairs, 7 targets, mostly 1 to 5 times) the estimate divided
# by that peak had a median of 0.48 and a 10th to 90th percentile range of
# 0.22 to 2.7: the estimate is typically half the peak, and uncertain by a
# factor of 2 to 5 either way.
CHECKED_ESTIMATE_AGAINST_MEASURED_RATIOS = (0.22, 0.48, 2.7)

# The largest peak-to-room ratio of an estimate that was checked, for each
# camera, by the camera profile's name with case, spaces and punctuation
# ignored (so "ZWO ASI 533MM Pro" and "ZWO ASI533MM Pro" are one camera). The
# rule: every bin of estimate size
# up to the range has at least 90% of its cases (and a 90th percentile of the
# error) within `CHECKED_ESTIMATE_ERROR_FRACTION`, with at least 4 targets in
# the top bin. ASI533MM Pro: bins up to 63 times the room hold 90% to 100%
# (50 to 263 cases each), 63 to 80 times holds 96% (23 cases, 4 targets), and
# 80 to 100 times holds 83% with a 90th percentile error of 1.5. The D5300
# fails at its first populated bin (57% at 2 to 5 times) and stays at 62% to
# 80% in every bin from 5 to 1,000 times, so no range is stated for it, and an
# estimate for any camera not listed is beyond the checked range.
CHECKED_ESTIMATE_RANGE_ROOM_MULTIPLES_BY_CAMERA: dict[str, float] = {
    normalize_camera_name("ZWO ASI 533MM Pro"): 80.0
}


def checked_estimate_range(camera_name: str | None) -> float:
    """Give the largest checked size of a peak estimate for a camera.

    Parameters
    ----------
    camera_name : `str` or `None`
        The camera profile's name, in any spelling.

    Returns
    -------
    room_multiples : `float`
        The largest estimate, in multiples of the room between the sky and the
        ceiling, whose growth with the exposure was checked. 0 when the camera
        has no checked range (so any estimate is beyond it).
    """
    if not camera_name:
        return 0.0
    return CHECKED_ESTIMATE_RANGE_ROOM_MULTIPLES_BY_CAMERA.get(normalize_camera_name(camera_name), 0.0)


# How far outside the clipped core (in pixels) the pixels used for the
# estimate lie, and how wide that band is.
CORE_RING_OFFSET_PIXELS = 1
CORE_RING_WIDTH_PIXELS = 1


@dataclass
class FrameSaturation:
    """What one raw frame shows about saturation.

    Attributes
    ----------
    saturated : `bool`
        Whether a star is clipped at the ceiling.
    ceiling_adu : `float`
        The value at which the frame clips.
    sky_adu : `float`
        The median pixel value, taken as the sky level.
    peak_above_sky_adu : `float` or `None`
        How far the brightest star rises above the sky, in ADU: measured when
        the star is not clipped, estimated from its wings when it is, and
        `None` when it cannot be worked out (or the star is so heavily clipped
        that the estimate is not believed, see
        `MAXIMUM_RELIABLE_PEAK_TO_ROOM_RATIO`).
    peak_is_estimated : `bool`
        Whether `peak_above_sky_adu` came from the wings of a clipped star.
    """

    saturated: bool
    ceiling_adu: float
    sky_adu: float
    peak_above_sky_adu: float | None
    peak_is_estimated: bool


def _clipped_blobs(frame: np.ndarray, ceiling: float) -> tuple[np.ndarray, np.ndarray]:
    """Label the groups of connected pixels that sit at the ceiling.

    Returns
    -------
    labels : `numpy.ndarray`
        Blob number of each pixel (0 is not clipped).
    sizes : `numpy.ndarray`
        The number of pixels of blob 1, 2, ... (in that order).
    """
    labels, count = label(frame >= ceiling * (1.0 - CEILING_TOLERANCE_FRACTION))
    if count == 0:
        return labels, np.zeros(0, dtype=int)
    return labels, np.bincount(labels.ravel())[1:]


def _measured_peak_above_sky(frame: np.ndarray, sky: float) -> float | None:
    """Find the brightest star's peak, ignoring hot pixels.

    Returns
    -------
    peak : `float` or `None`
        The highest value above the sky among pixels whose four nearest
        neighbours are at least `STAR_NEIGHBOUR_FRACTION` of the way up, or
        `None` when no pixel qualifies.
    """
    data = np.asarray(frame, dtype=np.float32)
    # Minimum of the four nearest neighbours: a cross-shaped footprint that
    # leaves the centre pixel out.
    footprint = np.array([[0, 1, 0], [1, 0, 1], [0, 1, 0]], dtype=bool)
    neighbour_minimum = minimum_filter(data, footprint=footprint, mode="nearest")
    above_sky = data - sky
    qualifies = (neighbour_minimum - sky) >= STAR_NEIGHBOUR_FRACTION * above_sky
    qualifies &= above_sky > 0
    if not qualifies.any():
        return None
    return float(above_sky[qualifies].max())


def _estimated_peak_from_wings(
    frame: np.ndarray, labels: np.ndarray, sizes: np.ndarray, sky: float, fwhm_pixels: float
) -> float | None:
    """Estimate the peak of the largest clipped star from the pixels around it.

    The star's profile is taken as a Moffat function of the given FWHM. The
    value in a thin ring just outside the clipped core is extrapolated inward
    to the star's centre. For a trail rather than a round star, the narrow
    half-width of the clipped region is used, because the profile falls off
    across the trail.

    Returns
    -------
    peak : `float` or `None`
        The estimated peak above the sky, or `None` if the ring is not above
        the sky or the core is a single pixel.
    """
    largest = int(np.argmax(sizes)) + 1
    core = labels == largest
    rows, columns = np.nonzero(core)
    if rows.size < SATURATED_BLOB_MINIMUM_PIXELS:
        return None
    # The half-width across the core: for a filled ellipse, the semi-minor
    # axis is twice the standard deviation along the narrow direction.
    covariance = np.cov(np.vstack([rows, columns]).astype(np.float64))
    narrow_variance = float(np.min(np.linalg.eigvalsh(covariance)))
    half_width = 2.0 * np.sqrt(max(narrow_variance, 0.0))
    inner = binary_dilation(core, iterations=CORE_RING_OFFSET_PIXELS)
    outer = binary_dilation(core, iterations=CORE_RING_OFFSET_PIXELS + CORE_RING_WIDTH_PIXELS)
    ring_values = np.asarray(frame)[outer & ~inner]
    if ring_values.size == 0:
        return None
    ring_above_sky = float(np.median(ring_values)) - sky
    if ring_above_sky <= 0:
        return None
    ring_radius = half_width + CORE_RING_OFFSET_PIXELS + 0.5 * CORE_RING_WIDTH_PIXELS
    # For a Moffat function, FWHM = 2 alpha sqrt(2^(1/beta) - 1).
    alpha = fwhm_pixels / (2.0 * np.sqrt(2.0 ** (1.0 / MOFFAT_BETA) - 1.0))
    return ring_above_sky * float((1.0 + (ring_radius / alpha) ** 2) ** MOFFAT_BETA)


def measure_frame_saturation(
    frame: np.ndarray, ceiling_adu: float, fwhm_pixels: float = DEFAULT_STAR_FWHM_PIXELS
) -> FrameSaturation:
    """Measure how close the brightest star of a raw frame is to the ceiling.

    Parameters
    ----------
    frame : `numpy.ndarray`
        One raw frame, 2-D (a colour frame is read from its first plane).
    ceiling_adu : `float`
        The value at which this camera's frames clip, in ADU. It is used when
        the frame itself is not clipped, so no ceiling can be read from its
        pixels. Take it from the camera's profile.
    fwhm_pixels : `float`, optional
        The FWHM of a star in this frame, used to estimate a clipped star's
        peak.

    Returns
    -------
    saturation : `FrameSaturation`
        Whether a star is clipped, the ceiling and sky, and the peak.
    """
    data = np.asarray(frame)
    if data.ndim == 3:
        data = data[0]
    sky = float(np.median(data))
    # A clipped frame shows its own ceiling: the maximum, when several
    # connected pixels are at it.
    own_maximum = float(data.max())
    labels, sizes = _clipped_blobs(data, own_maximum)
    if sizes.size and sizes.max() >= SATURATED_BLOB_MINIMUM_PIXELS:
        ceiling = own_maximum
        peak = _estimated_peak_from_wings(data, labels, sizes, sky, fwhm_pixels)
        if peak is not None and peak > MAXIMUM_RELIABLE_PEAK_TO_ROOM_RATIO * (ceiling - sky):
            peak = None
        return FrameSaturation(True, ceiling, sky, peak, True)
    # Not clipped: the ceiling comes from the camera, and the peak is measured.
    return FrameSaturation(False, ceiling_adu, sky, _measured_peak_above_sky(data, sky), False)


def group_is_saturated(frames: list[FrameSaturation]) -> bool:
    """Decide whether an exposure length saturates a star.

    Parameters
    ----------
    frames : `list` [`FrameSaturation`]
        The frames sampled from one exposure group.

    Returns
    -------
    saturated : `bool`
        `True` when at least `SATURATED_FRAME_FRACTION` of the frames are
        clipped; `False` for an empty list.
    """
    if not frames:
        return False
    return sum(frame.saturated for frame in frames) / len(frames) >= SATURATED_FRAME_FRACTION


def recommend_exposure_seconds(exposure_seconds: float, frames: list[FrameSaturation]) -> float | None:
    """Work out the exposure that puts the brightest star at the target.

    The brightness above the sky grows in proportion to the exposure, so the
    exposure is scaled by how far the peak is from the level wanted.

    Parameters
    ----------
    exposure_seconds : `float`
        The exposure length of the frames.
    frames : `list` [`FrameSaturation`]
        The frames sampled at that exposure length.

    Returns
    -------
    recommended : `float` or `None`
        The exposure length in seconds that puts the median peak at
        `TARGET_PEAK_FRACTION` of the room above the sky, or `None` when no
        frame gave a peak.
    """
    peaks = [frame.peak_above_sky_adu for frame in frames if frame.peak_above_sky_adu]
    if not peaks or exposure_seconds <= 0:
        return None
    room = float(np.median([frame.ceiling_adu - frame.sky_adu for frame in frames]))
    return exposure_seconds * TARGET_PEAK_FRACTION * room / float(np.median(peaks))


def recommend_stack_exposure_seconds(
    exposure_seconds: list[float], group_frames: list[list[FrameSaturation]]
) -> float | None:
    """Choose one recommended exposure for a stack made of several groups.

    A group whose stars are not clipped gives a measured peak, so it is trusted
    over one that has to be estimated from wings. Among those, the longest
    exposure gives the peak with the best signal. If every group is clipped,
    the median of the estimates is used.

    Parameters
    ----------
    exposure_seconds : `list` [`float`]
        The exposure length of each group.
    group_frames : `list` [`list` [`FrameSaturation`]]
        The sampled frames of each group, in the same order.

    Returns
    -------
    recommended : `float` or `None`
        The recommended exposure in seconds, or `None` when no group gave one.
    """
    measured, estimated = [], []
    for exposure, frames in zip(exposure_seconds, group_frames, strict=True):
        recommendation = recommend_exposure_seconds(exposure, frames)
        if recommendation is None:
            continue
        (estimated if group_is_saturated(frames) else measured).append((exposure, recommendation))
    if measured:
        return max(measured)[1]
    if estimated:
        return float(np.median([recommendation for _, recommendation in estimated]))
    return None
