"""Gives a small bright smooth object, such as M 57, its own stretch.

The preview stretch (see `stack_preview`) lifts the empty sky to a grey
level so that faint structure shows. The same curve pushes anything much
brighter than the sky to white. A star cluster does not mind, because its
stars are small dots. A small bright planetary nebula or galaxy core does:
its ring or its spiral core is a smooth patch, and the whole patch lands near
white as one flat disc. The bright-object route (see `bright_object`) is made
for the Moon and acts only when a large share of the picture whitens, so it
never sees such a patch.

This module protects the patch with a second stretch that applies only
inside it.

1. *Is the target the right kind?* Only a nebula or a galaxy qualifies (the
   target's `object_category`). A cluster, a star, a solar system object or an
   unknown kind never does. The kind decides first because a cluster of
   bright stars also makes bright regions, and the measure in step 2 cannot
   tell those apart (M 13 reads 0.26, a galaxy 0.2 to 0.3). The user may
   override this per target (`Target.preview_bright_object`): "off" never
   uses the step, and a chosen object (such as a nebula in the field of a
   cluster) replaces the target as the anchor and skips the kind check. Steps
   2 to 4 still apply to a chosen object, so a choice with no smooth bright
   region at its place does nothing.
2. *Is there a smooth bright region?* The stack is smoothed with a median
   filter, which removes stars. Where the smoothed image is well above the
   sky and the area is large enough, there is a region. The share of the
   region's light that sits in point-like peaks above the smoothed image must
   be small, or the region is a pile of stars and is dropped.
3. *The second stretch.* It is one highlight-compression curve of the
   linear value, not a new picture. The normal picture's brightness at the
   knee (`MASKED_STRETCH_KNEE`, 0.4) is
   traced back to a linear level, read from pixels outside the region. At
   and below that level the picture is left as it is. Above it, a log curve
   takes the picture from the knee to 1 at a white point that is a high
   percentile of the region's smoothed pixels, so a star on top of it cannot
   set it. The curve starts with the slope the normal picture has at the
   knee, so there is no bend. Values the normal stretch crushed together near
   white are spread out again. Because it is one rising function of the
   linear value, a brighter pixel is never darker than a dimmer one.
4. *Where the curve applies.* The region of step 2 is grown to every
   connected pixel of the smoothed image above the knee level, so the curve
   cuts through no bright area and no above-knee pixel of the object is left
   with the old stretch. Outside that area every pixel is below the knee,
   where the curve equals the picture, so there is no edge to feather. Star
   peaks inside the area follow the same curve; stars outside it are not
   touched. The curve is used only if the normal picture crushes the region:
   more than half of its pixels above 0.85. Size does not matter.

The step runs last, after the denoise and the star toning, on the picture
and on the linear stack the picture came from. The record of the plan says
whether the step was used and, if not, why.
"""

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
from astropy.io import fits
from scipy import ndimage

from astrometricslib._internal.drivers.fits_access import read_data, read_header, write_image
from astrometricslib._internal.drivers.image import AstrometricsImage
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError, StorageError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_bases import factor_for
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    RunBasis,
    TunedParameter,
)
from astrometricslib.pipelines.shared.image_scaling import measure_sky_background
from astrometricslib.pipelines.stacking.post_processing.bright_object import WHITE_POINT_PERCENTILE
from astrometricslib.pipelines.stacking.post_processing.stack_image import celestial_wcs, pixel_scale_arcsec

logger = logging.getLogger(__name__)

MASKED_STRETCH_CATEGORIES = frozenset({"nebula", "galaxy"})
"""The target kinds (`Target.object_category`) that may get the step."""

# The most a region's light may sit in point-like peaks for it to count as a
# smooth object. On the library's stacks (survey of 2026-11-22) the regions
# wanted read 0.09 to 0.34: M 57 0.13, M 81 0.14, NGC 4438 0.21, M 31 0.23
# to 0.34, NGC 7331 0.31, M 106 0.33. The halo of a bright star in the M 57
# field reads 0.39, and the regions of stars read 0.4 and more. The limit sits
# between the last two: 0.01 above the highest wanted region and 0.04 below
# that halo, so it is tight on both sides. The four star clusters with a
# bright region read 0.26 to 0.29, so this number cannot tell a cluster from
# a galaxy: the target's kind does that. Not validated beyond that list.
MASKED_STRETCH_STAR_PEAK_LIMIT = TunedParameter(
    name="stacking.masked_stretch_star_peak_limit",
    default=0.35,
    unit="fraction",
    description="Largest share of a bright region's light in point-like peaks for it to count as smooth.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D75",
)

# The median filter that removes stars is this many pixels on a side. A star
# on the reference setup is 2.3 pixels wide (FWHM), so 11 pixels is 4.7 star
# widths: a blob narrower than half the box is removed, and a ring or a
# galaxy core wider than that stays. On the reference setup this stands for
# 11 pixels; the code scales it as a multiple of the star width. Taken from
# the survey script of 2026-11-22 and not varied since.
_STAR_REMOVAL_BOX_PIXELS = TunedParameter(
    name="stacking.masked_stretch_star_removal_box_pixels",
    default=factor_for(11, RunBasis.STAR_WIDTH_PX),
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=11,
    unit="× star width",
    description="Side of the median filter that removes the stars before the regions are found.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-D75",
)

