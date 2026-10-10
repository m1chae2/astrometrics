"""Checks the flat frames used to calibrate a stack, and cleans a noisy one.

A flat frame is a picture of an evenly lit surface. Dividing every light
frame by the master flat removes vignetting (darker corners) and dust
shadows. The division also copies the master flat's own noise into every
light frame. That noise is the same pattern in each frame, so stacking
does not average it away unless the frames are dithered (moved a few pixels
between exposures). A master flat with 4% noise puts a 4% fixed pattern into
a stack whose own noise is only 3-6% of the sky level.

This module has two jobs.

1. Measure the flat set: how many frames, how bright they are, and how
   noisy the master flat will be. A set that is too faint, too bright or too
   noisy is reported as an issue.
2. Choose how much to blur a noisy master flat. Vignetting and dust
   shadows are many pixels wide, so a light Gaussian blur keeps them. The
   blur width is the smallest that brings the master flat's noise down to
   `MAXIMUM_FLAT_NOISE_FRACTION`. Siril applies the blur (its ``gauss``
   command) while it builds the master. The cost is that real
   pixel-to-pixel sensitivity differences are blurred too. They are about 1%
   on a CMOS sensor, much less than the noise that is removed. Taking more
   flats is the better cure. The blur is a fallback for the flats at hand.

Colour (Bayer) sensors are measured but not smoothed, because blurring the
mosaic would mix the colour channels.
"""

import logging
import math
import os
from dataclasses import dataclass, field

import numpy as np

from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.foundation.errors import NotFoundError, StorageError

logger = logging.getLogger(__name__)

# The largest relative noise (standard deviation divided by the mean) the
# master flat may have before it is smoothed. A stack's own noise at the sky
# level measured 3-6% of the sky on the ASI533MM Pro stacks of 20 targets.
# A fixed 0.5% pattern adds under 3% to that noise's variance, which is
# invisible. The master flat of the single-flat library measured 4.5%, and
# the fixed pattern accounted for 86% of the fine-scale noise of the
# undithered M 101 stack. Smoothing that flat halved the stack's noise.
MAXIMUM_FLAT_NOISE_FRACTION = 0.005

# Flat frames should sit well above the read noise and well below the
# point where the pixels stop responding linearly. These two limits are
# common practice (flats at roughly a quarter to three quarters of full
# scale are ideal), set at the points where the advice stops being
# comfortable. They are design estimates and have not been validated on
# this observatory's data. The one flat measured here sat at 1% of full
# scale.
MINIMUM_FLAT_LEVEL_FRACTION = 0.10
MAXIMUM_FLAT_LEVEL_FRACTION = 0.90

# The smoothing width is kept between these limits, in pixels. Below 1
# pixel the blur does nothing useful. Above 8 pixels it starts to blur dust
# shadows (typically tens of pixels wide) and to follow noise structure.
# The start of each sentence `assess_flats` puts in `issues`. The gates in
# `assess_input_quality` tell the issues apart by these, so the text and the
# gates cannot drift apart.
FAINT_FLAT_ISSUE_PREFIX = "flats are faint:"
BRIGHT_FLAT_ISSUE_PREFIX = "flats are bright:"
NOISY_FLAT_ISSUE_PREFIX = "master flat noise is"
UNREADABLE_FLATS_ISSUE = "no flat frame could be read"

MINIMUM_SMOOTHING_SIGMA_PIXELS = 1.0
MAXIMUM_SMOOTHING_SIGMA_PIXELS = 8.0

# The expected noise of the master flat divides one frame's noise by the square
# root of the frame count, counting at most this many frames. A set of more
# frames is already far below the noise limit, and rejection of outliers in
# Siril's stack makes the true figure only a little better than the estimate.
MAXIMUM_FRAMES_AVERAGED = 32

# Written into the key of the stored master flats, so that a change to how
# the master flat is built or blurred does not reuse one built the old way.
# Increase it whenever that changes what the master flat contains. Version 4
# subtracts the bias from a lone flat too. Version 5 is saved as 32-bit
# floating point (the script sets ``set32bits``), not as the 16-bit integers
# of Siril's default preference.
FLAT_MASTER_RECIPE = "flat-recipe-5"

