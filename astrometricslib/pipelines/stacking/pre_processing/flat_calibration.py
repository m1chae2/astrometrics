"""Checks the flat frames used to calibrate a stack, and cleans a noisy one.

A flat frame is a picture of an evenly lit surface. Dividing every light
frame by the master flat removes vignetting (darker corners) and dust
shadows. The division also copies the master flat's own noise into every
light frame. That noise is the same pattern in each frame, so stacking
does not average it away unless the frames are dithered (moved a few pixels
between exposures). A master flat with 4% noise puts a 4% fixed pattern into
a stack whose own noise is only 3-6% of the sky level.

This module has three jobs.

1. Find the flat frames that are clipped (see `find_clipped_channels`) so
   the stacker can leave them out of the master flat. A frame whose pixels
   sit at the sensor's ceiling no longer shows the vignette, so averaging it
   in flattens the master and, on a colour sensor, tints the corners.
2. Measure the flat set that is left: how many frames, how bright they are,
   and how noisy the master flat will be. A set that is too faint, too
   bright or too noisy is reported as an issue.
3. Choose how much to blur a noisy master flat. Vignetting and dust
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

from astrometricslib._internal.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib._internal.drivers.image import AstrometricsImage
from astrometricslib.foundation.errors import NotFoundError, StorageError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)

logger = logging.getLogger(__name__)

# The largest relative noise (standard deviation divided by the mean) the
# master flat may have before it is smoothed. A stack's own noise at the sky
# level measured 3-6% of the sky on the ASI533MM Pro stacks of 20 targets.
# A fixed 0.5% pattern adds under 3% to that noise's variance, which is
# invisible. The master flat of the single-flat library measured 4.5%, and
# the fixed pattern accounted for 86% of the fine-scale noise of the
# undithered M 101 stack. Smoothing that flat halved the stack's noise.
MAXIMUM_FLAT_NOISE_FRACTION = TunedParameter(
    name="stacking.maximum_flat_noise_fraction",
    default=0.005,
    unit="fraction",
    description="Largest relative noise a master flat may have before it is smoothed.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.EQUIPMENT_PROFILE,
    equipment=frozenset({EquipmentKind.CAMERA}),
    register_entry="STK-05",
)

# Flat frames should sit well above the read noise and well below the
# point where the pixels stop responding linearly. These two limits are
# common practice (flats at roughly a quarter to three quarters of full
# scale are ideal), set at the points where the advice stops being
# comfortable. They are design estimates and have not been validated on
# this observatory's data. The one flat measured here sat at 1% of full
# scale.
MINIMUM_FLAT_LEVEL_FRACTION = TunedParameter(
    name="stacking.minimum_flat_level_fraction",
    default=0.10,
    unit="fraction of full scale",
    description="Lowest flat level, as a share of full scale, that is accepted.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D05",
)
MAXIMUM_FLAT_LEVEL_FRACTION = TunedParameter(
    name="stacking.maximum_flat_level_fraction",
    default=0.90,
    unit="fraction of full scale",
    description="Highest flat level, as a share of full scale, that is accepted.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D05",
)

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

MINIMUM_SMOOTHING_SIGMA_PIXELS = TunedParameter(
    name="stacking.minimum_smoothing_sigma_pixels",
    default=1.0,
    unit="px",
    description="Narrowest Gaussian width used when a noisy master flat is smoothed.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D06",
)
MAXIMUM_SMOOTHING_SIGMA_PIXELS = TunedParameter(
    name="stacking.maximum_smoothing_sigma_pixels",
    default=8.0,
    unit="px",
    description="Widest Gaussian width used when a noisy master flat is smoothed.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D06",
)

# The expected noise of the master flat divides one frame's noise by the square
# root of the frame count, counting at most this many frames. A set of more
# frames is already far below the noise limit, and rejection of outliers in
# Siril's stack makes the true figure only a little better than the estimate.
MAXIMUM_FRAMES_AVERAGED = TunedParameter(
    name="stacking.maximum_frames_averaged",
    default=32,
    unit="frames",
    description="Most frames counted when the expected noise of a master flat is worked out.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D63",
)

# The sources `FlatAssessment.full_scale_source` can name. The first is the
# sensor's own clip level from its camera profile. The other two are guesses
# made from the pixel values, used when no profile applies.
FULL_SCALE_SOURCE_CAMERA_PROFILE = "camera profile"
FULL_SCALE_SOURCE_FLOAT_RANGE = "guess: pixel values within 0-1 (floating-point image)"
FULL_SCALE_SOURCE_16_BIT_GUESS = "guess: 16-bit counts (no camera profile applies)"

# A flat pixel at or above this fraction of full scale counts as saturated.
_SATURATED_FRACTION_OF_FULL_SCALE = TunedParameter(
    name="stacking.saturated_fraction_of_full_scale",
    default=0.98,
    unit="fraction of full scale",
    description="Share of full scale at or above which a flat pixel counts as saturated.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D18",
)

# A flat frame is clipped when more than this share of the pixels of any one
# colour channel (of the whole frame, for a monochrome sensor) are saturated.
# This is the limit `assess_flats` has always used for a bright flat, now
# applied to each frame and each channel: one pixel in a thousand at the
# ceiling. Not measured; on the Nikon D5300 flats of 2024-02-01 the clipped
# frames have 100% of their green and blue pixels at the ceiling and the kept
# ones none, so any limit between the two gives the same split.
_CLIPPED_SHARE_LIMIT = TunedParameter(
    name="stacking.flat_clipped_share_limit",
    default=0.001,
    unit="fraction of pixels",
    description=(
        "Largest share of saturated pixels in any one colour channel of a flat frame before it is left out."
    ),
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D18",
)

# The names of the Bayer letters, for the sentence that reports clipping.
_CHANNEL_NAMES = {"R": "red", "G": "green", "B": "blue"}
_CHANNEL_ORDER = "RGB"


@dataclass(frozen=True)
class FlatAssessment:
    """What was measured about a set of flat frames.

    Attributes
    ----------
    frame_count : `int`
        The number of flat frames in the set, clipped ones included.
    level_fraction : `float` or `None`
        The mean brightness of the flat frames that are used, as a fraction
        of `full_scale`. `None` if no frame could be read.
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
    left_out_paths : `list` [`str`]
        The frames that are clipped and so left out of the master flat, in
        the order they were given. Empty when no frame is clipped, and also
        when every frame is clipped (no frame is left out then, because
        there would be no flat).
    """

    frame_count: int
    level_fraction: float | None = None
    noise_fraction: float | None = None
    smoothing_sigma_pixels: float | None = None
    issues: list[str] = field(default_factory=list)
    full_scale: float | None = None
    full_scale_source: str | None = None
    left_out_paths: list[str] = field(default_factory=list)

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
            "left_out_frame_count": len(self.left_out_paths),
        }


def _read_frame_and_pattern(path: str) -> tuple[np.ndarray, str] | None:
    """Read one calibration frame and the colour layout of its pixels.

    Parameters
    ----------
    path : `str`
        Path of the FITS file.

    Returns
    -------
    frame_and_pattern : `tuple` or `None`
        The pixel values as a 2-D float array, and the Bayer layout of the
        four pixels of the top-left 2 by 2 block in row order, for example
        ``"RGGB"``. The layout is empty for a monochrome frame. `None` if
        the file cannot be read or is not a single-plane image.
    """
    try:
        image = AstrometricsImage(path)
        data = np.asarray(image.data, dtype=np.float64)
        header = image.header
    except (OSError, ValueError, KeyError, NotFoundError, StorageError) as error:
        logger.debug("Could not read calibration frame '%s': %s", path, error)
        return None
    if data.ndim == 3 and data.shape[0] == 1:
        data = data[0]
    if data.ndim != 2:
        return None
    pattern = str(header.get("BAYERPAT", "")).strip().upper()
    if len(pattern) != 4 or not pattern.isalpha():
        return data, ""
    # Shift the layout by the header's Bayer offsets, so it names the pixels
    # as stored. The offsets are 0 or 1.
    try:
        x_offset = int(header.get("XBAYROFF", 0)) % 2
        y_offset = int(header.get("YBAYROFF", 0)) % 2
    except TypeError, ValueError:
        x_offset = y_offset = 0
    shifted = "".join(
        pattern[((row + y_offset) % 2) * 2 + (column + x_offset) % 2] for row in (0, 1) for column in (0, 1)
    )
    return data, shifted


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
    read = _read_frame_and_pattern(path)
    return None if read is None else read[0]


def find_clipped_channels(frame: np.ndarray, pattern: str, saturation_level: float) -> list[str]:
    """List the colour channels of a flat frame that are clipped.

    A channel is clipped when more than `_CLIPPED_SHARE_LIMIT` of its pixels
    are at or above `saturation_level`. On a colour sensor the test is made
    for each channel on its own. A share taken over the whole frame can
    miss a frame whose green pixels are all clipped while its red pixels are
    not, because the clipped channel is only part of the frame. Dividing
    by such a frame's master flat tints the corners.

    Parameters
    ----------
    frame : `numpy.ndarray`
        One flat frame, 2-D.
    pattern : `str`
        The Bayer layout of the top-left 2 by 2 block in row order (for
        example ``"RGGB"``), or an empty string for a monochrome sensor.
    saturation_level : `float`
        The pixel value at or above which a pixel counts as saturated.

    Returns
    -------
    channels : `list` [`str`]
        The letters of the clipped channels (``"R"``, ``"G"``, ``"B"``) in
        that order, sorted otherwise. For a monochrome frame, ``[""]`` when
        it is clipped. Empty when the frame is not clipped.
    """
    if not pattern:
        saturated_share = float(np.mean(frame >= saturation_level))
        return [""] if saturated_share > resolve(_CLIPPED_SHARE_LIMIT).value else []
    saturated_count: dict[str, int] = {}
    pixel_count: dict[str, int] = {}
    for position, letter in enumerate(pattern):
        block = frame[position // 2 :: 2, position % 2 :: 2]
        saturated_count[letter] = saturated_count.get(letter, 0) + int(
            np.count_nonzero(block >= saturation_level)
        )
        pixel_count[letter] = pixel_count.get(letter, 0) + block.size
    clipped = [
        letter
        for letter in saturated_count
        if saturated_count[letter] > resolve(_CLIPPED_SHARE_LIMIT).value * pixel_count[letter]
    ]
    return sorted(clipped, key=lambda letter: (_CHANNEL_ORDER.find(letter) % 4, letter))


def _clipped_flats_sentence(left_out: int, total: int, channels: set[str], all_clipped: bool) -> str:
    """Write the issue sentence about clipped flat frames.

    Parameters
    ----------
    left_out : `int`
        The number of clipped frames.
    total : `int`
        The number of frames in the set.
    channels : `set` [`str`]
        The clipped channels over all of those frames (letters, with ``""``
        for a monochrome sensor).
    all_clipped : `bool`
        Whether every frame is clipped, so none could be left out.

    Returns
    -------
    sentence : `str`
        One plain-language sentence.
    """
    ordered = sorted(channels - {""}, key=lambda letter: (_CHANNEL_ORDER.find(letter) % 4, letter))
    names = [_CHANNEL_NAMES.get(letter, letter) for letter in ordered]
    where = f"{' and '.join(names)} at the sensor's ceiling" if names else "at the sensor's ceiling"
    if all_clipped:
        return (
            f"all {total} flat frames are clipped ({where}), so none could be left out "
            "and the master flat has lost its vignette shape in the clipped channels"
        )
    verb, tail = ("is", "was") if left_out == 1 else ("are", "were")
    return (
        f"{left_out} of {total} flat frames {verb} clipped ({where}) and {tail} left out of the master flat"
    )


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
    maximum_flat_noise_fraction = resolve(MAXIMUM_FLAT_NOISE_FRACTION).value
    if noise_fraction <= maximum_flat_noise_fraction:
        return None
    sigma = noise_fraction / (maximum_flat_noise_fraction * 2.0 * math.sqrt(math.pi))
    return min(
        resolve(MAXIMUM_SMOOTHING_SIGMA_PIXELS).value,
        max(resolve(MINIMUM_SMOOTHING_SIGMA_PIXELS).value, sigma),
    )


def _unique_files(paths: list[str]) -> list[str]:
    """Drop paths that lead to the same file, keeping the first of each.

    Parameters
    ----------
    paths : `list` [`str`]
        Frame paths. Symbolic links count as the file they point to, and so
        do hard links (the same file under a second name): each file counts
        once, however many names it has.

    Returns
    -------
    unique_paths : `list` [`str`]
        The paths, each real file once, in the original order.
    """
    seen: set[object] = set()
    unique = []
    for path in paths:
        try:
            status = os.stat(path)
            identity: object = (status.st_dev, status.st_ino)
        except OSError:
            identity = os.path.realpath(path)
        if identity not in seen:
            seen.add(identity)
            unique.append(path)
    return unique


def assess_flats(flat_paths: list[str], camera: str | None = None) -> FlatAssessment:
    """Measure a set of flat frames and list what is wrong with it.

    Every frame is first checked for clipping (see `find_clipped_channels`).
    A clipped frame is named in `FlatAssessment.left_out_paths`, so the
    stacker leaves it out of the master flat, and the measurements below
    describe the frames that are kept. If every frame is clipped, none is
    left out (a master flat from them is still better than none, and the
    level check reports them as bright).

    The brightness is the mean over the kept frames. The noise of one frame
    comes from the first kept frame alone when one frame is kept, and from
    the difference of the first two kept frames when more are. The
    difference cancels the vignette, dust and the pixel-to-pixel sensitivity
    pattern exactly, so it measures only the noise.

    Each frame is read once to check it. The first two kept frames are read
    a second time for the noise measurement, so only two frames are held in
    memory at once.

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
    minimum_flat_level_fraction = resolve(MINIMUM_FLAT_LEVEL_FRACTION).value
    flat_paths = _unique_files(flat_paths)
    frame_count = len(flat_paths)

    full_scale = 0.0
    full_scale_source = ""
    readable: list[tuple[str, float, list[str]]] = []
    for path in flat_paths:
        read = _read_frame_and_pattern(path)
        if read is None:
            continue
        frame, pattern = read
        if not readable:
            full_scale, full_scale_source = _full_scale(frame, camera)
        saturation_level = resolve(_SATURATED_FRACTION_OF_FULL_SCALE).value * full_scale
        readable.append((
            path,
            float(np.nanmean(frame)),
            find_clipped_channels(frame, pattern, saturation_level),
        ))
    if not readable:
        return FlatAssessment(frame_count, issues=[UNREADABLE_FLATS_ISSUE])

    issues: list[str] = []
    clipped = [entry for entry in readable if entry[2]]
    all_clipped = len(clipped) == len(readable)
    left_out_paths: list[str] = []
    if clipped:
        issues.append(
            _clipped_flats_sentence(
                len(clipped),
                frame_count,
                {letter for _, _, channels in clipped for letter in channels},
                all_clipped,
            )
        )
        if not all_clipped:
            left_out_paths = [path for path, _, _ in clipped]
    left_out = set(left_out_paths)
    kept = [entry for entry in readable if entry[0] not in left_out]
    kept_count = len(kept)

    first = _read_frame(kept[0][0])
    if first is None:
        return FlatAssessment(frame_count, issues=[UNREADABLE_FLATS_ISSUE])
    level_fraction = float(np.mean([mean for _, mean, _ in kept])) / full_scale
    if level_fraction < minimum_flat_level_fraction:
        issues.append(
            f"{FAINT_FLAT_ISSUE_PREFIX} {level_fraction:.1%} of full scale ({full_scale:g}, "
            f"{full_scale_source}), below {minimum_flat_level_fraction:.0%}, so their noise is high"
        )
    saturated = float(np.mean(first >= resolve(_SATURATED_FRACTION_OF_FULL_SCALE).value * full_scale))
    clipped_share_limit = resolve(_CLIPPED_SHARE_LIMIT).value
    if level_fraction > resolve(MAXIMUM_FLAT_LEVEL_FRACTION).value or saturated > clipped_share_limit:
        issues.append(
            f"{BRIGHT_FLAT_ISSUE_PREFIX} {level_fraction:.1%} of full scale ({full_scale:g}, "
            f"{full_scale_source}) with {saturated:.2%} of pixels saturated, so the response may "
            "not be linear"
        )

    single_frame_noise = None
    second = _read_frame(kept[1][0]) if kept_count > 1 else None
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
        noise_fraction = single_frame_noise / math.sqrt(
            max(1, min(kept_count, resolve(MAXIMUM_FRAMES_AVERAGED).value))
        )
        sigma = smoothing_sigma_for_noise(noise_fraction)
        if sigma is not None:
            issues.append(
                f"{NOISY_FLAT_ISSUE_PREFIX} {noise_fraction:.2%} from {kept_count} frame(s), above the "
                f"{resolve(MAXIMUM_FLAT_NOISE_FRACTION).value:.1%} limit"
            )
    return FlatAssessment(
        frame_count,
        level_fraction,
        noise_fraction,
        sigma,
        issues,
        full_scale=full_scale,
        full_scale_source=full_scale_source,
        left_out_paths=left_out_paths,
    )