# The white point is read after an opening over a square of this many pixels
# (2.1 star widths on the reference setup, 5 pixels), so a star is removed
# and the ring's knots, which are wider, are kept. On the M 57 copy run of
# 2026-11-22 the 99.8th percentile of the region read 0.0515 raw, 0.047 after
# a 3-pixel opening, 0.0377 after 5 and 0.029 after 7, which flattened the
# ring. On the reference setup this stands for 5 pixels; the code scales it
# as a multiple of the star width.
_WHITE_POINT_OPENING_PIXELS = TunedParameter(
    name="stacking.masked_stretch_white_point_opening_pixels",
    default=factor_for(5, RunBasis.STAR_WIDTH_PX),
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=5,
    unit="× star width",
    description="Side of the opening that removes stars before the white point is read.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-D75",
)

# The mask fades to zero over this many pixels outside the region, 3 star
# widths (7 pixels on the reference setup). It is wide enough to hide the
# region's edge and narrow enough to leave the sky alone. The M 57 copy run
# of 2026-11-22 left a faint dark band near the ring at this width. Not
# tuned further. The code scales it as a multiple of the star width.
_FEATHER_PIXELS = TunedParameter(
    name="stacking.masked_stretch_feather_pixels",
    default=factor_for(7, RunBasis.STAR_WIDTH_PX),
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=7,
    unit="× star width",
    description="Distance outside a region over which the second stretch fades out.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-D75",
)

# A pixel of the smoothed image is part of a region when it is more than this
# many noise widths of the smoothed image above the sky. The sky-multiple rule
# below is the stricter one on a bright sky; this one keeps the noise of a
# very dark sky from making regions. Taken from the survey script of
# 2026-11-22.
_REGION_NOISE_WIDTHS = TunedParameter(
    name="stacking.masked_stretch_region_noise_widths",
    default=5.0,
    unit="sigma",
    description="Sky-noise widths above the sky that a smoothed pixel needs to join a region.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D75",
)

# A pixel of the smoothed image is part of a region when it is also at least
# this multiple of the sky level above the sky, so the region is twice as
# bright as the sky. Taken from the survey script of 2026-11-22. On the M 57
# stack the normal stretch puts the ring and the hole at 0.87, so it is
# crushed. The M 51 galaxy never reaches this level (its brightest smoothed
# pixel is 1.06 times the sky), so it is not a region.
_REGION_SKY_MULTIPLE = TunedParameter(
    name="stacking.masked_stretch_region_sky_multiple",
    default=1.0,
    unit="× sky level",
    description="Sky levels above the sky that a smoothed pixel needs to join a region.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D75",
)

# A region needs at least this many pixels. Smaller blobs are the cores of
# the brightest stars or hot spots, not an object to protect. The smallest
# wanted region of the survey is 215 pixels (M 31, Nikon).
_MINIMUM_REGION_PIXELS = TunedParameter(
    name="stacking.masked_stretch_minimum_region_pixels",
    default=200,
    unit="pixels",
    description="Fewest pixels a bright region needs to be protected.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D75",
)

# The region at the target must be at least this fraction of the target's
# catalog major axis across (equivalent diameter). A bright core is much
# smaller than the catalog extent, which includes the faint outskirts. In the
# copy run of 2026-11-22 the regions at the target were 1.07 of it for M 57,
# 0.059 for M 81 and 0.032 for M 31. A fiftieth sits below the smallest of
# those and still rules out a lone star (a region of 200 pixels is about 30
# arcseconds) for a target of about 25 arcminutes or less. Design estimate.
_MINIMUM_SIZE_RATIO = TunedParameter(
    name="stacking.masked_stretch_minimum_size_ratio",
    default=0.02,
    unit="× catalog size",
    description="Smallest region size, as a fraction of the target's catalog major axis.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D75",
)

# The region at the target must be at most this many catalog major axes
# across. A region cannot be much larger than the object: a larger one is a
# gradient or a neighbor that merged with it. 1.5 leaves room for a catalog
# size that is too small. Design estimate.
_MAXIMUM_SIZE_RATIO = TunedParameter(
    name="stacking.masked_stretch_maximum_size_ratio",
    default=1.5,
    unit="× catalog size",
    description="Largest region size, as a fraction of the target's catalog major axis.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D75",
)

# With no plate solution, the target is taken to be at the image center, and
# a region counts as at the target when it comes within this fraction of the
# image's short side of the center. On the reference setup the short side is
# 3008 px, or 94 arcminutes, so 0.05 is 4.7 arcminutes. The mount points at
# the target, and a slew that is not centered is good to a few arcminutes; a
# neighboring star or galaxy is usually farther off. Design estimate.
_CENTER_TOLERANCE_FRACTION = TunedParameter(
    name="stacking.masked_stretch_center_tolerance_fraction",
    default=0.05,
    unit="× image short side",
    description="How near the image center a region must come when the stack has no plate solution.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D75",
)

# A pixel of the stack counts as a point-like peak when it stands this many
# pixel-noise widths above the smoothed image. 5 widths keeps the noise of
# the sky out (it would take about one pixel in 3.5 million).
_PEAK_NOISE_WIDTHS = TunedParameter(
    name="stacking.masked_stretch_peak_noise_widths",
    default=5.0,
    unit="sigma",
    description="Noise widths above the smoothed image at which a pixel counts as a point-like peak.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D75",
)