# The sources `FlatAssessment.full_scale_source` can name. The first is the
# sensor's own clip level from its camera profile. The other two are guesses
# made from the pixel values, used when no profile applies.
FULL_SCALE_SOURCE_CAMERA_PROFILE = "camera profile"
FULL_SCALE_SOURCE_FLOAT_RANGE = "guess: pixel values within 0-1 (floating-point image)"
FULL_SCALE_SOURCE_16_BIT_GUESS = "guess: 16-bit counts (no camera profile applies)"

# A flat pixel at or above this fraction of full scale counts as saturated.
_SATURATED_FRACTION_OF_FULL_SCALE = 0.98


@dataclass(frozen=True)
class FlatAssessment:
    """What was measured about a set of flat frames.

    Attributes
    ----------
    frame_count : `int`
        The number of flat frames in the set.
    level_fraction : `float` or `None`
        The mean brightness of a flat frame as a fraction of `full_scale`.
        `None` if no frame could be read.
    full_scale : `float` or `None`
        The pixel value that counts as full scale (100%) for the level and
        saturation checks. `None` if no frame could be read.
    full_scale_source : `str` or `None`
        Where `full_scale` came from. It starts with
        `FULL_SCALE_SOURCE_CAMERA_PROFILE` and names the camera when the
        value is the sensor's clip ceiling from its camera profile. Any
        other text means the value is a guess from the pixel values. `None`
        if no frame could be read.
    noise_fraction : `float` or `None`
        The expected relative noise of the master flat: the noise of one
        frame divided by the square root of the frame count. `None` if it
        could not be measured.
    smoothing_sigma_pixels : `float` or `None`
        The Gaussian width that brings the noise down to
        `MAXIMUM_FLAT_NOISE_FRACTION`. `None` if no smoothing is needed.
    issues : `list` [`str`]
        One plain-language sentence for each problem found.
    """

    frame_count: int
    level_fraction: float | None = None
    noise_fraction: float | None = None
    smoothing_sigma_pixels: float | None = None
    issues: list[str] = field(default_factory=list)
    full_scale: float | None = None
    full_scale_source: str | None = None

    @property
    def needs_smoothing(self) -> bool:
        """Whether the master flat is too noisy to use as it is."""
        return self.smoothing_sigma_pixels is not None

    def as_diagnostics(self) -> dict[str, object]:
        """Return the assessment as a plain dictionary for the run log.

        Returns
        -------
        diagnostics : `dict`
            The assessment's fields, with ``smoothing_sigma_pixels`` set to
            `None` when the master flat is used unsmoothed. ``full_scale``
            and ``full_scale_source`` say what the level was measured
            against and why.
        """
        return {
            "frame_count": self.frame_count,
            "level_fraction": self.level_fraction,
            "full_scale": self.full_scale,
            "full_scale_source": self.full_scale_source,
            "noise_fraction": self.noise_fraction,
            "smoothing_sigma_pixels": self.smoothing_sigma_pixels,
            "issues": list(self.issues),
        }


def _read_frame(path: str) -> np.ndarray | None:
    """Read one calibration frame as a 2-D float array.

    Parameters
    ----------
    path : `str`
        Path of the FITS file.

    Returns
    -------
    frame : `numpy.ndarray` or `None`
        The pixel values, or `None` if the file cannot be read or is not a
        single-plane image.
    """
    try:
        data = np.asarray(AstrometricsImage(path).data, dtype=np.float64)
    except (OSError, ValueError, KeyError, NotFoundError, StorageError) as error:
        logger.debug("Could not read calibration frame '%s': %s", path, error)
        return None
    if data.ndim == 3 and data.shape[0] == 1:
        data = data[0]
    return data if data.ndim == 2 else None


