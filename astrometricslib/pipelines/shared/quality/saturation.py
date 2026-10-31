"""Tools to check if a photo is too bright (overexposed).

This counts how many pixels are completely maxed out (pure white). The
photo isn't thrown away just because a few stars are too bright, but
a warning flag is left so later steps know those specific stars can't
be measured accurately.

`resolve_saturation_threshold` finds the level at which a raw frame's pixels
count as saturated, from the camera's profile. The raw frame check and the
spectral frame check both use it, so they agree on the level.

`clip_and_linearity_levels` gives the two levels the target-clipping check
counts pixels against: the clip ceiling (less a small tolerance) and the
photometric linearity limit.
"""

from dataclasses import dataclass

import numpy as np

from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.models.camera_profile import CameraProfile, ProvenanceKind

SATURATION_ADU = 65000
"""Pixels at or above this value count as saturated when no camera profile
gives a level (16-bit frames). See `resolve_saturation_threshold`."""

# A stack has no fixed saturation level: Siril rescales it, so a saturated
# star can sit anywhere below 1.0 and an unsaturated stack also tops out at
# 1.0. So the level is found from the stack's own pixels.
#
# In the Vega session's group
# stacks the saturated pixels sit at a ceiling that differs from group to
# group (about 1.0 for the 0.5-2 s groups but 0.81-0.82 for the 3 s and 5 s
# groups, because Siril's normalization rescales each stack), and the 5 s
# group's ceiling lies below 0.95, so its saturated trail was used as if it
# were a measurement. That made the combined spectrum too red (measured
# 2026-09-21, see
# documentation/evidence/stack_exposure_groups_20260920.json). So each group's
# ceiling is measured from its own pixels: the median of its brightest
# SATURATION_CEILING_SAMPLE_PIXELS pixels. Saturated pixels pile up there, so
# it is a ceiling only if at least SATURATION_PLATEAU_MINIMUM_PIXELS pixels lie
# within SATURATION_PLATEAU_TOLERANCE of it. On Vega the saturated groups had
# 571-828 such pixels and 101-576 for the 0.5-2 s groups; a real, unsaturated
# peak has only a handful. The counts 25, 50 and 3% were chosen from those
# numbers and have not been tested on other stars. Pixels above
# SATURATION_MASK_FRACTION_OF_CEILING times the ceiling are masked, because the
# plateau is ragged (0.79-0.85 in the 3 s group) and the last few percent
# below it are already clipped by some frames in the average.
SATURATION_CEILING_SAMPLE_PIXELS = 25
SATURATION_PLATEAU_MINIMUM_PIXELS = 50
SATURATION_PLATEAU_TOLERANCE = 0.03
SATURATION_MASK_FRACTION_OF_CEILING = 0.9

# The pile-up test alone is fooled by a stack with no bright star: the
# brightest pixels of plain sky noise also bunch together (the top 25 of a
# 10,000-pixel noise field sit within 3% of each other). A real saturation
# ceiling stands far above the sky, so it must be at least this many times the
# stack's median. The M 13 stacks have ceilings of 1.0 against medians of
# 0.004-0.005 (a ratio near 200) and the Vega group ceilings of 0.8 lie far
# above their sky. The 10 was chosen well below those and well above noise
# (a ratio near 1.5); it has not been tested on other stars.
SATURATION_CEILING_MINIMUM_OVER_MEDIAN = 10.0


def compute_saturated_pixel_fraction(data: np.ndarray, saturation_threshold: float) -> float:
    """Calculate what percentage of the image is completely blown out.

    Parameters
    ----------
    data : `numpy.ndarray`
        The actual pixels of the image.
    saturation_threshold : `float`
        The brightness level that counts as 'maxed out'. This changes
        depending on which camera took the picture.

    Returns
    -------
    saturated_fraction : `float`
        The percentage of pixels that are too bright (from 0.0 to 1.0).
    """
    if data.size == 0:
        return 0.0
    return float(np.count_nonzero(data >= saturation_threshold) / data.size)


# The share of saturated pixels (0.1%) above which a frame or stack is flagged.
# A guess so far: it has not been checked against real stacks.
DEFAULT_SATURATION_FLAG_THRESHOLD = 0.001