# The smoothed image is sampled every n-th pixel to measure its own noise.
_NOISE_SAMPLE_STRIDE = 4

# The rounds of 3-sigma clipping that find the smoothed image's sky noise.
_NOISE_CLIP_ROUNDS = 4

# The smallest brightness for which a color factor is worked out (guards the
# division).
_MINIMUM_VALUE = 1e-6

# The picture brightness at which the compression starts. Below it the picture
# is left as it is. A lower knee gives the curve more of the range to spread
# the crushed highlights over, and dims the bulge or halo around the object
# more. Real preview runs on copies of 2026-11-22 (knees 0.65, 0.5, 0.4 and
# 0.3): the M 57 ring minus hole read 0.048 in the normal picture and 0.123,
# 0.144, 0.159 and 0.175; the spread of M 31's region 0.048 and 0.094, 0.118,
# 0.135 and 0.150; the halo around M 31 dimmed from 0.830 to 0.801, 0.747,
# 0.710 and 0.675, and the halo around M 81 from 0.744 to 0.740, 0.694, 0.662
# and 0.631. 0.4 gains 3.3 times the ring contrast and 2.8 times the spread
# for a halo no more than 0.12 dimmer; 0.3 dims the halo a further 0.03 to 0.04
# for a gain of 0.015. Chosen by eye on the contact sheet; not validated
# beyond these four targets. The star toning's own knee is 0.65 and does not
# limit this one.
MASKED_STRETCH_KNEE = TunedParameter(
    name="stacking.masked_stretch_knee",
    default=0.4,
    unit="fraction",
    description="Picture brightness at which the masked stretch's highlight compression starts.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D75",
)

# The compression is used only when the normal picture crushes the region:
# more than this share of the region's pixels must lie above 0.85. Real
# preview runs on copies of 2026-11-22 (GraXpert, Cosmic Clarity and star
# toning, no extra stretch) gave 0.97 for M 31, 0.82 for M 81, 0.88 for
# M 57 and 1.00 for NGC 7331, and the compression roughly doubled the
# picture's spread inside the region for each (10th to 90th percentile 0.05
# to 0.11, 0.05 to 0.12). Half sits below the lowest of those and above a
# region the normal picture already shows in full. Size is not the rule: M 31
# (0.29% of the frame) and M 81 (0.01%) both gained. Design estimate.
MASKED_STRETCH_CRUSHED_SHARE = TunedParameter(
    name="stacking.masked_stretch_crushed_share",
    default=0.5,
    unit="fraction",
    description="Share of a region's pixels the normal picture must put above the crushed level.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D75",
)

# The picture brightness above which a pixel counts as crushed toward white.
# The star toning's tanh bends everything above its 0.65 knee to under 0.92,
# so 0.85 is where values are within about 0.07 of that ceiling. Design
# estimate.
_CRUSHED_LEVEL = 0.85

# The number of pixels outside the mask whose brightness is nearest a target
# brightness. Their medians give the picture's brightness and the linear
# level there, so a stack whose picture has no pixel exactly at the knee (a
# sharp-edged synthetic ring) still gets one. 2,000 pixels hold the median to
# about 2% of the spread of the pixels (1.25 over the square root of 2,000),
# and the copies of 2026-11-22 had tens of thousands within 0.02 of the knee.
# Design estimate.
_KNEE_PIXELS = 2000

# A pixel is extended, not a star, when it is within this share of the
# smoothed (star-free) value. A star peak stands many times above it, and the
# noise of a pixel at the knee level is a few percent of its value on the
# library's stacks (design estimate, not measured separately).
_EXTENDED_TOLERANCE = 0.05

# The picture's slope at the knee is read between the knee and this much
# darker, where the star toning has not yet bent the picture. 0.10 is a third
# of the way from the sky level (about 0.13) to the knee (0.65) at the lower
# end of the curve and keeps the two sets of pixels far enough apart for
# their medians to differ. Design estimate.
_SLOPE_SPAN = 0.10

# The largest curvature constant the log curve may take, so a bisection on a
# very steep slope ends. At 1e12 the curve is flat to the eye at 1 a pixel
# past the knee, which no real stack reaches.
_LARGEST_CURVATURE = 1e12

# Reasons given when the step does not act.
REASON_NOT_APPLICABLE_KIND = "the target is not a nebula or a galaxy"
REASON_TURNED_OFF = "turned off for this target"
REASON_NO_REGION = "no smooth bright region was found"
REASON_NO_TARGET_REGION = "no bright region at the target"
REASON_TARGET_OUTSIDE = "the target is outside the frame"
REASON_SIZE_MISMATCH = "the bright region at the target does not fit the target's catalog size"
SOURCE_WCS = "plate solution"
SOURCE_CENTER = "image center"
REASON_NO_SKY = "the sky could not be measured"
REASON_NO_WHITE_POINT = "the region is not brighter than the sky"
REASON_SIZE_DIFFERS = "the picture and the stack differ in size"
REASON_UNREADABLE = "the stack could not be read"