def _full_scale(frame: np.ndarray, camera: str | None = None) -> tuple[float, str]:
    """Find the brightest value a pixel of this frame can have, and say why.

    A floating-point image (all values within 0-1) always has a full scale
    of 1.0, whatever the camera. For integer counts, the camera's profile
    gives the sensor's clip ceiling: the highest value a saturated pixel
    reaches. That ceiling depends on the sensor. A 14-bit camera such as the
    Nikon D5300 stores its counts unscaled and tops out at 16383, so a flat
    that fills the sensor reads about 16383, not 65535. The 16-bit guess
    applies only when the camera is unknown or has no profile of its own.

    Parameters
    ----------
    frame : `numpy.ndarray`
        A calibration frame.
    camera : `str`, optional
        The name of the camera that took the frame, in any spelling the
        camera profiles accept. Leave it out if it is not known.

    Returns
    -------
    full_scale : `float`
        The frame's full-scale value: 1.0 for a floating-point image, the
        camera profile's clip ceiling, or else 65535.0 (16-bit counts).
    source : `str`
        Where the value came from. It begins with
        `FULL_SCALE_SOURCE_CAMERA_PROFILE` and names the camera for a
        profile, and otherwise is `FULL_SCALE_SOURCE_FLOAT_RANGE` or
        `FULL_SCALE_SOURCE_16_BIT_GUESS`.
    """
    if float(np.nanmax(frame)) <= 1.5:
        return 1.0, FULL_SCALE_SOURCE_FLOAT_RANGE
    if camera:
        profile = resolve_camera_profile(camera)
        if not profile.is_generic_fallback:
            return float(profile.clip_ceiling_adu.value), (
                f"{FULL_SCALE_SOURCE_CAMERA_PROFILE}: {profile.camera_name} clip ceiling"
            )
    return 65535.0, FULL_SCALE_SOURCE_16_BIT_GUESS


def measure_frame_noise_fraction(frame: np.ndarray) -> float | None:
    """Measure the pixel-to-pixel noise of one flat frame.

    The noise is estimated from the differences between neighbouring pixels.
    A smooth vignette cancels in those differences, so only the noise is
    left. Neighbouring pixels carry independent noise, so the spread of
    their difference is the square root of two times the noise of one pixel.
    The spread is measured with the median absolute deviation, which
    ignores dust specks and dead pixels.

    Parameters
    ----------
    frame : `numpy.ndarray`
        One flat frame, 2-D.

    Returns
    -------
    noise_fraction : `float` or `None`
        The noise divided by the frame's median, or `None` if the frame has
        no positive median.
    """
    median = float(np.nanmedian(frame))
    if not median > 0.0:
        return None
    differences = frame[:, 1:] - frame[:, :-1]
    mad = float(np.nanmedian(np.abs(differences - np.nanmedian(differences))))
    return 1.4826 * mad / math.sqrt(2.0) / median


def smoothing_sigma_for_noise(noise_fraction: float) -> float | None:
    """Find the Gaussian width that brings a flat's noise to the limit.

    A Gaussian blur of width sigma reduces the standard deviation of white
    noise by the factor ``1 / (2 * sigma * sqrt(pi))`` (the factor for a
    two-dimensional Gaussian kernel). The width is chosen so the result
    equals `MAXIMUM_FLAT_NOISE_FRACTION`.

    Parameters
    ----------
    noise_fraction : `float`
        The master flat's relative noise.

    Returns
    -------
    sigma : `float` or `None`
        The width in pixels, between `MINIMUM_SMOOTHING_SIGMA_PIXELS` and
        `MAXIMUM_SMOOTHING_SIGMA_PIXELS`. `None` if the noise is already
        within the limit.
    """
    if noise_fraction <= MAXIMUM_FLAT_NOISE_FRACTION:
        return None
    sigma = noise_fraction / (MAXIMUM_FLAT_NOISE_FRACTION * 2.0 * math.sqrt(math.pi))
    return min(MAXIMUM_SMOOTHING_SIGMA_PIXELS, max(MINIMUM_SMOOTHING_SIGMA_PIXELS, sigma))


def _unique_files(paths: list[str]) -> list[str]:
    """Drop paths that lead to the same file, keeping the first of each.

    Parameters
    ----------
    paths : `list` [`str`]
        Frame paths. Symbolic links count as the file they point to.

    Returns
    -------
    unique_paths : `list` [`str`]
        The paths, each real file once, in the original order.
    """
    seen: set[str] = set()
    unique = []
    for path in paths:
        real_path = os.path.realpath(path)
        if real_path not in seen:
            seen.add(real_path)
            unique.append(path)
    return unique


