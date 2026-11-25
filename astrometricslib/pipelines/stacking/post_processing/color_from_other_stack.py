"""Makes a color picture of a monochrome stack, with color from another stack.

A monochrome camera (such as the ZWO ASI533MM with its luminance filter)
gives a sharp, deep picture with no color. A color camera on the same target
(such as the Nikon D5300) gives color, but with wider stars and more noise.
When a target has both, this step makes a second picture of the monochrome
stack, beside its own picture: the brightness and detail come from the
monochrome picture and the color from the color stack. This is the classic
LRGB method, where L is the luminance. The monochrome picture and both
stacks are never changed.

1. *Which color stack.* The target's stacks of other setups
   (``stacks_by_configuration``) are searched for a color stack with a
   finished picture. One at the same focal length is preferred, else the
   nearest focal length.
2. *Alignment.* The color picture and the linear color stack are moved onto
   the monochrome picture's pixel grid (see `other_stack_alignment`). The
   step is skipped when the two grids do not agree well enough or when the
   color stack does not cover enough of the monochrome frame.
3. *Color of the extended light.* StarNet (run through Siril) takes the
   stars out of both pictures. The starless color picture's sky is made gray
   first: the tint of its sky (each channel's offset from the mean of the
   three, measured away from the stars where both pictures lie near their
   own sky level) is smoothed into a map and taken away everywhere, under a
   nebula or galaxy too, because a sky tint near an object is also in the
   object's light. It is then turned into CIE Lab, a color space with one
   lightness channel (L) and two color channels (a, b). Its color channels are
   blurred, so the color stack's noise does not show, and scaled so the
   color keeps its strength at the monochrome picture's lightness. The color
   fades in where the color stack's starless picture stands from 2 to 8
   noise widths above its sky, so the noisy sky stays gray. StarNet leaves
   the faint outer skirt of each color star behind, and a lens whose red
   stars are wider than its green ones (such as the Nikkor at 300 mm) leaves
   it red. So around each monochrome star, out to a few color-stack star
   widths, the color is replaced by the color of the light just beyond.
   Around a saturated star whose wing color is not trusted (see
   `star_color_measurement`), the skirt reaches much farther, so the color
   is replaced out to a few radii of the star's footprint.
4. *Color of the stars.* Each star's color is measured once on the linear
   color stack and painted only inside the monochrome star (see
   `star_color_measurement`).
5. *The picture.* The monochrome lightness is joined with the color
   channels and turned back into red, green and blue. The picture is saved
   as a JPEG beside the monochrome picture
   (``<stack>_color_preview.jpg``), with a small JSON record of how it was
   made (``<stack>_color_preview.json``).

A skip, such as no color stack, is normal and is logged as information. A
failure of a step that should have worked, such as StarNet, is logged as a
warning and stored as a notice of the job.
"""

import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from subprocess import SubprocessError
from typing import Any

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.table import Table
from astropy.wcs import WCS
from PIL import Image
from scipy import ndimage
from skimage.color import lab2rgb, rgb2lab

from astrometricslib._internal.drivers.cached_simbad_driver import get_simbad_driver
from astrometricslib._internal.drivers.fits_access import read_data, read_header, write_image
from astrometricslib.foundation.config import get_configuration
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.paths import siril_work_directory
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.gaia_star_colors import (
    BRIGHTEST_G_MAGNITUDE,
    gaia_star_colors_in_footprint,
)
from astrometricslib.pipelines.shared.simbad_object_types import STAR_REGION_VOTABLE_FIELDS
from astrometricslib.pipelines.shared.stack_preview_path import (
    PREVIEW_JPEG_QUALITY,
    color_preview_path_for,
    color_preview_record_path_for,
    processed_fits_path_for,
)
from astrometricslib.pipelines.stacking.post_processing.other_stack_alignment import (
    AlignmentFit,
    align_with_stars,
    find_bright_stars,
)
from astrometricslib.pipelines.stacking.post_processing.sky_levelling import sky_weight
from astrometricslib.pipelines.stacking.post_processing.stack_image import robust_sigma
from astrometricslib.pipelines.stacking.post_processing.stack_preview import (
    SkippedPreviewStep,
    has_plate_solution,
    is_colour_image,
    run_preview_script,
)
from astrometricslib.pipelines.stacking.post_processing.star_color_measurement import (
    SOURCE_NAMES,
    StarColors,
    StarFootprints,
    measure_star_colors,
)
from astrometricslib.pipelines.stacking.post_processing.starnet_stretch import (
    STARLESS_NAME,
    collect_starnet_outputs,
)
from astrometricslib.pipelines.stacking.processing.image_registration import warp

logger = logging.getLogger(__name__)

__all__ = [
    "COLOR_PREVIEW_HALO_WIDTHS",
    "COLOR_PREVIEW_MAXIMUM_ALIGNMENT_RMS_PIXELS",
    "COLOR_PREVIEW_MINIMUM_COVERAGE",
    "COLOR_PREVIEW_SATURATED_HALO_RADII",
    "COLOR_PREVIEW_SKY_NOISE_MULTIPLE",
    "COLOR_PREVIEW_SKY_TINT_LIMITS",
    "COLOR_PREVIEW_SKY_TINT_SMOOTHING",
    "ColorPreviewOutcome",
    "ColorStackChoice",
    "choose_color_stack",
    "compose_color_picture",
    "extended_color",
    "fill_halos",
    "fill_wide_halos",
    "make_color_preview_for_target",
    "neutralize_sky",
    "write_color_preview",
]

# The step's name in the job's notices.
STEP_NAME = "Color from another stack"