@dataclass(frozen=True)
class TargetAnchor:
    """Where the protected object is on the sky, and its catalog size.

    Attributes
    ----------
    ra_deg : `float` or `None`
        The object's right ascension in degrees, or `None` if not known.
    dec_deg : `float` or `None`
        The object's declination in degrees, or `None` if not known.
    major_axis_arcmin : `float` or `None`
        The catalog major axis in arcminutes, or `None` if not known.
    mode : `str`
        ``"auto"`` (the anchor is the target and its kind must qualify),
        ``"off"`` (the step is not used) or ``"object"`` (the anchor is an
        object the user chose, and the target's kind is not checked).
    object_id : `str` or `None`
        SIMBAD's name of the chosen object, with mode ``"object"``.
    """

    ra_deg: float | None = None
    dec_deg: float | None = None
    major_axis_arcmin: float | None = None
    mode: str = "auto"
    object_id: str | None = None


def anchor_for_target(target: Any) -> TargetAnchor:
    """Read where the masked stretch should look, from a target.

    The target's own catalog position and size are used, unless the user
    turned the step off or chose another object
    (`Target.preview_bright_object`).

    Parameters
    ----------
    target : `Target`
        The target.

    Returns
    -------
    anchor : `TargetAnchor`
        The position and size. A position that cannot be read is `None`.
        A choice of "object" without a usable position counts as "auto".
    """
    choice = getattr(target, "preview_bright_object", None)
    if choice is not None and choice.mode == "off":
        return TargetAnchor(mode="off")
    if (
        choice is not None
        and choice.mode == "object"
        and choice.ra_deg is not None
        and choice.dec_deg is not None
    ):
        return TargetAnchor(
            float(choice.ra_deg),
            float(choice.dec_deg),
            float(choice.major_axis_arcmin) if choice.major_axis_arcmin else None,
            mode="object",
            object_id=choice.simbad_id,
        )
    try:
        ra_deg, dec_deg = float(target.ra_deg), float(target.dec_deg)
    except ValueError, TypeError, AttributeError, InvalidArgumentError:
        ra_deg = dec_deg = None
    axis = getattr(target, "major_axis_arcmin", None)
    return TargetAnchor(ra_deg, dec_deg, float(axis) if axis else None)


@dataclass(frozen=True)
class MaskedStretchPlan:
    """What the masked stretch will do, or why it will not.

    Attributes
    ----------
    used : `bool`
        Whether the second stretch is applied.
    reason : `str`
        Why it is not applied, or an empty string when it is.
    category : `str` or `None`
        The target's kind that was checked.
    region_count : `int`
        The smooth bright regions the second stretch covers.
    region_pixels : `int`
        The pixels in those regions.
    black_point : `float` or `None`
        The sky level, in the stack's own units, that comes out black.
    white_point : `float` or `None`
        The brightness, in the stack's own units, that comes out white.
    weights : `numpy.ndarray` or `None`
        The region found in step 2, as 1 inside and a fall to 0 outside over a
        few star widths, as large as the picture. `None` when unused.
    smoothed : `numpy.ndarray` or `None`
        The stack with the stars removed, as large as the picture, which the
        region is grown on. `None` when unused.
    position_source : `str` or `None`
        How the target's place in the picture was found: the plate solution
        or the image center. `None` if the step stopped before that.
    size_ratio : `float` or `None`
        The region's equivalent diameter over the catalog major axis, or
        `None` when the size could not be checked.
    mode : `str`
        The user's choice for this target: ``"auto"``, ``"off"`` or
        ``"object"``.
    object_id : `str` or `None`
        SIMBAD's name of the chosen object, with mode ``"object"``.
    """

    used: bool
    reason: str
    category: str | None
    region_count: int = 0
    region_pixels: int = 0
    black_point: float | None = None
    white_point: float | None = None
    weights: np.ndarray | None = None
    smoothed: np.ndarray | None = None
    position_source: str | None = None
    size_ratio: float | None = None
    mode: str = "auto"
    object_id: str | None = None

    def record(self) -> dict[str, Any]:
        """Describe the plan for the preview settings record.

        Returns
        -------
        record : `dict` [`str`, `Any`]
            JSON-ready fields: whether it was used, why not, the target kind,
            the region count and area, the white point, the user's choice
            (``mode``) and the chosen object's name (``object``).
        """
        return {
            "used": self.used,
            "reason": self.reason,
            "category": self.category,
            "region_count": self.region_count,
            "region_pixels": self.region_pixels,
            "white_point": self.white_point,
            "position_source": self.position_source,
            "size_ratio": self.size_ratio,
            "mode": self.mode,
            "object": self.object_id,
        }

    def step_sentence(self) -> str:
        """Say what the step did, for the list of preview steps.

        Returns
        -------
        sentence : `str`
            A plain sentence that names the regions and the white point, or
            the reason the step was not used.
        """
        if not self.used:
            return f"Masked stretch not used: {self.reason}"
        return (
            f"Masked stretch done on {self.region_count} bright smooth region"
            f"{'' if self.region_count == 1 else 's'} ({self.region_pixels} pixels, "
            f"white point {self.white_point:.4g})"
        )