def assess_flats(flat_paths: list[str], camera: str | None = None) -> FlatAssessment:
    """Measure a set of flat frames and list what is wrong with it.

    The first frame gives the brightness. The noise of one frame comes from
    the first frame alone when the set has one frame, and from the
    difference of the first two frames when it has more. The difference
    cancels the vignette, dust and the pixel-to-pixel sensitivity pattern
    exactly, so it measures only the noise.

    Parameters
    ----------
    flat_paths : `list` [`str`]
        Paths of the flat frames.
    camera : `str`, optional
        The name of the camera that took the flats. Its profile sets the
        full-scale value for the brightness and saturation checks (see
        `_full_scale`). Without it, or for a camera with no profile, the
        assessment guesses the scale from the pixel values.

    Returns
    -------
    assessment : `FlatAssessment`
        The measurements and issues. A set with no readable frame has
        `level_fraction` and `noise_fraction` of `None` and one issue.
        `FlatAssessment.full_scale_source` records where the full-scale
        value came from.
    """
    flat_paths = _unique_files(flat_paths)
    frame_count = len(flat_paths)
    first = _read_frame(flat_paths[0]) if flat_paths else None
    if first is None:
        return FlatAssessment(frame_count, issues=[UNREADABLE_FLATS_ISSUE])

    issues: list[str] = []
    full_scale, full_scale_source = _full_scale(first, camera)
    level_fraction = float(np.nanmean(first)) / full_scale
    if level_fraction < MINIMUM_FLAT_LEVEL_FRACTION:
        issues.append(
            f"{FAINT_FLAT_ISSUE_PREFIX} {level_fraction:.1%} of full scale ({full_scale:g}, "
            f"{full_scale_source}), below {MINIMUM_FLAT_LEVEL_FRACTION:.0%}, so their noise is high"
        )
    saturated = float(np.mean(first >= _SATURATED_FRACTION_OF_FULL_SCALE * full_scale))
    if level_fraction > MAXIMUM_FLAT_LEVEL_FRACTION or saturated > 0.001:
        issues.append(
            f"{BRIGHT_FLAT_ISSUE_PREFIX} {level_fraction:.1%} of full scale ({full_scale:g}, "
            f"{full_scale_source}) with {saturated:.2%} of pixels saturated, so the response may "
            "not be linear"
        )

    single_frame_noise = None
    second = _read_frame(flat_paths[1]) if frame_count > 1 else None
    if second is not None and second.shape == first.shape:
        # Scale the second frame to the first, so a lamp that drifted
        # between exposures does not look like noise.
        scaled_second = second * (float(np.nanmedian(first)) / max(float(np.nanmedian(second)), 1e-12))
        difference = first - scaled_second
        mad = float(np.nanmedian(np.abs(difference - np.nanmedian(difference))))
        median = float(np.nanmedian(first))
        # Two frames that are exactly equal are one file (a copy or a second
        # listing of it), not two exposures. Their difference is zero, which
        # would read as a noiseless flat.
        if mad > 0.0 and median > 0.0:
            single_frame_noise = 1.4826 * mad / math.sqrt(2.0) / median
    if single_frame_noise is None:
        single_frame_noise = measure_frame_noise_fraction(first)

    noise_fraction = None
    sigma = None
    if single_frame_noise is not None:
        noise_fraction = single_frame_noise / math.sqrt(max(1, min(frame_count, MAXIMUM_FRAMES_AVERAGED)))
        sigma = smoothing_sigma_for_noise(noise_fraction)
        if sigma is not None:
            issues.append(
                f"{NOISY_FLAT_ISSUE_PREFIX} {noise_fraction:.2%} from {frame_count} frame(s), above the "
                f"{MAXIMUM_FLAT_NOISE_FRACTION:.1%} limit; take more flats"
            )
    return FlatAssessment(
        frame_count,
        level_fraction,
        noise_fraction,
        sigma,
        issues,
        full_scale=full_scale,
        full_scale_source=full_scale_source,
    )