# The expected star width, in pixels, on the monochrome picture, on the color
# picture and on the linear color stack. It sizes the star finder's template.
# Measured on M 27: the ZWO ASI533MM picture's stars are about 3.5 pixels
# across; the stretched Nikon D5300 picture's about 6, and its linear stack's
# about 4 (STK-62). They are only the starting guess: the alignment measures
# the star width on each picture and uses that (see `other_stack_alignment`),
# because the ZWO stars of M 42 and M 45 were about 7 pixels wide, and a 3.5
# pixel template found 5 stars on M 42.
COLOR_PREVIEW_STAR_WIDTHS_PIXELS = TunedParameter(
    name="stacking.color_preview_star_widths_pixels",
    default=(3.5, 6.0, 4.0),
    unit="pixels",
    description="Expected star width on the monochrome picture, the color picture and the linear stack.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# The largest root-mean-square distance between paired stars after the move,
# in monochrome pixels. M 27 (ZWO 405 mm and Nikon 405 mm) gave 0.38 to 0.50.
# At 1 pixel a star's painted color would start to sit beside the star, so a
# worse fit is a failed alignment and the step is skipped (STK-62).
COLOR_PREVIEW_MAXIMUM_ALIGNMENT_RMS_PIXELS = TunedParameter(
    name="stacking.color_preview_maximum_alignment_rms_pixels",
    default=1.0,
    unit="pixels",
    description="Largest star-pair distance after alignment for the color picture to be made.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# The smallest share of the monochrome frame the color stack must cover. The
# uncovered part would stay gray, which reads as a fault in the picture. M 27
# was covered fully; 0.95 allows a thin gray edge (STK-62).
COLOR_PREVIEW_MINIMUM_COVERAGE = TunedParameter(
    name="stacking.color_preview_minimum_coverage",
    default=0.95,
    unit="fraction of the monochrome frame",
    description="Smallest share of the monochrome frame the color stack must cover.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# The width of the blur of the color channels, in pixels: of the extended
# light, and of the pixel-by-pixel color used for a few bright blended stars.
# 3 pixels hid the Nikon's color noise on M 27 and kept the color edges of the
# nebula; the blended stars need less, because they are bright (STK-62).
COLOR_PREVIEW_CHROMA_BLUR_PIXELS = TunedParameter(
    name="stacking.color_preview_chroma_blur_pixels",
    default=(3.0, 2.0),
    unit="pixels",
    description="Blur width of the color channels of the extended light and of blended stars.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# The color stack's signal-to-noise ratio, after a blur of the third width,
# below which the extended light is gray and above which it has full color;
# the color fades in smoothly between them. On M 27 this left 68% of the
# frame gray (the sky) and gave 15% full color (STK-62).
COLOR_PREVIEW_COLOR_FADE = TunedParameter(
    name="stacking.color_preview_color_fade",
    default=(2.0, 8.0, 6.0),
    unit="signal-to-noise ratio, signal-to-noise ratio, pixels",
    description="Signal-to-noise ratio where extended color starts and is full, and its blur width.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# The gain on the color strength of the extended light. A stretched color
# picture's nebula looks paler on the deeper monochrome lightness; 1.25 gave
# M 27 the strength of the Nikon picture by eye (STK-62).
COLOR_PREVIEW_NEBULA_CHROMA_GAIN = TunedParameter(
    name="stacking.color_preview_nebula_chroma_gain",
    default=1.25,
    unit="factor",
    description="Gain on the color strength of the extended light.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# The color is scaled by the monochrome lightness over the color picture's
# lightness, so the color keeps its strength; the ratio is held between these
# limits, so a dark color picture under a bright monochrome star does not
# give a garish color (STK-62).
COLOR_PREVIEW_LIGHTNESS_RATIO_LIMITS = TunedParameter(
    name="stacking.color_preview_lightness_ratio_limits",
    default=(0.5, 2.0),
    unit="ratio",
    description="Limits of the lightness ratio that scales the color strength.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# A star's color fades toward white in its brightest core: the color is
# multiplied by (100 - L) / 25, held between the second number and 1, where L
# is the CIE lightness (0 to 100). A burnt-out core is white on any camera,
# and a fully colored white core looks false (STK-62).
COLOR_PREVIEW_STAR_CORE_TAPER = TunedParameter(
    name="stacking.color_preview_star_core_taper",
    default=(25.0, 0.35),
    unit="lightness, fraction",
    description="Lightness range over which a star's color fades in its core, and the least color kept.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# A pixel counts as sky for the color picture's sky tint only when it is away
# from the stars and both the monochrome and the color starless pictures,
# blurred, lie within this many sky spreads of their own sky level. The sky
# level is the most common blurred value, and the spread is measured on the
# darker side of it only, where no nebula or galaxy light is. The first
# version took the darkest quarter of the frame as sky; on M 31 that quarter
# held the faint galaxy disk, whose red was taken away as a sky tint and the
# galaxy lost a third of its color. Two spreads keep almost all true sky and
# leave the disk out (STK-62).
COLOR_PREVIEW_SKY_NOISE_MULTIPLE = TunedParameter(
    name="stacking.color_preview_sky_noise_multiple",
    default=2.0,
    unit="sky spreads",
    description="How close to its sky level a pixel must be, on both pictures, to sample the color sky tint.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# The sky tint, the largest red, green or blue offset from gray in the
# picture's units (0 to 1), below which nothing is taken away and above
# which it is held. 0.002 is half a level of 255, under what can be seen. The
# Nikon D5300 pictures at 300 mm of M 101, M 13 and M 81 had tints of 0.012
# to 0.016 (3 to 4 levels); 0.03 allows twice that and stops a bad sky sample
# from tinting the whole picture (STK-62).
COLOR_PREVIEW_SKY_TINT_LIMITS = TunedParameter(
    name="stacking.color_preview_sky_tint_limits",
    default=(0.002, 0.03),
    unit="picture units (0 to 1)",
    description="Sky tint below which none is taken away, and the most that is taken away.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# The sky tint is not the same over the frame: GraXpert leaves a broad warm
# glow where the color stack's sky is brightest. So the tint is measured as a
# map: each channel's offset on the sky pixels, averaged with a Gaussian of the
# first number's width (in monochrome pixels) and divided by the share of sky
# pixels under that Gaussian (the sky coverage). Where the coverage is below
# the second number, the map is blended toward the frame-wide tint, reaching
# it at zero coverage, because the nearest sky is then far away. On M 101
# (Nikon D5300 at 300 mm, 3004 pixel frame) the tint under the arms was red
# +2.4, blue -1.5 levels of 255 against +1.0, -0.5 for the whole frame. A
# width of 150 pixels (5% of the frame) follows it: the arms' red over green
# went from +5.8 to +2.2 levels; 300 pixels left +3.1. The coverage under the
# galaxy was at least 0.20 on M 101 and 0.016 at the middle of M 81; under the
# disk of M 31 it is zero. The map's red minus green, against the frame-wide
# value, scattered by 0.31 levels (M 31) and 1.06 (M 81, where the warm glow
# is) at a coverage of 0.05 to 0.20, by 0.55 and 0.79 at 0.01 to 0.05, and by
# 1.14 on M 31 below 0.01, where the map rests on sky far away. Below 0.05 the
# map is blended toward the frame-wide tint, with a fifth of its own weight
# left at 0.01 (STK-62).
COLOR_PREVIEW_SKY_TINT_SMOOTHING = TunedParameter(
    name="stacking.color_preview_sky_tint_smoothing",
    default=(150.0, 0.05),
    unit="pixels, share of sky pixels",
    description="Smoothing width of the sky tint map; sky share below which it nears the frame's tint.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# The fewest sky pixels needed to measure the sky tint, as a share of the
# covered frame; with fewer, the tint is left alone. Not tuned.
_MINIMUM_SKY_SHARE = 0.005

# How far past each monochrome star's footprint the extended color is
# replaced by the color just beyond, in widths of the color stack's widest
# star (red or green). The color previews of M 101, M 13 and M 81 (Nikon
# D5300, 300 mm, red stars about 40% wider than green) showed red skirts out
# to about 10 to 20 pixels from a star, which two widths reach (STK-62).
COLOR_PREVIEW_HALO_WIDTHS = TunedParameter(
    name="stacking.color_preview_halo_widths",
    default=2.0,
    unit="color-stack star widths",
    description="How far past a star's footprint its color skirt is replaced, in color-stack star widths.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# How far past the footprint of a saturated star whose wing color is not
# trusted (the color stack's red stars are too wide, see
# `star_color_measurement.STAR_RED_WIDE_TOLERANCE`) the extended color is
# replaced, in radii of that footprint (the radius of a circle of the same
# area). On M 45 (Nikon D5300 at 300 mm, red stars 22% wider than green) the
# red-magenta skirt that StarNet left around Electra reached about 80 to 100
# pixels from the star, whose footprint radius is 33 pixels, and around Maia
# about 50 pixels (radius 27). Two radii past the footprint reach beyond both
# (STK-62).
COLOR_PREVIEW_SATURATED_HALO_RADII = TunedParameter(
    name="stacking.color_preview_saturated_halo_radii",
    default=2.0,
    unit="footprint radii",
    description="How far past an untrusted saturated star's footprint its color skirt is replaced.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-62",
)

# The wide halos of saturated stars are filled on a grid coarser by the
# halo radius over this many pixels, because a blur that wide is slow at full
# size and the filled color is smooth anyway. Plumbing, not tuned.
_WIDE_HALO_SAMPLING_PIXELS = 8.0

# The halo radius used when the color stack's star width could not be
# measured, in pixels: the 8 pixel circle of the photometry fallback.
# Not tuned.
_FALLBACK_HALO_PIXELS = 8.0

# Where fewer than this share of the pixels around a halo lie outside every
# halo (the core of a dense cluster), the halo is gray. Not tuned.
_HALO_FILL_MINIMUM_WEIGHT = 0.05

# The most stars found on the monochrome picture for the alignment, and on
# each color image. Enough for a quadratic fit in every quarter; more only
# costs time. Not tuned.
_REFERENCE_STAR_COUNT = 3000
_MOVING_STAR_COUNT = 2000

# How far past the monochrome frame the Gaia question reaches, in degrees.
# Not tuned.
_GAIA_MARGIN_DEGREES = 0.02

# The most rows one stored SIMBAD tile may hold. It is the star identifier's
# limit, so both read the same stored answers. Not tuned.
_SIMBAD_ROW_LIMIT = 5000

# The lightness offset that keeps the lightness ratio finite on black
# pixels, in CIE lightness units (0 to 100). Not tuned.
_LIGHTNESS_OFFSET = 2.0

# The Siril version that has the ``starnet`` command. Not tuned.
_MINIMUM_SIRIL_VERSION = "1.2.0"


@dataclass(frozen=True)
class ColorStackChoice:
    """The color stack chosen to color a monochrome stack.

    Attributes
    ----------
    stacked_path : `str`
        The color stack.
    picture_path : `str`
        Its finished (stretched) picture.
    configuration_key : `str`
        The setup it came from, such as ``"Nikon D5300@405mm"``.
    camera : `str`
        Its camera.
    focal_length_mm : `float` or `None`
        Its focal length.
    """

    stacked_path: str
    picture_path: str
    configuration_key: str
    camera: str
    focal_length_mm: float | None

    def record(self) -> dict[str, Any]:
        """Give the choice as plain values for the JSON record.

        Returns
        -------
        record : `dict`
            The paths, the setup, the camera and the focal length.
        """
        return {
            "stack": self.stacked_path,
            "picture": self.picture_path,
            "configuration": self.configuration_key,
            "camera": self.camera,
            "focal_length_mm": self.focal_length_mm,
        }


@dataclass
class ColorPreviewOutcome:
    """What the color step did.

    Attributes
    ----------
    made : `bool`
        Whether the color picture was written.
    reason : `str`
        Why it was not made, or an empty string.
    failed : `bool`
        `True` when something that should have worked did not (a warning);
        `False` for a normal skip, such as no color stack.
    path : `str` or `None`
        The color picture, when made.
    record : `dict` [`str`, `Any`]
        What was measured, as written to the JSON record.
    """

    made: bool
    reason: str = ""
    failed: bool = False
    path: str | None = None
    record: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        """Say in one sentence what the step did.

        Returns
        -------
        sentence : `str`
            The alignment, coverage and star counts when made, or the reason.
        """
        if not self.made:
            return f"No color picture: {self.reason}"
        alignment = self.record.get("alignment", {}).get("picture", {})
        counts = self.record.get("star_counts", {})
        color = self.record.get("color_stack", {})
        return (
            f"Color picture made with color from {color.get('configuration', 'another stack')}: "
            f"alignment {alignment.get('residual_pixels', float('nan')):.2f} px, "
            f"coverage {100 * self.record.get('coverage_fraction', 0.0):.0f}%, "
            f"{counts.get('measured', 0) + counts.get('measured_wing', 0)} measured star colors, "
            f"{counts.get('gaia', 0)} from Gaia"
        )


def _focal_length_from_header(path: str) -> float | None:
    """Read a stack's focal length from its header.

    Parameters
    ----------
    path : `str`
        The stack.

    Returns
    -------
    focal_length_mm : `float` or `None`
        The ``FOCALLEN`` value, or `None` when there is none.
    """
    try:
        value = read_header(path).get("FOCALLEN")
        return float(value) if value else None
    except OSError, ValueError, TypeError:
        return None


def choose_color_stack(stacking: Any, mono_stacked_path: str) -> tuple[ColorStackChoice | None, str]:
    """Choose the color stack that colors a monochrome stack.

    Parameters
    ----------
    stacking : `TargetStackingResult`
        The target's imaging stacks (``stacks_by_configuration``).
    mono_stacked_path : `str`
        The monochrome stack.

    Returns
    -------
    choice : `ColorStackChoice` or `None`
        The color stack with a finished picture at the same focal length,
        else the nearest focal length (more frames first on a tie), or
        `None`.
    reason : `str`
        Why none was chosen, or an empty string.
    """
    configurations = dict(getattr(stacking, "stacks_by_configuration", None) or {})
    mono_focal_length = next(
        (
            entry.focal_length_mm
            for entry in configurations.values()
            if entry.stacked_image == mono_stacked_path and entry.focal_length_mm
        ),
        None,
    ) or _focal_length_from_header(mono_stacked_path)
    candidates = []
    without_picture = []
    for key, entry in configurations.items():
        path = entry.stacked_image
        if not path or path == mono_stacked_path or not os.path.isfile(path) or not is_colour_image(path):
            continue
        picture = processed_fits_path_for(path)
        if not os.path.isfile(picture):
            without_picture.append(key)
            continue
        focal_length = entry.focal_length_mm or _focal_length_from_header(path)
        distance = (
            abs(float(focal_length) - float(mono_focal_length))
            if focal_length and mono_focal_length
            else float("inf")
        )
        candidates.append((
            distance,
            -int(entry.frames_stacked or 0),
            ColorStackChoice(path, picture, key, entry.camera or "", focal_length),
        ))
    if not candidates:
        if without_picture:
            return None, "the color stack has no finished picture yet (" + ", ".join(
                sorted(without_picture)
            ) + ")"
        return None, "the target has no color stack from another setup"
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2], ""


def _lightness(gray: np.ndarray) -> np.ndarray:
    """Give the CIE lightness of a gray picture.

    Parameters
    ----------
    gray : `numpy.ndarray`
        Display values from 0 to 1.

    Returns
    -------
    lightness : `numpy.ndarray`
        CIE L, from 0 to 100, as 32-bit floats.
    """
    gray = np.clip(np.asarray(gray, dtype=np.float32), 0.0, 1.0)
    return rgb2lab(np.repeat(gray[..., np.newaxis], 3, axis=-1))[..., 0].astype(np.float32)


def _to_lab(channels_first: np.ndarray) -> np.ndarray:
    """Turn a color picture into CIE Lab.

    Parameters
    ----------
    channels_first : `numpy.ndarray`
        Display values from 0 to 1, shape (3, rows, columns).

    Returns
    -------
    lab : `numpy.ndarray`
        Shape (rows, columns, 3), 32-bit floats.
    """
    return rgb2lab(np.clip(np.moveaxis(np.asarray(channels_first), 0, -1), 0.0, 1.0)).astype(np.float32)


def neutralize_sky(color_starless: np.ndarray, sky: np.ndarray) -> tuple[np.ndarray, list[float], float]:
    """Take the sky tint out of a color picture, under its objects too.

    The tint of a pixel is each channel's offset from the mean of the three.
    It is measured on the sky pixels and smoothed into a map (see
    `COLOR_PREVIEW_SKY_TINT_SMOOTHING`), so a tint that changes across the
    frame is followed. Where little sky lies near (inside a large galaxy),
    the map is blended toward the frame-wide tint: each channel's median over
    the sky pixels, less the mean of the three medians. The map is taken away
    in full everywhere, because a sky tint near a nebula or galaxy is also in
    its light; the object keeps the part of its color that differs from the
    sky around it. The sky's brightness is kept; only its tint goes. Where
    the tint is below the first of `COLOR_PREVIEW_SKY_TINT_LIMITS`, nothing
    is taken away, and a tint above the second is held to it.

    Parameters
    ----------
    color_starless : `numpy.ndarray`
        The color picture, shape (3, rows, columns), values 0 to 1.
    sky : `numpy.ndarray`
        Where the picture shows sky.

    Returns
    -------
    picture : `numpy.ndarray`
        The picture with a gray sky, clipped to 0 to 1.
    offsets : `list` [`float`]
        The frame-wide tint of the red, green and blue channels (zero when
        there are too few sky pixels or it is below the floor).
    largest : `float`
        The largest offset taken from any channel at any pixel.
    """
    picture = np.asarray(color_starless, dtype=np.float32)
    if sky.sum() < _MINIMUM_SKY_SHARE * sky.size:
        return picture, [0.0, 0.0, 0.0], 0.0
    levels = np.array([float(np.median(picture[channel][sky])) for channel in range(3)])
    offsets = levels - levels.mean()
    floor, cap = (float(value) for value in resolve(COLOR_PREVIEW_SKY_TINT_LIMITS).value)
    width, least_coverage = (float(value) for value in resolve(COLOR_PREVIEW_SKY_TINT_SMOOTHING).value)
    coverage = _blur_on_coarse_grid(sky.astype(np.float32), width)
    blend = np.clip(coverage / least_coverage, 0.0, 1.0)
    gray = picture.mean(axis=0)
    tint = np.empty_like(picture)
    for channel in range(3):
        local = _blur_on_coarse_grid(np.where(sky, picture[channel] - gray, 0.0), width) / np.maximum(
            coverage, 1e-6
        )
        tint[channel] = blend * local + (1.0 - blend) * float(offsets[channel])
    del gray, coverage, blend
    size = np.max(np.abs(tint), axis=0)
    # A pixel whose tint is below the floor keeps its color; a larger tint
    # is held to the cap.
    tint *= np.where(size < floor, 0.0, np.minimum(1.0, cap / np.maximum(size, 1e-12)))[None, :, :]
    largest = min(float(np.max(size)), cap)
    if largest < floor:
        return picture, [0.0, 0.0, 0.0], 0.0
    picture = np.clip(picture - tint, 0.0, 1.0)
    if float(np.max(np.abs(offsets))) < floor:
        offsets = np.zeros(3)
    return picture, [float(value) for value in offsets], largest


def fill_halos(chroma: np.ndarray, halos: np.ndarray, radius_px: float) -> np.ndarray:
    """Replace the color inside the star halos with the color just beyond.

    The color outside the halos is spread inward by a Gaussian average that
    counts only pixels outside every halo (a normalized blur), so each halo
    takes the mean color of the light around it.

    Parameters
    ----------
    chroma : `numpy.ndarray`
        One CIE Lab color channel (``a`` or ``b``).
    halos : `numpy.ndarray`
        Where the color is replaced.
    radius_px : `float`
        The halo radius; the width of the Gaussian average.

    Returns
    -------
    chroma : `numpy.ndarray`
        The channel with the halos filled; gray (zero) where almost nothing
        around a halo lies outside every halo.
    """
    if not halos.any():
        return chroma
    outside = (~halos).astype(np.float32)
    weight = ndimage.gaussian_filter(outside, radius_px)
    spread = ndimage.gaussian_filter(chroma * outside, radius_px)
    filled = np.where(weight > _HALO_FILL_MINIMUM_WEIGHT, spread / np.maximum(weight, 1e-6), 0.0)
    return np.where(halos, filled, chroma).astype(np.float32)


def _blur_on_coarse_grid(image: np.ndarray, sigma_px: float) -> np.ndarray:
    """Blur a picture with a wide Gaussian, working on a coarser grid.

    The picture is averaged in square blocks, blurred, and spread back to
    full size by linear interpolation. For a blur many blocks wide this
    gives nearly the same result as a full-size blur, much faster.

    Parameters
    ----------
    image : `numpy.ndarray`
        The picture.
    sigma_px : `float`
        The width of the Gaussian, in full-size pixels.

    Returns
    -------
    blurred : `numpy.ndarray`
        The blurred picture, the same shape, as 32-bit floats.
    """
    block = max(1, int(sigma_px // _WIDE_HALO_SAMPLING_PIXELS))
    rows, columns = image.shape
    padded_rows, padded_columns = -(-rows // block) * block, -(-columns // block) * block
    padded = np.pad(
        np.asarray(image, dtype=np.float32),
        ((0, padded_rows - rows), (0, padded_columns - columns)),
        mode="edge",
    )
    coarse = padded.reshape(padded_rows // block, block, padded_columns // block, block).mean(axis=(1, 3))
    coarse = ndimage.gaussian_filter(coarse, sigma_px / block)
    if block == 1:
        return coarse[:rows, :columns].astype(np.float32)
    # Block centers sit half a block in; the positions are shifted to match.
    row_positions = (np.arange(rows) + 0.5) / block - 0.5
    column_positions = (np.arange(columns) + 0.5) / block - 0.5
    grid_rows, grid_columns = np.meshgrid(row_positions, column_positions, indexing="ij")
    return ndimage.map_coordinates(coarse, [grid_rows, grid_columns], order=1, mode="nearest").astype(
        np.float32
    )


def fill_wide_halos(
    chroma: np.ndarray, wide: np.ndarray, excluded: np.ndarray, radius_px: float
) -> np.ndarray:
    """Replace the color inside the wide halos of saturated stars.

    It works as `fill_halos`, with a blur as wide as the widest halo, so the
    color reaches into the middle of a large halo from the light around it.

    Parameters
    ----------
    chroma : `numpy.ndarray`
        One CIE Lab color channel (``a`` or ``b``).
    wide : `numpy.ndarray`
        Where the color is replaced.
    excluded : `numpy.ndarray`
        The pixels whose color is not used (every halo, wide or not).
    radius_px : `float`
        The width of the Gaussian average.

    Returns
    -------
    chroma : `numpy.ndarray`
        The channel with the wide halos filled; gray (zero) where almost
        nothing around a halo lies outside every halo.
    """
    if not wide.any():
        return chroma
    outside = (~excluded).astype(np.float32)
    weight = _blur_on_coarse_grid(outside, radius_px)
    spread = _blur_on_coarse_grid(chroma * outside, radius_px)
    filled = np.where(weight > _HALO_FILL_MINIMUM_WEIGHT, spread / np.maximum(weight, 1e-6), 0.0)
    return np.where(wide, filled, chroma).astype(np.float32)


def _wide_halos(star_labels: np.ndarray, reach_px: np.ndarray) -> np.ndarray:
    """Mark the wide halos around the stars that have one.

    Parameters
    ----------
    star_labels : `numpy.ndarray`
        The monochrome star footprints (region number of each pixel).
    reach_px : `numpy.ndarray`
        For each region number, how far past its footprint its halo
        reaches, in pixels; zero for no wide halo.

    Returns
    -------
    wide : `numpy.ndarray`
        Whether each pixel lies within the reach of the nearest footprint
        that has a wide halo.
    """
    seeds = reach_px[star_labels] > 0
    if not seeds.any():
        return np.zeros(star_labels.shape, dtype=bool)
    distance, (nearest_rows, nearest_columns) = ndimage.distance_transform_edt(~seeds, return_indices=True)
    return distance <= reach_px[star_labels[nearest_rows, nearest_columns]]


def extended_color(
    mono_starless: np.ndarray,
    color_starless: np.ndarray,
    covered: np.ndarray,
    star_labels: np.ndarray | None = None,
    halo_radius_px: float = 0.0,
    wide_halo_reach_px: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Find the color of the extended light on the monochrome grid.

    Parameters
    ----------
    mono_starless : `numpy.ndarray`
        The monochrome picture without stars, values 0 to 1.
    color_starless : `numpy.ndarray`
        The color picture without stars, on the monochrome grid, shape
        (3, rows, columns).
    covered : `numpy.ndarray`
        Where the moved color picture holds real data.
    star_labels : `numpy.ndarray`, optional
        The monochrome star footprints (nonzero inside a star). With them,
        the sky is taken away from the stars and the star halos are filled
        (see `fill_halos`).
    halo_radius_px : `float`, optional
        How far past each footprint the halo reaches, in pixels. Zero leaves
        the halos alone.
    wide_halo_reach_px : `numpy.ndarray`, optional
        For each region number of ``star_labels``, how far past its
        footprint a wider halo reaches, in pixels (zero for none). It is
        given to the saturated stars whose wing color is not trusted (see
        `fill_wide_halos`).

    Returns
    -------
    chroma_a, chroma_b : `numpy.ndarray`
        The CIE Lab ``a`` and ``b`` of each pixel: zero (gray) where the
        color stack's signal is weak or where it does not cover.
    statistics : `dict` [`str`, `Any`]
        The color stack's sky level and pixel noise, the frame-wide sky
        tint and the largest local tint taken away, the halo radius, the
        number of stars with a wide halo and the widest one, and the shares
        of the frame with full color and with none.
    """
    blur, _ = (float(value) for value in resolve(COLOR_PREVIEW_CHROMA_BLUR_PIXELS).value)
    fade_low, fade_high, fade_blur = (float(value) for value in resolve(COLOR_PREVIEW_COLOR_FADE).value)
    ratio_low, ratio_high = (float(value) for value in resolve(COLOR_PREVIEW_LIGHTNESS_RATIO_LIMITS).value)
    gain = float(resolve(COLOR_PREVIEW_NEBULA_CHROMA_GAIN).value)
    if star_labels is not None and np.any(star_labels):
        distance = ndimage.distance_transform_edt(star_labels == 0)
        halos = distance <= halo_radius_px
        away_from_stars = covered & (distance > max(halo_radius_px, 1.0))
        del distance
    else:
        halos = np.zeros(covered.shape, dtype=bool)
        away_from_stars = covered
    wide = None
    wide_radius = 0.0
    if star_labels is not None and wide_halo_reach_px is not None and np.any(wide_halo_reach_px > 0):
        wide = _wide_halos(star_labels, np.asarray(wide_halo_reach_px, dtype=np.float32))
        wide_radius = float(np.max(wide_halo_reach_px))
        away_from_stars = away_from_stars & ~wide
    # The sky sample: away from the stars, with both the monochrome and the
    # color starless pictures near their own sky level.
    sky = np.zeros(covered.shape, dtype=bool)
    if away_from_stars.any():
        multiple = float(resolve(COLOR_PREVIEW_SKY_NOISE_MULTIPLE).value)
        mono_blurred = ndimage.gaussian_filter(np.asarray(mono_starless, dtype=np.float32), fade_blur)
        color_blurred = ndimage.gaussian_filter(
            np.asarray(color_starless, dtype=np.float32).mean(axis=0), fade_blur
        )
        sky = (
            away_from_stars
            & (sky_weight(mono_blurred, away_from_stars, multiple) >= 1.0)
            & (sky_weight(color_blurred, away_from_stars, multiple) >= 1.0)
        )
        del mono_blurred, color_blurred
    color_starless, sky_offsets, largest_tint = neutralize_sky(color_starless, sky)
    lab = _to_lab(color_starless)
    chroma_a = ndimage.gaussian_filter(lab[..., 1], blur)
    chroma_b = ndimage.gaussian_filter(lab[..., 2], blur)
    color_lightness = ndimage.gaussian_filter(lab[..., 0], blur)
    del lab
    if halo_radius_px > 0.0:
        chroma_a = fill_halos(chroma_a, halos, halo_radius_px)
        chroma_b = fill_halos(chroma_b, halos, halo_radius_px)
    if wide is not None:
        chroma_a = fill_wide_halos(chroma_a, wide, halos | wide, wide_radius)
        chroma_b = fill_wide_halos(chroma_b, wide, halos | wide, wide_radius)
    mono_lightness = ndimage.gaussian_filter(_lightness(mono_starless), blur)
    ratio = np.clip(
        (mono_lightness + _LIGHTNESS_OFFSET) / (color_lightness + _LIGHTNESS_OFFSET), ratio_low, ratio_high
    )
    gray = color_starless.mean(axis=0)
    sky_level = float(np.median(gray[covered])) if covered.any() else 0.0
    pixel_noise = float(robust_sigma((gray - ndimage.median_filter(gray, 5))[::3, ::3].ravel()))
    # The noise of a Gaussian-weighted mean of independent pixels.
    smooth_noise = max(pixel_noise / (2.0 * np.sqrt(np.pi) * fade_blur), 1e-12)
    signal_to_noise = (ndimage.gaussian_filter(gray, fade_blur) - sky_level) / smooth_noise
    fade = np.clip((signal_to_noise - fade_low) / (fade_high - fade_low), 0.0, 1.0)
    fade = fade * fade * (3.0 - 2.0 * fade)
    fade[~covered] = 0.0
    scale = ratio * fade * gain
    statistics = {
        "color_starless_sky": round(sky_level, 5),
        "color_pixel_noise": round(pixel_noise, 5),
        "sky_tint_removed_rgb": [round(value, 5) for value in sky_offsets],
        "sky_tint_largest_removed": round(largest_tint, 5),
        "sky_sample_fraction": round(float(np.mean(sky)), 4),
        "halo_radius_pixels": round(float(halo_radius_px), 2),
        "wide_halo_stars": int(np.count_nonzero(wide_halo_reach_px)) if wide is not None else 0,
        "wide_halo_largest_pixels": round(wide_radius, 1),
        "fraction_full_color": round(float(np.mean(fade > 0.99)), 4),
        "fraction_no_color": round(float(np.mean(fade < 0.01)), 4),
    }
    return (chroma_a * scale).astype(np.float32), (chroma_b * scale).astype(np.float32), statistics


def _pixel_color(luminance: np.ndarray, color_picture: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Give the smoothed color of the color picture, pixel by pixel.

    It is used only for bright stars in a blend, whose own color cannot be
    measured.

    Parameters
    ----------
    luminance : `numpy.ndarray`
        The monochrome picture.
    color_picture : `numpy.ndarray`
        The color picture with stars, on the monochrome grid.

    Returns
    -------
    chroma_a, chroma_b : `numpy.ndarray`
        The CIE Lab ``a`` and ``b``, scaled to the monochrome lightness.
    """
    _, blur = (float(value) for value in resolve(COLOR_PREVIEW_CHROMA_BLUR_PIXELS).value)
    ratio_low, ratio_high = (float(value) for value in resolve(COLOR_PREVIEW_LIGHTNESS_RATIO_LIMITS).value)
    lab = _to_lab(color_picture)
    ratio = np.clip(
        (ndimage.gaussian_filter(_lightness(luminance), blur) + _LIGHTNESS_OFFSET)
        / (ndimage.gaussian_filter(lab[..., 0], blur) + _LIGHTNESS_OFFSET),
        ratio_low,
        ratio_high,
    )
    return (
        (ndimage.gaussian_filter(lab[..., 1], blur) * ratio).astype(np.float32),
        (ndimage.gaussian_filter(lab[..., 2], blur) * ratio).astype(np.float32),
    )


def compose_color_picture(
    luminance: np.ndarray,
    footprints: StarFootprints,
    star_colors: StarColors,
    extended_a: np.ndarray,
    extended_b: np.ndarray,
    pixel_a: np.ndarray | None = None,
    pixel_b: np.ndarray | None = None,
) -> np.ndarray:
    """Join the monochrome lightness with the star and extended colors.

    Inside a star's footprint the star's color is weighted by the share of
    the pixel's light that is starlight; outside every footprint the pixel
    takes the extended color.

    Parameters
    ----------
    luminance : `numpy.ndarray`
        The monochrome picture, values 0 to 1.
    footprints : `StarFootprints`
        The monochrome stars.
    star_colors : `StarColors`
        The color of each star.
    extended_a, extended_b : `numpy.ndarray`
        The color of the extended light (see `extended_color`).
    pixel_a, pixel_b : `numpy.ndarray`, optional
        The pixel-by-pixel color for bright blended stars. Without it they
        take the extended color.

    Returns
    -------
    picture : `numpy.ndarray`
        Red, green and blue display values from 0 to 1, shape
        (rows, columns, 3), as 32-bit floats.
    """
    taper_range, taper_floor = (float(value) for value in resolve(COLOR_PREVIEW_STAR_CORE_TAPER).value)
    lightness = _lightness(luminance)
    labels = footprints.labels
    fraction = np.clip(
        footprints.starlight / np.maximum(np.asarray(luminance, dtype=np.float32), 1e-3), 0.0, 1.0
    )
    pixel_source = star_colors.sources[labels]
    pixel_code = SOURCE_NAMES.index("pixel_fallback")
    in_star = (labels > 0) & (pixel_source != pixel_code) & (pixel_source != 0)
    taper = np.clip((100.0 - lightness) / taper_range, taper_floor, 1.0)
    out_a = extended_a.copy()
    out_b = extended_b.copy()
    star_a = star_colors.chroma_a[labels] * lightness * taper
    star_b = star_colors.chroma_b[labels] * lightness * taper
    out_a[in_star] = (fraction * star_a + (1.0 - fraction) * extended_a)[in_star]
    out_b[in_star] = (fraction * star_b + (1.0 - fraction) * extended_b)[in_star]
    if pixel_a is not None and pixel_b is not None:
        blended = (labels > 0) & (pixel_source == pixel_code)
        out_a[blended] = (fraction * pixel_a + (1.0 - fraction) * extended_a)[blended]
        out_b[blended] = (fraction * pixel_b + (1.0 - fraction) * extended_b)[blended]
    picture = lab2rgb(np.stack([lightness, out_a, out_b], axis=-1))
    return np.clip(picture, 0.0, 1.0).astype(np.float32)


def _celestial_wcs(header: Any) -> WCS | None:
    """Read the celestial part of a plate solution.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        A FITS header.

    Returns
    -------
    wcs : `astropy.wcs.WCS` or `None`
        The two sky axes, or `None` when the header has no solution.
    """
    if "CTYPE1" not in header or "CRVAL1" not in header:
        return None
    try:
        return WCS(header, naxis=2)
    except ValueError, KeyError, MemoryError:
        return None


def _run_starnet(scratch: str, name: str, data: np.ndarray, siril_executable: str) -> np.ndarray | None:
    """Take the stars out of a stretched picture with StarNet.

    The picture is already stretched, so StarNet runs without its own
    stretch (see `starnet_stretch` for the linear case).

    Parameters
    ----------
    scratch : `str`
        A scratch folder; a sub-folder is made for this picture.
    name : `str`
        A short name for the sub-folder.
    data : `numpy.ndarray`
        The picture, 2-D or channels first.
    siril_executable : `str`
        The command that starts Siril.

    Returns
    -------
    starless : `numpy.ndarray` or `None`
        The picture without stars, the same shape, or `None` when StarNet
        did not run.
    """
    folder = os.path.join(scratch, name)
    os.makedirs(folder, exist_ok=True)
    write_image(os.path.join(folder, "picture.fits"), np.asarray(data, dtype=np.float32))
    commands = [f"requires {_MINIMUM_SIRIL_VERSION}", 'load "picture.fits"', "starnet", "close"]
    if not run_preview_script(folder, commands, siril_executable) or not collect_starnet_outputs(folder):
        return None
    starless = read_data(os.path.join(folder, STARLESS_NAME))
    if starless is None or starless.shape != np.shape(data):
        return None
    return np.asarray(starless, dtype=np.float32)


def _gaia_colors(wcs: WCS, shape: tuple[int, int]) -> tuple[Any, str]:
    """Get the Gaia colors of the stars under the monochrome frame.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The monochrome picture's plate solution.
    shape : `tuple` [`int`, `int`]
        The picture's rows and columns.

    Returns
    -------
    stars : `astropy.table.Table` or `None`
        The Gaia stars with their colors, or `None`.
    note : `str`
        Why there are none, or an empty string.
    """
    rows, columns = shape
    ra, dec = wcs.all_pix2world(
        [-0.5, columns - 0.5, columns - 0.5, -0.5], [-0.5, -0.5, rows - 0.5, rows - 0.5], 0
    )
    corners = [(float(a), float(b)) for a, b in zip(ra, dec, strict=True)]
    try:
        stars = gaia_star_colors_in_footprint(corners, _GAIA_MARGIN_DEGREES)
    except ExternalServiceError as error:
        return None, f" (the Gaia colors could not be had: {error})"
    return stars, "" if stars is not None else " (Gaia has no stars here)"


def _first_column(table: Table, names: tuple[str, ...]) -> np.ndarray | None:
    """Read the first of several possible columns of a SIMBAD table as numbers.

    Parameters
    ----------
    table : `astropy.table.Table`
        The table.
    names : `tuple` [`str`]
        The names the column has had in different astroquery versions.

    Returns
    -------
    values : `numpy.ndarray` or `None`
        The values, NaN where blank, or `None` when no such column exists.
    """
    name = next((name for name in names if name in table.colnames), None)
    if name is None:
        return None
    return np.ma.filled(np.ma.asarray(table[name], dtype=np.float64), np.nan)


def _bright_star_colors(wcs: WCS, shape: tuple[int, int]) -> tuple[Table | None, str]:
    """Get the SIMBAD ``B - V`` colors of the stars under the monochrome frame.

    The Gaia color table holds no star brighter than G = 10.5, so the
    brightest stars of a field, which saturate, are looked up in SIMBAD.
    Only stars brighter than V = 10.5 are kept. The
    question is the one the star identifier asks, so a field it has seen is
    read from the stored answers with no network call.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The monochrome picture's plate solution.
    shape : `tuple` [`int`, `int`]
        The picture's rows and columns.

    Returns
    -------
    stars : `astropy.table.Table` or `None`
        The stars with ``ra``, ``dec``, ``v`` and ``b_minus_v``, or `None`.
    note : `str`
        Why there are none, or an empty string.
    """
    rows, columns = shape
    center_ra, center_dec = wcs.all_pix2world([(columns - 1) / 2.0], [(rows - 1) / 2.0], 0)
    corner_ra, corner_dec = wcs.all_pix2world([-0.5], [-0.5], 0)
    center = SkyCoord(float(center_ra[0]) * u.deg, float(center_dec[0]) * u.deg)
    radius = float(center.separation(SkyCoord(float(corner_ra[0]) * u.deg, float(corner_dec[0]) * u.deg)).deg)
    try:
        table = get_simbad_driver().query_region(
            center,
            f"{radius + _GAIA_MARGIN_DEGREES:.4f}d",
            votable_fields=STAR_REGION_VOTABLE_FIELDS,
            row_limit=_SIMBAD_ROW_LIMIT,
        )
    except (ExternalServiceError, ValueError, OSError) as error:
        return None, f"The catalog colors of the bright stars could not be had: {error}."
    if table is None or len(table) == 0:
        return None, "SIMBAD lists no stars here, so the saturated stars were left white."
    ra = _first_column(table, ("ra", "RA_d", "ra_d"))
    dec = _first_column(table, ("dec", "DEC_d", "dec_d"))
    blue = _first_column(table, ("B", "FLUX_B", "flux_b"))
    visual = _first_column(table, ("V", "FLUX_V", "flux_v"))
    if ra is None or dec is None or blue is None or visual is None:
        return None, "The SIMBAD answer has no B and V magnitudes, so the saturated stars were left white."
    # Only stars brighter than the Gaia color table's limit are kept. A
    # fainter catalog star inside the large footprint of a bright star (a
    # red field star in its glow) would otherwise color the whole glow; a
    # faint star is paired with its Gaia color by position instead.
    usable = (
        np.isfinite(ra)
        & np.isfinite(dec)
        & np.isfinite(blue)
        & np.isfinite(visual)
        & (visual < BRIGHTEST_G_MAGNITUDE)
    )
    stars = Table({
        "ra": ra[usable],
        "dec": dec[usable],
        "v": visual[usable],
        "b_minus_v": (blue - visual)[usable],
    })
    return stars, ""


def _skip(reason: str, record: dict[str, Any]) -> ColorPreviewOutcome:
    """Give the outcome of a normal skip.

    Returns
    -------
    outcome : `ColorPreviewOutcome`
        Not made, not failed, with the reason.
    """
    return ColorPreviewOutcome(made=False, reason=reason, record=record)


def write_color_preview(
    mono_stacked_path: str, choice: ColorStackChoice, siril_executable: str
) -> ColorPreviewOutcome:
    """Make the color picture of a monochrome stack and save it beside it.

    Parameters
    ----------
    mono_stacked_path : `str`
        The monochrome stack. Its finished picture must exist.
    choice : `ColorStackChoice`
        The color stack (see `choose_color_stack`).
    siril_executable : `str`
        The command that starts Siril, for StarNet.

    Returns
    -------
    outcome : `ColorPreviewOutcome`
        Whether the picture was made, and what was measured.
    """
    started = time.monotonic()
    record: dict[str, Any] = {"mono_stack": mono_stacked_path, "color_stack": choice.record(), "notes": []}
    mono_picture_path = processed_fits_path_for(mono_stacked_path)
    if not os.path.isfile(mono_picture_path):
        return _skip("the monochrome stack has no finished picture", record)
    for path, label in ((mono_picture_path, "monochrome picture"), (choice.picture_path, "color picture")):
        if not has_plate_solution(path):
            return _skip(f"the {label} has no plate solution", record)
    if not has_plate_solution(choice.stacked_path):
        return _skip("the color stack has no plate solution", record)
    mono_width, color_picture_width, color_linear_width = (
        float(value) for value in resolve(COLOR_PREVIEW_STAR_WIDTHS_PIXELS).value
    )
    try:
        luminance = read_data(mono_picture_path)
        mono_wcs = _celestial_wcs(read_header(mono_picture_path))
        color_picture = read_data(choice.picture_path)
        color_wcs = _celestial_wcs(read_header(choice.picture_path))
    except (OSError, ValueError) as error:
        return ColorPreviewOutcome(False, f"a picture could not be read ({error})", True, record=record)
    if luminance is None or color_picture is None or mono_wcs is None or color_wcs is None:
        return _skip("a picture has no image data or no usable plate solution", record)
    luminance = np.nan_to_num(np.asarray(luminance, dtype=np.float32))
    color_picture = np.nan_to_num(np.asarray(color_picture, dtype=np.float32))
    if luminance.ndim != 2 or color_picture.ndim != 3 or color_picture.shape[0] != 3:
        return _skip("the pictures are not one monochrome and one color picture", record)
    shape = luminance.shape

    reference_stars = find_bright_stars(luminance, _REFERENCE_STAR_COUNT, mono_width)
    record["mono_stars_for_alignment"] = len(reference_stars)
    picture_fit = align_with_stars(
        reference_stars, mono_wcs, color_picture[1], color_wcs, color_picture_width, _MOVING_STAR_COUNT
    )
    limit = float(resolve(COLOR_PREVIEW_MAXIMUM_ALIGNMENT_RMS_PIXELS).value)
    if picture_fit is None:
        return _skip("too few stars could be paired to align the color picture", record)
    record["alignment"] = {"picture": picture_fit.record()}
    if picture_fit.residual_pixels > limit:
        return _skip(
            f"the color picture aligns to {picture_fit.residual_pixels:.2f} px, worse than {limit:.2f} px",
            record,
        )
    warped_picture, covered = warp(color_picture, picture_fit.transform, order=3, output_shape=shape)
    coverage = float(covered.mean())
    record["coverage_fraction"] = round(coverage, 4)
    minimum_coverage = float(resolve(COLOR_PREVIEW_MINIMUM_COVERAGE).value)
    if coverage < minimum_coverage:
        return _skip(
            f"the color stack covers {100 * coverage:.0f}% of the monochrome frame, "
            f"less than {100 * minimum_coverage:.0f}%",
            record,
        )
    warped_picture = np.clip(warped_picture, 0.0, 1.0)
    del color_picture

    try:
        linear = read_data(choice.stacked_path)
        linear_wcs = _celestial_wcs(read_header(choice.stacked_path))
    except (OSError, ValueError) as error:
        return ColorPreviewOutcome(False, f"the color stack could not be read ({error})", True, record=record)
    if linear is None or linear_wcs is None or np.ndim(linear) != 3:
        return _skip("the color stack has no color data or no usable plate solution", record)
    linear = np.nan_to_num(np.asarray(linear, dtype=np.float32))
    linear_fit: AlignmentFit | None = align_with_stars(
        reference_stars, mono_wcs, linear[1], linear_wcs, color_linear_width, _MOVING_STAR_COUNT
    )
    if linear_fit is None:
        return _skip("too few stars could be paired to align the color stack", record)
    record["alignment"]["linear"] = linear_fit.record()
    if linear_fit.residual_pixels > limit:
        return _skip(
            f"the color stack aligns to {linear_fit.residual_pixels:.2f} px, worse than {limit:.2f} px",
            record,
        )
    # Bilinear, so a star's summed light is not changed by overshoot.
    linear_on_grid, _ = warp(linear, linear_fit.transform, order=1, output_shape=shape)
    del linear

    scratch = None
    try:
        root = siril_work_directory()
        os.makedirs(root, exist_ok=True)
        scratch = tempfile.mkdtemp(prefix="color_preview_", dir=root)
        mono_starless = _run_starnet(scratch, "mono", luminance, siril_executable)
        color_starless_original = None
        if mono_starless is not None:
            color_starless_original = _run_starnet(
                scratch, "color", read_data(choice.picture_path), siril_executable
            )
    except (OSError, SubprocessError) as error:
        return ColorPreviewOutcome(False, f"StarNet could not run ({error})", True, record=record)
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
    if mono_starless is None or color_starless_original is None:
        return ColorPreviewOutcome(False, "StarNet did not give a starless picture", True, record=record)
    color_starless, _ = warp(
        np.nan_to_num(color_starless_original), picture_fit.transform, order=3, output_shape=shape
    )
    del color_starless_original
    color_starless = np.clip(color_starless, 0.0, 1.0)

    gaia_stars, gaia_note = _gaia_colors(mono_wcs, shape)
    if gaia_note:
        record["notes"].append(gaia_note.strip(" ()").capitalize() + ".")
    footprints, star_colors = measure_star_colors(
        luminance,
        mono_starless,
        linear_on_grid,
        covered,
        mono_wcs,
        gaia_stars,
        gaia_note,
        lambda: _bright_star_colors(mono_wcs, shape),
    )
    del linear_on_grid
    color_widths = star_colors.record.get("color_star_width_pixels_rgb")
    halo_radius = (
        float(resolve(COLOR_PREVIEW_HALO_WIDTHS).value) * max(float(color_widths[0]), float(color_widths[1]))
        if color_widths
        else _FALLBACK_HALO_PIXELS
    )
    wide_reach = None
    if star_colors.untrusted_wing is not None:
        # Region k + 1 has footprint area footprints.area[k].
        wide_reach = np.zeros(footprints.count + 1, dtype=np.float32)
        wide_reach[1:] = np.where(
            star_colors.untrusted_wing[1:],
            float(resolve(COLOR_PREVIEW_SATURATED_HALO_RADII).value) * np.sqrt(footprints.area / np.pi),
            0.0,
        )
    extended_a, extended_b, record["extended"] = extended_color(
        mono_starless, color_starless, covered, footprints.labels, halo_radius, wide_reach
    )
    del color_starless
    pixel_a, pixel_b = _pixel_color(luminance, warped_picture)
    picture = compose_color_picture(
        luminance, footprints, star_colors, extended_a, extended_b, pixel_a, pixel_b
    )
    record["star_counts"] = dict(star_colors.counts)
    record["stars"] = star_colors.record
    record["notes"].extend(star_colors.notes)

    output_path = color_preview_path_for(mono_stacked_path)
    record["made_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    record["runtime_seconds"] = round(time.monotonic() - started, 1)
    try:
        Image.fromarray((picture * 255.0 + 0.5).astype(np.uint8)).save(
            output_path, quality=PREVIEW_JPEG_QUALITY
        )
        with open(color_preview_record_path_for(mono_stacked_path), "w", encoding="utf-8") as record_file:
            json.dump(record, record_file, indent=1)
    except OSError as error:
        return ColorPreviewOutcome(
            False, f"the color picture could not be saved ({error})", True, record=record
        )
    return ColorPreviewOutcome(True, path=output_path, record=record)


def _remove_old_color_preview(stacked_path: str) -> None:
    """Remove the color picture and record of a stack, if there are any.

    Parameters
    ----------
    stacked_path : `str`
        The monochrome stack.
    """
    for path in (color_preview_path_for(stacked_path), color_preview_record_path_for(stacked_path)):
        if os.path.exists(path):
            os.remove(path)


def make_color_preview_for_target(
    target: Any,
    stacked_path: str,
    is_spectral: bool = False,
    skipped_steps: list[SkippedPreviewStep] | None = None,
) -> ColorPreviewOutcome:
    """Make the color picture of a target's monochrome stack, when it can.

    It runs after the monochrome picture is made. Any earlier color picture
    of the stack is removed first, so a picture never shows an older stack.
    The target's ``stacking.color_preview_image`` names the new picture, or
    is cleared; the caller saves the target. It is skipped, as information,
    when the setting is off, the stack is a spectral or color stack, or the
    target has no color stack. A failure is logged as a warning and added to
    `skipped_steps`.

    Parameters
    ----------
    target : `Target`
        The target that owns the stack.
    stacked_path : `str`
        The stack whose picture was just made.
    is_spectral : `bool`, optional
        Whether the stack is a spectroscopy stack (never colored).
    skipped_steps : `list` [`SkippedPreviewStep`], optional
        If given, a failure is added to it, so the job lists it.

    Returns
    -------
    outcome : `ColorPreviewOutcome`
        What the step did.
    """
    try:
        _remove_old_color_preview(stacked_path)
    except OSError as error:
        logger.warning("Could not remove the old color picture of '%s': %s.", stacked_path, error)
    outcome = _make_color_preview(target, stacked_path, is_spectral, skipped_steps)
    _record_on_target(target, stacked_path, outcome)
    return outcome


def _record_on_target(target: Any, stacked_path: str, outcome: ColorPreviewOutcome) -> None:
    """Point the target at its color picture, or clear a stale pointer.

    Only the stack the target shows is recorded. The caller saves the target.

    Parameters
    ----------
    target : `Target`
        The target that owns the stack.
    stacked_path : `str`
        The monochrome stack.
    outcome : `ColorPreviewOutcome`
        What the step did.
    """
    stacking = getattr(target, "stacking", None)
    if stacking is None or not hasattr(stacking, "color_preview_image"):
        return
    if outcome.made and outcome.path and stacking.stacked_image == stacked_path:
        stacking.color_preview_image = outcome.path
    elif stacking.color_preview_image == color_preview_path_for(stacked_path) or (
        stacking.color_preview_image and not os.path.isfile(stacking.color_preview_image)
    ):
        stacking.color_preview_image = ""


def _make_color_preview(
    target: Any,
    stacked_path: str,
    is_spectral: bool,
    skipped_steps: list[SkippedPreviewStep] | None,
) -> ColorPreviewOutcome:
    """Decide whether the color picture can be made, make it, log it.

    Parameters
    ----------
    target : `Target`
        The target that owns the stack.
    stacked_path : `str`
        The stack whose picture was just made.
    is_spectral : `bool`
        Whether the stack is a spectroscopy stack.
    skipped_steps : `list` [`SkippedPreviewStep`] or `None`
        If given, a failure is added to it.

    Returns
    -------
    outcome : `ColorPreviewOutcome`
        What the step did.
    """
    configuration = get_configuration()
    if not configuration.get_preview_color_from_other_stack_enabled():
        return ColorPreviewOutcome(False, "the setting preview_color_from_other_stack_enabled is off")
    if is_spectral:
        return ColorPreviewOutcome(False, "a spectral stack is not colored")
    if not os.path.isfile(stacked_path) or is_colour_image(stacked_path):
        return ColorPreviewOutcome(False, "the stack is not a monochrome stack")
    choice, reason = choose_color_stack(target.stacking, stacked_path)
    if choice is None:
        outcome = ColorPreviewOutcome(False, reason)
        logger.info("No color picture for '%s': %s.", os.path.basename(stacked_path), reason)
        return outcome
    siril_executable = configuration.get_siril_executable()
    if not siril_executable:
        outcome = ColorPreviewOutcome(False, "no Siril executable is configured, so StarNet cannot run")
        logger.info("No color picture for '%s': %s.", os.path.basename(stacked_path), outcome.reason)
        return outcome
    try:
        outcome = write_color_preview(stacked_path, choice, siril_executable)
    except (OSError, ValueError, KeyError, IndexError, MemoryError) as error:
        # A picture with odd contents must never fail the stack or the remake.
        outcome = ColorPreviewOutcome(False, f"the color picture could not be made ({error})", True)
    if outcome.made:
        logger.info("%s.", outcome.summary())
    elif outcome.failed:
        logger.warning("No color picture for '%s': %s.", os.path.basename(stacked_path), outcome.reason)
        if skipped_steps is not None:
            skipped_steps.append(SkippedPreviewStep(STEP_NAME, outcome.reason))
    else:
        logger.info("No color picture for '%s': %s.", os.path.basename(stacked_path), outcome.reason)
    return outcome