def _unused(
    reason: str, category: str | None, position_source: str | None = None, anchor: TargetAnchor | None = None
) -> MaskedStretchPlan:
    """Build the plan for a stack the step leaves alone.

    Parameters
    ----------
    reason : `str`
        Why the step is not used.
    category : `str` or `None`
        The target's kind.
    position_source : `str` or `None`, optional
        How the target's place was found, if the step got that far.
    anchor : `TargetAnchor`, optional
        The anchor in use, so the plan records the user's choice.

    Returns
    -------
    plan : `MaskedStretchPlan`
        A plan with ``used`` false.
    """
    return MaskedStretchPlan(
        used=False,
        reason=reason,
        category=category,
        position_source=position_source,
        mode=anchor.mode if anchor else "auto",
        object_id=anchor.object_id if anchor else None,
    )


def _locate_target(
    shape: tuple[int, int], header: fits.Header | None, anchor: TargetAnchor
) -> tuple[float, float, str, float, float | None] | str:
    """Find the target's pixel and the plate scale.

    Parameters
    ----------
    shape : `tuple` [`int`, `int`]
        The picture's rows and columns.
    header : `astropy.io.fits.Header` or `None`
        The stack's header.
    anchor : `TargetAnchor`
        The target's catalog position and size.

    Returns
    -------
    located : `tuple` or `str`
        The column, row, source name, the distance in pixels within which a
        region counts as at the target, and the plate scale in arcseconds per
        pixel (`None` if unknown). Or the reason the target is outside the
        frame.
    """
    rows, columns = shape
    wcs = celestial_wcs(header) if header is not None else None
    scale: float | None = None
    if wcs is not None:
        scale = pixel_scale_arcsec(wcs)
    elif header is not None:
        try:
            focal_length, pixel_size = float(header["FOCALLEN"]), float(header["XPIXSZ"])
            scale = 206.265 * pixel_size / focal_length if focal_length > 0 and pixel_size > 0 else None
        except KeyError, ValueError, TypeError:
            scale = None
    if wcs is not None and anchor.ra_deg is not None and anchor.dec_deg is not None:
        column, row = (float(value) for value in wcs.world_to_pixel_values(anchor.ra_deg, anchor.dec_deg))
        if not (np.isfinite(column) and np.isfinite(row) and 0 <= column < columns and 0 <= row < rows):
            return REASON_TARGET_OUTSIDE
        return column, row, SOURCE_WCS, max(resolve(_FEATHER_PIXELS).value, 1.0), scale
    tolerance = resolve(_CENTER_TOLERANCE_FRACTION).value * min(rows, columns)
    return (columns - 1) / 2.0, (rows - 1) / 2.0, SOURCE_CENTER, tolerance, scale


def _luminance(data: np.ndarray) -> np.ndarray | None:
    """Reduce a stack to one 2-D plane.

    Parameters
    ----------
    data : `numpy.ndarray`
        A 2-D stack, or a colour stack with the channels first or last.

    Returns
    -------
    plane : `numpy.ndarray` or `None`
        The plane (colour averaged), with non-finite values set to zero, or
        `None` if the data are not an image.
    """
    plane = np.asarray(data, dtype=np.float32)
    if plane.ndim == 3:
        plane = plane.mean(axis=0 if plane.shape[0] in (1, 3, 4) else 2)
    if plane.ndim != 2:
        return None
    return np.nan_to_num(plane)


def _smoothed_sky_noise(smoothed: np.ndarray) -> float:
    """Measure the noise of the star-free image over the empty sky.

    Parameters
    ----------
    smoothed : `numpy.ndarray`
        The median-filtered image.

    Returns
    -------
    noise : `float`
        One standard deviation of the sky, found by clipping everything more
        than 3 widths from the median a few times over a sparse grid.
    """
    values = smoothed[::_NOISE_SAMPLE_STRIDE, ::_NOISE_SAMPLE_STRIDE].ravel()
    nonzero = values[values != 0]
    values = nonzero if nonzero.size > 1000 else values
    spread = 0.0
    for _ in range(_NOISE_CLIP_ROUNDS):
        centre = np.median(values)
        spread = 1.4826 * float(np.median(np.abs(values - centre)))
        kept = values[np.abs(values - centre) < 3.0 * spread]
        if kept.size < 10:
            break
        values = kept
    return spread