def is_saturation_significant(
    saturated_fraction: float, flag_threshold: float = DEFAULT_SATURATION_FLAG_THRESHOLD
) -> bool:
    """Decide if there are enough overexposed pixels to warn the user about it.

    Parameters
    ----------
    saturated_fraction : `float`
        The percentage of maxed-out pixels found.
    flag_threshold : `float`, optional
        How much overexposure is 'too much'. Default is 0.1%. (Note: this
        is just a guess right now and might need to be adjusted later).

    Returns
    -------
    is_significant : `bool`
        True if the image is too bright, False if it's fine.
    """
    return saturated_fraction >= flag_threshold


# The largest value a pixel of a Siril-written stack can hold. A 16-bit raw
# frame counted in ADU always has some pixel above this, so an image whose
# brightest pixel is at or below it is a normalised stack, not a raw frame.
NORMALISED_STACK_MAXIMUM = 1.0


def is_normalised_stack_scale(data: np.ndarray) -> bool:
    """Tell a Siril-normalised stack (values 0 to 1) from a raw frame in ADU.

    Parameters
    ----------
    data : `numpy.ndarray`
        The pixels of the image.

    Returns
    -------
    is_normalised : `bool`
        True if the brightest finite pixel is at or below 1.0.
    """
    finite = data[np.isfinite(data)]
    return finite.size > 0 and float(finite.max()) <= NORMALISED_STACK_MAXIMUM


def find_saturation_plateau_ceiling(image: np.ndarray) -> float | None:
    """Find the value at which a stack's saturated pixels pile up, if they do.

    Parameters
    ----------
    image : `numpy.ndarray`
        A stacked image.

    Returns
    -------
    ceiling : `float` or `None`
        The median of the brightest SATURATION_CEILING_SAMPLE_PIXELS pixels
        when at least SATURATION_PLATEAU_MINIMUM_PIXELS pixels lie within
        SATURATION_PLATEAU_TOLERANCE of it and it is at least
        SATURATION_CEILING_MINIMUM_OVER_MEDIAN times the stack's median;
        `None` when there is no such pile (the stack is not saturated).
    """
    flat = np.asarray(image, dtype=np.float64).ravel()
    flat = flat[np.isfinite(flat)]
    if flat.size < SATURATION_PLATEAU_MINIMUM_PIXELS:
        return None
    brightest = np.partition(flat, -SATURATION_CEILING_SAMPLE_PIXELS)[-SATURATION_CEILING_SAMPLE_PIXELS:]
    ceiling = float(np.median(brightest))
    if ceiling <= 0 or ceiling < SATURATION_CEILING_MINIMUM_OVER_MEDIAN * float(np.median(flat)):
        return None
    pixels_near_ceiling = int(np.count_nonzero(flat >= ceiling * (1.0 - SATURATION_PLATEAU_TOLERANCE)))
    if pixels_near_ceiling < SATURATION_PLATEAU_MINIMUM_PIXELS:
        return None
    return ceiling


def compute_stack_saturated_pixel_fraction(data: np.ndarray, region: np.ndarray | None = None) -> float:
    """Calculate the fraction of a stack's pixels that are saturated.

    The ceiling is found from the whole stack, then pixels at or above
    SATURATION_MASK_FRACTION_OF_CEILING times it are counted, the same
    level the exposure-group stacking masks.

    Parameters
    ----------
    data : `numpy.ndarray`
        The whole stacked image, used to find the ceiling.
    region : `numpy.ndarray`, optional
        A cut-out of `data` to count in. Defaults to all of `data`.

    Returns
    -------
    saturated_fraction : `float`
        The fraction of pixels (0.0 to 1.0) at the saturation plateau, or
        0.0 if the stack has no plateau.
    """
    ceiling = find_saturation_plateau_ceiling(data)
    if ceiling is None:
        return 0.0
    counted = data if region is None else region
    return compute_saturated_pixel_fraction(
        np.asarray(counted, dtype=float), ceiling * SATURATION_MASK_FRACTION_OF_CEILING
    )