def plan_masked_stretch(
    data: np.ndarray,
    category: str | None,
    anchor: TargetAnchor | None = None,
    header: fits.Header | None = None,
) -> MaskedStretchPlan:
    """Decide whether a stack gets the masked stretch, and plan it.

    Parameters
    ----------
    data : `numpy.ndarray`
        The linear stack. A colour stack is averaged across its channels.
    category : `str` or `None`
        The target's kind (`Target.object_category`). Only ``"nebula"`` and
        ``"galaxy"`` qualify, unless the anchor holds an object the user
        chose (then the kind is not checked).
    anchor : `TargetAnchor`, optional
        The target's catalog position and size. Only the smooth region that
        holds the target's place, and fits its catalog size, is used. Without
        a position the image center stands for it; without a size only the
        position is tested.
    header : `astropy.io.fits.Header`, optional
        The stack's header, for its plate solution or its focal length and
        pixel size. Without a solution the image center stands for the
        target.

    Returns
    -------
    plan : `MaskedStretchPlan`
        The plan. When ``used`` is false, ``reason`` says why: the kind does
        not qualify, the sky could not be measured, no smooth bright region
        was found, none holds the target, the target is outside the frame,
        the region at the target does not fit its catalog size, or the region
        is not brighter than the sky.
    """
    anchor = anchor or TargetAnchor()
    if anchor.mode == "off":
        return _unused(REASON_TURNED_OFF, category, anchor=anchor)
    if anchor.mode != "object" and category not in MASKED_STRETCH_CATEGORIES:
        return _unused(
            REASON_NOT_APPLICABLE_KIND + f" (kind: {category or 'unknown'})", category, anchor=anchor
        )
    stack = _luminance(data)
    if stack is None:
        return _unused(REASON_UNREADABLE, category, anchor=anchor)
    sky = measure_sky_background(stack.astype(np.float64))
    if sky is None:
        return _unused(REASON_NO_SKY, category, anchor=anchor)
    sky_level, pixel_noise = sky
    box = max(3, round(resolve(_STAR_REMOVAL_BOX_PIXELS).value))
    smoothed = ndimage.median_filter(stack, size=box, mode="nearest")
    smoothed_noise = _smoothed_sky_noise(smoothed)
    threshold = sky_level + max(
        resolve(_REGION_NOISE_WIDTHS).value * smoothed_noise,
        resolve(_REGION_SKY_MULTIPLE).value * abs(sky_level),
    )
    labels, count = ndimage.label(smoothed > threshold)
    if count == 0:
        return _unused(REASON_NO_REGION, category, anchor=anchor)
    indices = np.arange(1, count + 1)
    areas = ndimage.sum(np.ones_like(stack), labels, indices)
    light = np.clip(stack - sky_level, 0.0, None)
    peak_light = np.where(
        stack - smoothed > resolve(_PEAK_NOISE_WIDTHS).value * pixel_noise,
        np.clip(stack - smoothed, 0.0, None),
        0.0,
    )
    region_light = ndimage.sum(light, labels, indices)
    region_peaks = ndimage.sum(peak_light, labels, indices)
    largest_share = resolve(MASKED_STRETCH_STAR_PEAK_LIMIT).value
    minimum_area = resolve(_MINIMUM_REGION_PIXELS).value
    kept = [
        int(index)
        for index, area, flux, peaks in zip(indices, areas, region_light, region_peaks, strict=True)
        if area >= minimum_area and flux > 0.0 and peaks / flux < largest_share
    ]
    if not kept:
        return _unused(REASON_NO_REGION, category, anchor=anchor)
    located = _locate_target(stack.shape, header, anchor)
    if isinstance(located, str):
        return _unused(located, category, anchor=anchor)
    target_column, target_row, source, tolerance, plate_scale = located
    # Only the region that holds the target's place is protected: the nearest
    # one within the tolerance. Its holes are filled, so a ring counts as one.
    found: tuple[float, tuple[slice, slice], np.ndarray] | None = None
    objects = ndimage.find_objects(labels)
    for index in kept:
        window = objects[index - 1]
        filled = ndimage.binary_fill_holes(labels[window] == index)
        rows_in, columns_in = np.nonzero(filled)
        gap = float(
            np.min(
                np.hypot(rows_in + window[0].start - target_row, columns_in + window[1].start - target_column)
            )
        )
        if gap <= tolerance and (found is None or gap < found[0]):
            found = (gap, window, filled)
    if found is None:
        return _unused(REASON_NO_TARGET_REGION, category, source, anchor)
    _, window, filled_region = found
    region_pixels = int(filled_region.sum())
    size_ratio: float | None = None
    if anchor.major_axis_arcmin and plate_scale:
        catalog_pixels = anchor.major_axis_arcmin * 60.0 / plate_scale
        size_ratio = 2.0 * float(np.sqrt(region_pixels / np.pi)) / catalog_pixels
        if not resolve(_MINIMUM_SIZE_RATIO).value <= size_ratio <= resolve(_MAXIMUM_SIZE_RATIO).value:
            return MaskedStretchPlan(
                used=False,
                reason=REASON_SIZE_MISMATCH + f" (ratio {size_ratio:.2f})",
                category=category,
                position_source=source,
                size_ratio=size_ratio,
                mode=anchor.mode,
                object_id=anchor.object_id,
            )
    inside = np.zeros(stack.shape, dtype=bool)
    inside[window] = filled_region
    # The white point comes from the region's own pixels after a grey-scale
    # opening, which removes features narrower than the box. A star on the
    # ring is removed, but the ring's knots, which are wider, stay.
    opening = max(2, round(resolve(_WHITE_POINT_OPENING_PIXELS).value))
    inside_values = ndimage.grey_opening(stack, size=(opening, opening))[inside]
    white_point = float(np.percentile(inside_values, resolve(WHITE_POINT_PERCENTILE).value))
    if not white_point > sky_level:
        return _unused(REASON_NO_WHITE_POINT, category, anchor=anchor)
    distance = ndimage.distance_transform_edt(~inside)
    fade = np.clip(1.0 - distance / max(resolve(_FEATHER_PIXELS).value, 1.0), 0.0, 1.0)
    weights = (fade * fade * (3.0 - 2.0 * fade)).astype(np.float32)
    return MaskedStretchPlan(
        used=True,
        reason="",
        category=category,
        region_count=1,
        region_pixels=int(inside.sum()),
        black_point=float(sky_level),
        white_point=white_point,
        weights=weights,
        smoothed=smoothed.astype(np.float32),
        position_source=source,
        size_ratio=size_ratio,
        mode=anchor.mode,
        object_id=anchor.object_id,
    )


def plan_masked_stretch_for_file(
    path: str, category: str | None, anchor: TargetAnchor | None = None
) -> MaskedStretchPlan:
    """Plan the masked stretch for a FITS stack on disk.

    Parameters
    ----------
    path : `str`
        Path of the linear stack the picture is made from.
    category : `str` or `None`
        The target's kind (see `plan_masked_stretch`).
    anchor : `TargetAnchor`, optional
        The target's catalog position and size (see `plan_masked_stretch`).

    Returns
    -------
    plan : `MaskedStretchPlan`
        The plan. A kind that does not qualify is answered without reading
        the file. A file that cannot be read gives an unused plan, with the
        reason logged at debug level.
    """
    if anchor is not None and anchor.mode == "off":
        return _unused(REASON_TURNED_OFF, category, anchor=anchor)
    if (anchor is None or anchor.mode != "object") and category not in MASKED_STRETCH_CATEGORIES:
        return _unused(
            REASON_NOT_APPLICABLE_KIND + f" (kind: {category or 'unknown'})", category, anchor=anchor
        )
    try:
        data = AstrometricsImage(path).data
    except (OSError, ValueError, KeyError, NotFoundError, StorageError) as error:
        logger.debug("Could not read '%s' to plan a masked stretch: %s", path, error)
        return _unused(REASON_UNREADABLE, category, anchor=anchor)
    try:
        header = read_header(path)
    except OSError, ValueError, KeyError:
        header = None
    return plan_masked_stretch(data, category, anchor, header)


def _level_near(
    stack: np.ndarray, brightness: np.ndarray, outside: np.ndarray, target: float
) -> tuple[float, float] | None:
    """Find the picture brightness and the linear level nearest a brightness.

    Parameters
    ----------
    stack : `numpy.ndarray`
        The linear stack, 2-D.
    brightness : `numpy.ndarray`
        The normal picture's brightness, 2-D, from 0 to 1.
    outside : `numpy.ndarray`
        Flat indices of the pixels that may be read: the extended ones (see
        `_extended_pixels`), where the picture is a plain function of the
        stack.
    target : `float`
        The picture brightness to look for.

    Returns
    -------
    point : `tuple` [`float`, `float`] or `None`
        The medians, over the `_KNEE_PIXELS` readable pixels whose picture
        brightness is nearest `target`, of that brightness and of the linear
        value. `None` if fewer pixels are readable.
    """
    if outside.size < _KNEE_PIXELS:
        return None
    distance = np.abs(brightness.ravel()[outside] - target)
    nearest = outside[np.argpartition(distance, _KNEE_PIXELS - 1)[:_KNEE_PIXELS]]
    return float(np.median(brightness.ravel()[nearest])), float(np.median(stack.ravel()[nearest]))


def _extended_pixels(stack: np.ndarray, smoothed: np.ndarray) -> np.ndarray:
    """List the pixels that are not stars or noise spikes.

    Parameters
    ----------
    stack : `numpy.ndarray`
        The linear stack, 2-D.
    smoothed : `numpy.ndarray`
        The stack with the stars removed.

    Returns
    -------
    indices : `numpy.ndarray`
        Flat indices of the pixels whose stack value is within
        `_EXTENDED_TOLERANCE` of the smoothed value. The picture is not
        changed by this function, so these pixels can be read anywhere,
        inside the region too.
    """
    close = np.abs(stack - smoothed) <= _EXTENDED_TOLERANCE * np.abs(smoothed)
    return np.flatnonzero(close.ravel())


def _log_curve(fraction: np.ndarray, slope: float) -> np.ndarray:
    """Apply the log curve that starts with a given slope and ends at 1.

    Parameters
    ----------
    fraction : `numpy.ndarray`
        Position from 0 (the knee level) to 1 (the white point).
    slope : `float`
        The curve's slope at 0, as a multiple of the straight line's (1).

    Returns
    -------
    curved : `numpy.ndarray`
        ``log(1 + a * x) / log(1 + a)``, with ``a`` set so that the slope at
        0 is `slope`. A slope of 1 or less gives the straight line. The curve
        is 0 at 0, 1 at 1, rises with `fraction` and bends down.
    """
    if slope <= 1.0 + 1e-9:
        return fraction
    low, high = 1e-9, _LARGEST_CURVATURE
    for _ in range(200):
        middle = np.sqrt(low * high)
        if middle / np.log1p(middle) < slope:
            low = middle
        else:
            high = middle
    curvature = np.sqrt(low * high)
    return np.log1p(curvature * fraction) / np.log1p(curvature)