def resolve_saturation_threshold(camera_name: str | None) -> tuple[float, str]:
    """Find the level at which this camera's pixels count as saturated.

    The level is the camera profile's `pixel_saturation_level_adu`: its
    saturation threshold, capped at the clip ceiling. A profile can list a
    threshold above the value a clipped pixel actually reaches (its
    clip ceiling), which would make saturation impossible to detect. In that
    case the clip ceiling is used instead, and the source text says so. The
    default `SATURATION_ADU` is used only when no profile can be read.

    Parameters
    ----------
    camera_name : `str` or `None`
        The camera that took the frame.

    Returns
    -------
    threshold_adu : `float`
        Pixels at or above this value count as saturated.
    source : `str`
        A sentence saying where the level came from.
    """
    from astrometricslib.drivers.camera_profile_store import resolve_camera_profile

    try:
        profile = resolve_camera_profile(camera_name)
    except AstrometricsError as error:
        return float(
            SATURATION_ADU
        ), f"default {SATURATION_ADU:g} ADU (no camera profile could be read: {error})"
    threshold = float(profile.pixel_saturation_level_adu.value)
    if profile.is_generic_fallback:
        return (
            threshold,
            f"generic fallback camera profile, {threshold:g} ADU (camera {camera_name!r} is not listed)",
        )
    if not profile.saturation_threshold_can_be_reached:
        return threshold, (
            f"{profile.camera_name} profile clip ceiling, {threshold:g} ADU "
            "(its saturation threshold is above the ceiling and no pixel can reach it)"
        )
    return threshold, f"{profile.camera_name} profile saturation threshold, {threshold:g} ADU"


# A pixel is at the clip ceiling when it is within this fraction of it. Frames
# that saturate pile up at exactly one value, so a small tolerance only allows
# for the last count or two.
CLIP_CEILING_TOLERANCE_FRACTION = 0.02


@dataclass(frozen=True)
class ClipAndLinearityLevels:
    """The two pixel levels a camera profile gives for the target check.

    Attributes
    ----------
    clip_level_adu : `float`
        A pixel at or above this value counts as clipped: the profile's clip
        ceiling less `CLIP_CEILING_TOLERANCE_FRACTION` of it.
    linearity_level_adu : `float` or `None`
        A pixel at or above this value is outside the sensor's linear range
        (the profile's photometric linearity limit), or `None` when the
        profile has none.
    notes : `tuple` of `str`
        A sentence for each number that is not a measured one, or for a
        profile that is the generic fallback. The rows built from these levels
        are assumed, not measured, when this is not empty.
    """

    clip_level_adu: float
    linearity_level_adu: float | None
    notes: tuple[str, ...]

    @property
    def is_assumed(self) -> bool:
        """`bool`: Whether any level rests on a non-measured number."""
        return bool(self.notes)


def clip_and_linearity_levels(profile: CameraProfile) -> ClipAndLinearityLevels:
    """Read the clip level and the linearity limit from a camera profile.

    Parameters
    ----------
    profile : `CameraProfile`
        The camera's profile.

    Returns
    -------
    levels : `ClipAndLinearityLevels`
        The clip level (the ceiling less the tolerance), the linearity limit
        and a note for every level that is a datasheet value or an assumption.
        The generic fallback profile (used for a camera that is not listed)
        adds a note, so rows built from it are assumed.
    """
    notes: list[str] = []
    if profile.is_generic_fallback:
        notes.append("the camera is not listed, so the generic fallback camera profile's levels are used")
    ceiling = profile.clip_ceiling_adu
    if ceiling.provenance.kind != ProvenanceKind.MEASURED:
        notes.append(f"the camera profile's clip ceiling is {ceiling.provenance.kind.value}, not measured")
    limit = profile.photometric_linearity_limit_adu
    linearity_level = None
    if limit is not None:
        linearity_level = float(limit.value)
        if limit.provenance.kind != ProvenanceKind.MEASURED:
            notes.append(
                f"the camera profile's linearity limit is {limit.provenance.kind.value}, not measured"
            )
    return ClipAndLinearityLevels(
        clip_level_adu=float(ceiling.value) * (1.0 - CLIP_CEILING_TOLERANCE_FRACTION),
        linearity_level_adu=linearity_level,
        notes=tuple(notes),
    )