def _grown_region(smoothed: np.ndarray, core: np.ndarray, knee_level: float) -> np.ndarray:
    """Grow the region to every connected pixel above the knee level.

    Parameters
    ----------
    smoothed : `numpy.ndarray`
        The stack with the stars removed.
    core : `numpy.ndarray`
        Boolean mask of the region found in step 2.
    knee_level : `float`
        The linear level the normal picture shows at the knee.

    Returns
    -------
    grown : `numpy.ndarray`
        Boolean mask: the core, and every pixel of the smoothed image above
        the knee level that is connected to it, with its holes filled.
    """
    labels, _ = ndimage.label(smoothed > knee_level)
    touching = np.unique(labels[core])
    grown = np.isin(labels, touching[touching > 0]) | core
    return ndimage.binary_fill_holes(grown)


def apply_masked_stretch(
    picture: np.ndarray, linear: np.ndarray, plan: MaskedStretchPlan
) -> np.ndarray | None:
    """Compress the highlights of a finished picture where the object is.

    Parameters
    ----------
    picture : `numpy.ndarray`
        The stretched picture, values from 0 to 1, 2-D or colour with the
        channels first.
    linear : `numpy.ndarray`
        The linear stack the picture came from, the same size.
    plan : `MaskedStretchPlan`
        A plan that is used.

    Returns
    -------
    result : `numpy.ndarray` or `None`
        The picture, with the pixels in the grown region (see
        `_grown_region`) that lie above the knee level given the curve:
        from the knee at that level to 1 at the white point, with the slope
        the picture has at the knee. Every other pixel is unchanged. The
        curve is a rising function of the stack, so a brighter pixel is never
        darker than a dimmer one inside the region. A colour pixel is scaled
        by one factor, so its colour does not change. `None` when the plan is
        unused, the sizes differ, or too few extended pixels exist to find the
        knee.
    """
    stack = _luminance(linear)
    if (
        not plan.used
        or plan.weights is None
        or plan.smoothed is None
        or plan.white_point is None
        or stack is None
        or stack.shape != plan.weights.shape
    ):
        return None
    clipped = np.clip(np.asarray(picture, dtype=np.float64), 0.0, 1.0)
    brightness = clipped.mean(axis=0) if clipped.ndim == 3 else clipped
    if brightness.shape != stack.shape:
        return None
    stack = stack.astype(np.float64)
    region = plan.weights >= 1.0
    if float(np.mean(brightness[region] > _CRUSHED_LEVEL)) <= resolve(MASKED_STRETCH_CRUSHED_SHARE).value:
        # The normal picture already shows the region: nothing to spread out.
        return clipped.astype(np.float32)
    outside = _extended_pixels(stack, plan.smoothed.astype(np.float64))
    knee_target = resolve(MASKED_STRETCH_KNEE).value
    knee_point = _level_near(stack, brightness, outside, knee_target)
    lower_point = _level_near(stack, brightness, outside, knee_target - _SLOPE_SPAN)
    if knee_point is None or lower_point is None:
        return None
    knee, knee_level = knee_point
    lower, lower_level = lower_point
    if plan.white_point <= knee_level or knee_level <= lower_level or knee <= lower:
        # The region never rises above the knee: nothing to compress.
        return clipped.astype(np.float32)
    slope = (knee - lower) / (knee_level - lower_level) * (plan.white_point - knee_level) / (1.0 - knee)
    grown = _grown_region(plan.smoothed.astype(np.float64), region, knee_level)
    fraction = np.clip((stack - knee_level) / (plan.white_point - knee_level), 0.0, 1.0)
    curve = knee + (1.0 - knee) * _log_curve(fraction, slope)
    applies = grown & (stack > knee_level)
    mixed = np.where(applies, curve, brightness)
    if clipped.ndim == 3:
        factor = np.divide(mixed, brightness, out=np.ones_like(brightness), where=brightness > _MINIMUM_VALUE)
        # A dark pixel that must get brighter has no colour to keep: it
        # becomes a grey of the new brightness.
        dark = brightness <= _MINIMUM_VALUE
        result = np.where(dark, mixed, clipped * factor)
        return np.clip(result, 0.0, 1.0).astype(np.float32)
    return np.clip(mixed, 0.0, 1.0).astype(np.float32)


def apply_masked_stretch_in_file(
    linear_path: str, picture_path: str, output_path: str, plan: MaskedStretchPlan
) -> bool:
    """Blend the second stretch into a stretched FITS picture and save it.

    Parameters
    ----------
    linear_path : `str`
        Path of the linear stack the picture came from.
    picture_path : `str`
        Path of the stretched picture, values from 0 to 1.
    output_path : `str`
        Path to write. The header of the picture is kept.
    plan : `MaskedStretchPlan`
        A plan that is used.

    Returns
    -------
    succeeded : `bool`
        `True` if the blended picture was written. `False` if a file could
        not be read, or the two differ in size. The reason is logged.
    """
    try:
        linear = read_data(linear_path)
        picture = read_data(picture_path)
        header = read_header(picture_path)
    except (OSError, ValueError, KeyError) as error:
        logger.warning("Could not read the files for the masked stretch of '%s': %s.", picture_path, error)
        return False
    if linear is None or picture is None:
        logger.warning("Could not apply the masked stretch to '%s': it is not an image.", picture_path)
        return False
    blended = apply_masked_stretch(picture, linear, plan)
    if blended is None:
        logger.warning("Could not apply the masked stretch to '%s': %s.", picture_path, REASON_SIZE_DIFFERS)
        return False
    write_image(output_path, blended, header)
    return True
