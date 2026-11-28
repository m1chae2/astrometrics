"""Line up the stacked image of each exposure group before they are combined.

Siril registers the frames of one group to that group's own reference frame,
so two group stacks of the same field are not lined up with each other. On the
2026 NGC 2244, NGC 2903 and NGC 2403 sessions they were off by 6 to 7 pixels
(2.5, -6.4), (5.1, -6.2) and (0.6, 7.0), and combining them as they were would
smear every star. The group stacks are therefore moved onto one grid, the
grid of a reference group, before they are combined.

How a group is moved depends on what the group shows:

- Imaging stacks and spectral stacks with enough stars are lined up by Siril,
  which plate-solves each stack against the Gaia catalog and moves it onto the
  reference's grid (see `siril_group_alignment` and the Siril driver's
  `group_registration`). The move is exact for a rotation, a scale and the
  lens distortion, and it needs no setting of ours.
- A spectral stack that has too few stars for Siril to solve is shifted so its
  zero-order star (the undispersed image of the star, which the grating
  leaves in place) lands where the reference's does
  (`find_zero_order_position`).
- A stack with no stars at all, such as the Moon, is shifted by the offset that
  phase correlation finds (`measure_alignment`).

This module holds the shift measurements and the shift itself, the finder of
the zero-order star and the finder of bright star centers that other steps
also use. A group whose offset cannot be found is reported, with the reason,
so the caller can leave it out rather than blur the stack with it.

Everything here works on arrays and returns arrays; reading and writing files
is left to the caller.
"""

import logging
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter, maximum_filter
from scipy.ndimage import shift as shift_image
from skimage.registration import phase_cross_correlation

from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.foundation.parameter_context import record_basis, run_basis_value
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_bases import REFERENCE_SETUP_BASES, factor_for
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    RunBasis,
    TunedParameter,
)
from astrometricslib.pipelines.stacking.processing.group_position_map import GroupPositionMap
from astrometricslib.pipelines.stacking.processing.image_registration import COVERED_PIXEL_THRESHOLD
from astrometricslib.utilities.robust_statistics import robust_sigma

logger = logging.getLogger(__name__)

# Structure wider than this many pixels (background gradients, nebulosity) is
# removed before the offset is measured, so stars decide it. Stars in these
# stacks have a FWHM of about 3 px (2.8-3.8 px, see
# `documentation/evidence/stack_exposure_groups_20260920.json`); 8 px is a
# little under three
# FWHM, wide enough to leave a star's profile alone.
#
# Stands for a width wider than a star's profile, so it is a multiple of the
# star width and follows it.
ALIGNMENT_HIGH_PASS_SIGMA_PIXELS = TunedParameter(
    name="stacking.alignment_high_pass_sigma_pixels",
    default=factor_for(8.0, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description="Width above which structure is removed before the offset between group stacks is measured.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-D64",
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=8.0,
)

# After high-pass filtering, values are clipped at this many noise sigmas so
# that a few saturated stars do not decide the offset on their own.
ALIGNMENT_CLIP_SIGMAS = TunedParameter(
    name="stacking.alignment_clip_sigmas",
    default=10.0,
    unit="noise sigmas",
    description="Level, in noise sigmas, at which filtered values are clipped before the offset is measured.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D64",
)

# The offset is measured to 1/20 of a pixel. Real offsets between group
# stacks are several pixels, and a 0.05 px error blurs a star's profile by
# far less than the seeing does.
ALIGNMENT_UPSAMPLE_FACTOR = TunedParameter(
    name="stacking.alignment_upsample_factor",
    default=20,
    unit="× pixel precision",
    description="Factor by which the offset search is refined below one pixel.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D64",
)

# An offset smaller than this is not applied: resampling smooths the image, and
# a shift this small does not change where a star sits.
NEGLIGIBLE_SHIFT_PIXELS = TunedParameter(
    name="stacking.negligible_shift_pixels",
    default=0.05,
    unit="px",
    description="Offset below which a shift between group stacks is not applied.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D64",
)

# The spline order used when a group stack is moved or turned onto the
# reference: 3 (cubic), the sharpest order that keeps the star shape. A map
# of counts that must not overshoot passes order 1 by itself. A method
# choice, not tied to the equipment (STK-13).
#
# A group stack moved here has already been resampled once, when Siril
# aligned its frames, so this is a second resample. The measurement of
# 2026-11-15 shows what it costs: faint stars 0.4 to 2.1 percent wider, but
# the brightest (clipped) stars 19 to 27 percent wider on M 27 and 33 percent
# on M 57, from cubic ringing around their cores. Issue #192 removes the
# second resample by registering every frame to one reference in one pass.
GROUP_RESAMPLE_SPLINE_ORDER = TunedParameter(
    name="stacking.group_resample_spline_order",
    default=3,
    unit="spline order",
    description="Spline order used to resample a group stack when it is moved or turned.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-13",
)

# The offset is trusted when, after moving one stack onto the other, the two
# filtered images correlate at least this well. Measured on real pairs of
# stacks of the same field (NGC 2244, NGC 2903, NGC 2403, NGC 1893 and the
# halves of one group) the correlation was 0.55 to 0.96; on two stacks of
# different fields, where no true offset exists, it was 0.03 to 0.09. 0.3 sits
# between the two groups.
MINIMUM_ALIGNMENT_CORRELATION = TunedParameter(
    name="stacking.minimum_alignment_correlation",
    default=0.3,
    unit="correlation",
    description="Lowest correlation of the filtered stacks at which the measured offset is trusted.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-13",
)

# For a spectral target, the offset between two group stacks is measured on
# only the central share of each dimension, not the whole frame. A slitless
# spectrum's dispersed trail can run at any angle (see
# `SpectroscopyConfig.dispersion_orientation`/`dispersion_angle_degrees`), so
# its position within the frame cannot be predicted from the pipeline here,
# but the star that casts it is framed near the centre of the field the same
# way any target is. On a real stacked Albireo frame (3008 x 3008 px, see
# `logs/stack_AlbireoPhaseCorrTest4...log`) essentially all of the signal --
# the star's zero order and the brightest part of its trail -- fell within
# the central 50% of the frame; the rest was empty sky, whose noise pattern
# differs between exposure groups and would otherwise be free to dominate the
# correlation. Not yet validated on other spectral sessions.
SPECTRAL_ALIGNMENT_CENTER_CROP_FRACTION = TunedParameter(
    name="stacking.spectral_alignment_center_crop_fraction",
    default=0.5,
    unit="fraction of frame size",
    description="Central share of each dimension used to align spectral group stacks.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D67",
)

# Aligning spectral group stacks on the zero-order star instead of on the
# image as a whole. The dispersed trail is a long streak, so shifting an image
# along it barely changes how well two stacks match, and a saturated long
# exposure and a faint short one look too different for the correlation to
# lock on. On the 2026 Vega group stacks the 0.05 s group's true offset from
# the 5 s group was about (+6 rows, +26 columns), where the correlation
# reported (-39, -5), and the 0.1 s group's came out 395 px off; the star's own
# position in each stack agreed to 1 px across the 1 s to 5 s groups. The star
# is found as the brightest spot of the image smoothed by this many pixels
# (seeing spreads the star over a few pixels).
#
# Stands for a blur sized to the zero-order star image, so it is a multiple of
# the star width and follows it.
ZERO_ORDER_SMOOTHING_SIGMA_PIXELS = TunedParameter(
    name="stacking.zero_order_smoothing_sigma_pixels",
    default=factor_for(2.0, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description="Width of the smoothing applied before the zero-order star is looked for.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA, EquipmentKind.FILTER_OR_GRATING}),
    register_entry="STK-D67",
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=2.0,
)

# The star is looked for only within this many pixels of the centre of the
# image, because a saturated long-exposure stack has a second spot as bright as
# the star: its dispersed trail, about 350 px below the star. On the Vega 5 s
# stack the brightest smoothed spot in the central half of the frame was the
# trail (0.76 of full scale, against 0.77 for the star), and the trail beat the
# star outright in the 1 s stack. The spectroscopy target is framed near the
# image centre (the same assumption `SPECTRAL_ALIGNMENT_CENTER_CROP_FRACTION`
# makes): on the Vega nights the star sat within 35 px of it. 150 px covers
# four times that and stays well inside the 350 px to the trail. A target
# framed farther from the centre is not found, and the images are correlated
# instead.
#
# Stands for the framing accuracy of the pointing, which is a length on the
# sky, so it takes fewer pixels on a coarser plate scale (the angle divided
# by the sky size of one pixel).
ZERO_ORDER_SEARCH_HALF_WIDTH_PIXELS = TunedParameter(
    name="stacking.zero_order_search_half_width_pixels",
    default=factor_for(150, RunBasis.PLATE_SCALE_ARCSEC_PER_PX, inverse=True),
    unit="px (an angle on the sky ÷ plate scale)",
    description="Distance from the image centre within which the zero-order star is looked for.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA, EquipmentKind.FILTER_OR_GRATING}),
    register_entry="STK-17",
    basis=RunBasis.PLATE_SCALE_ARCSEC_PER_PX,
    basis_inverse=True,
    reference_value=150,
)

# The star's centre is the brightness-weighted centre of the pixels within this
# many pixels of the brightest spot that are at least
# ZERO_ORDER_CENTROID_FRACTION_OF_PEAK of it. A saturated star is a flat
# plateau, where the brightest single pixel is arbitrary but the centre of the
# plateau is not. The window is wider than a saturated plateau (about 15 px
# across on the Vega 5 s stack, unmeasured elsewhere) and far narrower than the
# 40 px separation below.
#
# Stands for the size of the zero-order star image, so it is a multiple of the
# star width and follows it.
ZERO_ORDER_CENTROID_HALF_WINDOW_PIXELS = TunedParameter(
    name="stacking.zero_order_centroid_half_window_pixels",
    default=factor_for(25, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description="Half width of the window in which the zero-order star's centre is measured.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA, EquipmentKind.FILTER_OR_GRATING}),
    register_entry="STK-D67",
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=25,
)
ZERO_ORDER_CENTROID_FRACTION_OF_PEAK = TunedParameter(
    name="stacking.zero_order_centroid_fraction_of_peak",
    default=0.5,
    unit="fraction of peak",
    description=(
        "Share of the peak below which pixels are ignored when the zero-order star's centre is measured."
    ),
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D67",
)

# The star is only believed when nothing else at least this far away is
# brighter than ZERO_ORDER_MAX_RIVAL_FRACTION of it. A stack with two similar
# bright spots (a double star, or the trail as bright as the star) is
# ambiguous, and then the caller falls back to correlating the images.
# Unvalidated choices: on the Vega group stacks the brightest spot away from
# the star was well under half the star's brightness.
#
# Stands for the distance beyond which a spot is another object and not the
# star's wings, so it is a multiple of the star width and follows it.
ZERO_ORDER_RIVAL_SEPARATION_PIXELS = TunedParameter(
    name="stacking.zero_order_rival_separation_pixels",
    default=factor_for(40, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description="Distance beyond which a brighter spot makes the zero-order star ambiguous.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-17",
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=40,
)
ZERO_ORDER_MAX_RIVAL_FRACTION = TunedParameter(
    name="stacking.zero_order_max_rival_fraction",
    default=0.5,
    unit="fraction of peak",
    description=(
        "Largest brightness of a rival spot, as a share of the star, that still lets the star be believed."
    ),
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-17",
)

# Extended targets (a globular cluster, a galaxy, a nebula). The point-source
# search above fails on them: the target is a broad glow, not one spot, so the
# smoothed image has several bright spots of similar height. On the six M 13
# spectroscopy frames (five single frames and the stack) the brightest spot
# within the search window sits at about row 1491, column 1476, 845 ADU
# (frame 024), and a spot 67 px away at row 1486, column 1543 reaches 536 ADU.
# That is 0.63 of the brightest (0.47 once the sky is removed), above the 0.5
# limit, so it is the rival test, not the peak test, that rejects the cluster.
# The extended path instead averages the frame in blocks of
# EXTENDED_TARGET_BLOCK_PIXELS (cheap, and it suppresses noise), then smooths
# with a Gaussian about as wide as the target's bright part. At that scale the
# target is one smooth hill. The width is the larger of two:
#
# - EXTENDED_TARGET_SMOOTHING_SIZE_FRACTION of the target's catalog size (the
#   run's target-size basis, SIMBAD's major axis) in pixels. 25 px worked on
#   M 13, whose catalog size is 33 arcminutes, 1034 px at 1.915 arcsec/px, so
#   the share is 25 / 1034.
# - EXTENDED_TARGET_SMOOTHING_FLOOR_PIXELS, 4 star widths, so a small target
#   (M 57 is 1.2 arcminutes, which would give under 1 px) is still smoothed
#   well past single stars.
#
# A target with no catalog size (most stars) keeps
# EXTENDED_TARGET_SMOOTHING_SIGMA_PIXELS, the 25 px chosen on M 13, which is
# an angle on the sky and so takes fewer pixels on a coarser plate scale.
EXTENDED_TARGET_BLOCK_PIXELS = TunedParameter(
    name="stacking.extended_target_block_pixels",
    default=4,
    unit="px",
    description="Size of the blocks averaged before the centre of an extended target is searched for.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D66",
)
EXTENDED_TARGET_SMOOTHING_SIGMA_PIXELS = TunedParameter(
    name="stacking.extended_target_smoothing_sigma_pixels",
    default=factor_for(25.0, RunBasis.PLATE_SCALE_ARCSEC_PER_PX, inverse=True),
    unit="px (an angle on the sky ÷ plate scale)",
    description="Smoothing width for an extended target whose catalog size is not known.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-18",
    basis=RunBasis.PLATE_SCALE_ARCSEC_PER_PX,
    basis_inverse=True,
    reference_value=25.0,
)
EXTENDED_TARGET_SMOOTHING_SIZE_FRACTION = TunedParameter(
    name="stacking.extended_target_smoothing_size_fraction",
    default=25.0 / 1034.0,
    unit="× the catalog size in pixels",
    description="Smoothing width for an extended target, as a share of its catalog size.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-18",
)
EXTENDED_TARGET_SMOOTHING_FLOOR_PIXELS = TunedParameter(
    name="stacking.extended_target_smoothing_floor_pixels",
    default=factor_for(4.0 * REFERENCE_SETUP_BASES[RunBasis.STAR_WIDTH_PX], RunBasis.STAR_WIDTH_PX),
    unit="px (× star width)",
    description="Narrowest smoothing width for an extended target with a catalog size.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-D66",
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=4.0 * REFERENCE_SETUP_BASES[RunBasis.STAR_WIDTH_PX],
)


def extended_target_smoothing_sigma_pixels() -> float:
    """Work out the smoothing width used to find an extended target.

    Returns
    -------
    sigma_pixels : `float`
        The larger of `EXTENDED_TARGET_SMOOTHING_SIZE_FRACTION` of the
        target's catalog size in pixels and
        `EXTENDED_TARGET_SMOOTHING_FLOOR_PIXELS`; with no catalog size known,
        `EXTENDED_TARGET_SMOOTHING_SIGMA_PIXELS`. A plate scale the run does
        not know is the reference setup's.
    """
    size_arcsec = run_basis_value(RunBasis.TARGET_SIZE_ARCSEC)
    if size_arcsec is None:
        return float(resolve(EXTENDED_TARGET_SMOOTHING_SIGMA_PIXELS).value)
    scale = (
        run_basis_value(RunBasis.PLATE_SCALE_ARCSEC_PER_PX)
        or REFERENCE_SETUP_BASES[RunBasis.PLATE_SCALE_ARCSEC_PER_PX]
    )
    sized = float(resolve(EXTENDED_TARGET_SMOOTHING_SIZE_FRACTION).value) * size_arcsec / scale
    return max(float(resolve(EXTENDED_TARGET_SMOOTHING_FLOOR_PIXELS).value), sized)


# Two smoothed peaks count as independent when no brighter pixel lies within
# this many smoothing sigmas of either one (two sigmas is 50 px at the width
# above). Peaks closer than that are shoulders of one hill, not rivals.
EXTENDED_TARGET_INDEPENDENT_PEAK_SIGMAS = TunedParameter(
    name="stacking.extended_target_independent_peak_sigmas",
    default=2.0,
    unit="smoothing sigmas",
    description="Distance, in smoothing sigmas, beyond which two peaks count as independent.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D66",
)

# The brightest smoothed peak must lie inside the same central search window as
# the point-source search (ZERO_ORDER_SEARCH_HALF_WIDTH_PIXELS). Rivals are
# looked for twice as far out, so a target near the edge of the window is not
# accepted just because its competitor lies a little beyond it. Rivals
# farther than 300 px are ignored: on M 13 other smoothed peaks of similar
# height lie 380 px (row 1502, column 1122) and 520 px away, so a wider
# rival window would reject every M 13 frame. The width is worked out when it
# is used (`extended_target_rival_half_width_pixels`), so it follows the search
# window when that follows the plate scale.
EXTENDED_TARGET_RIVAL_WINDOW_FACTOR = 2


def extended_target_rival_half_width_pixels() -> float:
    """Give the half width of the window in which rival peaks are looked for.

    Returns
    -------
    half_width : `float`
        Twice the half width of the central search window, in pixels
        (`ZERO_ORDER_SEARCH_HALF_WIDTH_PIXELS`), so it is 300 px on the
        reference setup.
    """
    return EXTENDED_TARGET_RIVAL_WINDOW_FACTOR * resolve(ZERO_ORDER_SEARCH_HALF_WIDTH_PIXELS).value


# The peak is accepted only when the tallest independent rival is at most this
# fraction of it, both measured above the frame's sky level (the median of the
# block-averaged image). On the six M 13 frames the rival reached 0.078 to
# 0.088 of the peak at sigmas of 20 to 30 px (no rival at all in the 300 px
# window is also possible and passes). One third is about four times the worst
# measured value and well below the 1.0 of two equal targets, which the
# synthetic two-blob test rejects. Only M 13 has been measured.
EXTENDED_TARGET_MAX_RIVAL_FRACTION = TunedParameter(
    name="stacking.extended_target_max_rival_fraction",
    default=1.0 / 3.0,
    unit="fraction of peak",
    description=(
        "Largest height of an independent rival peak, as a share of the chosen peak, that still "
        "lets it be accepted."
    ),
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-18",
)

# The centre is the brightness-weighted centre of the smoothed pixels, within
# the independent-peak distance of the peak, that are at least this fraction of
# the peak (above sky). The block grid steps 4 px, so the peak pixel alone
# jumps between neighbouring blocks: on frame 024 it moved by 4 px between
# sigmas of 20 and 25 px, while the weighted centre moved by 0.2 px.
EXTENDED_TARGET_CENTROID_FRACTION_OF_PEAK = TunedParameter(
    name="stacking.extended_target_centroid_fraction_of_peak",
    default=0.5,
    unit="fraction of peak",
    description=(
        "Share of the peak below which smoothed pixels are ignored when an extended target's "
        "centre is measured."
    ),
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D66",
)

# Finding the bright stars of a stacked image (`detect_star_centroids`).
#
# Other steps use the centers of the brightest stars of a stack: the group
# gains compare their flux between groups, the aperture placement keeps clear
# of them and the noise measurement leaves them out. The constants below were
# chosen when these stars were also matched between two group stacks, on M 57
# and M 27 (2026-10); no other targets have been checked.

# The brightest this many stars of each stack are returned. On the three pairs
# of M 57 and M 27 stacks, 368 to 375 of 400 found a partner (92 to 94%).
REFINEMENT_STAR_COUNT = TunedParameter(
    name="stacking.refinement_star_count",
    default=400,
    unit="stars",
    description="Number of the brightest stars of a stack that the star finder returns.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D65",
)

# A star must rise this many robust sigmas above the smoothed,
# background-subtracted image. At 12 sigma the M 57 60 s stack had about 7,000
# candidates, far more than are used, so the faintest of the 400 chosen are
# still well above the noise.
REFINEMENT_DETECTION_SIGMAS = TunedParameter(
    name="stacking.refinement_detection_sigmas",
    default=12.0,
    unit="robust sigmas",
    description="Height, in robust sigmas, a star must rise above the smoothed background to be used.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D65",
)

# Stars above this share of the image's 99.99th percentile are skipped: a
# saturated star is a flat plateau, and the centre of a plateau is not where
# the star is once another stack has replaced its core.
REFINEMENT_SATURATION_FRACTION = TunedParameter(
    name="stacking.refinement_saturation_fraction",
    default=0.9,
    unit="fraction of peak",
    description="Share of the image's 99.99th percentile above which a star is skipped as saturated.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D65",
)

# Stars closer than this to the frame edge are skipped, so the centroid window
# stays inside the frame and clear of the zero-filled border a shift leaves.
#
# Stands for the pointing shift between nights, which is a length on the sky;
# it follows the plate scale (the sky size of one pixel).
REFINEMENT_EDGE_MARGIN_PIXELS = TunedParameter(
    name="stacking.refinement_edge_margin_pixels",
    default=factor_for(60, RunBasis.PLATE_SCALE_ARCSEC_PER_PX, inverse=True),
    unit="px (an angle on the sky ÷ plate scale)",
    description="Distance from the frame edge within which stars are skipped.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-D65",
    basis=RunBasis.PLATE_SCALE_ARCSEC_PER_PX,
    basis_inverse=True,
    reference_value=60,
)

# Half the width of the window in which a star's centre is measured, and the
# share of the window's peak below which a pixel is ignored. A star with a
# FWHM of 2 to 3 px has almost all of its light within 4 px.
#
# Stands for the part of a star that holds almost all its light, so it is a
# multiple of the star width and follows it.
REFINEMENT_CENTROID_HALF_WINDOW_PIXELS = TunedParameter(
    name="stacking.refinement_centroid_half_window_pixels",
    default=factor_for(4, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description="Half width of the window in which a star's centre is measured.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-D65",
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=4,
)
REFINEMENT_CENTROID_FLOOR_FRACTION = TunedParameter(
    name="stacking.refinement_centroid_floor_fraction",
    default=0.2,
    unit="fraction of peak",
    description="Share of the window's peak below which a pixel is ignored in the centre measurement.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D65",
)


@dataclass
class AlignmentResult:
    """How one group stack was lined up with the reference.

    The move is one of three kinds, named by `method`: Siril's sky-based
    move (`position_map` holds it), a shift onto the zero-order star, or a
    shift found by correlation. The reference group is not moved.

    Attributes
    ----------
    shift_rows_pixels : `float`
        How far the image center moves along the rows (the y axis) to line up
        with the reference. Positive moves it toward higher rows. For a Siril
        move, the shift of the image center.
    shift_columns_pixels : `float`
        How far the image center moves along the columns (the x axis).
    correlation : `float`
        How well the two filtered images agree once the image is moved by a
        correlation shift. Near one is a match; near zero means the offset
        found is not real. Zero for the other two methods, which do not use
        it.
    is_star_based : `bool`
        `True` when the offset is the difference between the two images'
        zero-order star positions (see `find_zero_order_position`) rather
        than the result of correlating the images. Such an offset is trusted
        without the correlation test, since the images may look very
        different (a saturated long exposure against a faint short one).
    method : `str`
        How the move was found: ``"siril"`` (plate solutions), ``"zero_order"``
        (the zero-order star's position), ``"correlation"`` (phase
        correlation of the images) or ``"none"`` for a reference that could
        not be solved and so was left as it is.
    position_map : `GroupPositionMap` or `None`
        The move Siril made, as the place to read in the group's own stack for
        each pixel of the common grid. `None` for a shift.
    rotation_degrees : `float`
        How far a Siril move turns the image about its center, in degrees.
        Positive turns from the +column axis toward the +row axis. A half
        turn is a meridian flip between the sessions. Zero for a shift.
    scale : `float`
        The factor by which a Siril move enlarges the image about its center.
        One for a shift.
    stars_found : `int` or `None`
        How many stars Siril found in the stack, when Siril ran on it.
    matched_stars : `int` or `None`
        How many of them Siril matched to the Gaia catalog.
    fit_residual_arcsec : `float` or `None`
        How far a matched star lies from its catalog position, root mean
        square, in arcseconds.
    fit_residual_pixels : `float` or `None`
        The same distance in the group stack's own pixels.
    fit_order : `int` or `None`
        The order of the distortion fit Siril kept. It falls below 3 when the
        stack holds too few stars for a cubic fit.
    is_reference : `bool`
        `True` for the group the others are lined up with.
    failure_reason : `str` or `None`
        Why the group could not be lined up, when Siril or the correlation
        failed. The reason a group is left out cites it.
    siril_reason : `str` or `None`
        Why Siril did not line the group up, when another method did (a
        spectral group moved by its zero-order star). `None` otherwise.
    """

    shift_rows_pixels: float
    shift_columns_pixels: float
    correlation: float
    is_star_based: bool = False
    method: str = "correlation"
    position_map: GroupPositionMap | None = None
    rotation_degrees: float = 0.0
    scale: float = 1.0
    stars_found: int | None = None
    matched_stars: int | None = None
    fit_residual_arcsec: float | None = None
    fit_residual_pixels: float | None = None
    fit_order: int | None = None
    is_reference: bool = False
    failure_reason: str | None = None
    siril_reason: str | None = None

    @property
    def trusted(self) -> bool:
        """Whether the move is good enough to use.

        Returns
        -------
        trusted : `bool`
            `True` for a Siril move (Siril only gives one for a stack it
            solved) and for a zero-order offset. A correlation shift is
            trusted at or above `MINIMUM_ALIGNMENT_CORRELATION`. A result that
            carries a failure reason is never trusted.
        """
        if self.failure_reason is not None:
            return False
        return (
            self.position_map is not None
            or self.is_star_based
            or self.correlation >= resolve(MINIMUM_ALIGNMENT_CORRELATION).value
        )

    def position_map_for(self, image_shape: tuple[int, ...]) -> GroupPositionMap:
        """Give the move as a position map, whatever its method.

        Parameters
        ----------
        image_shape : `tuple` [`int`, ...]
            The moving image's shape; its last two numbers are the rows and
            columns.

        Returns
        -------
        position_map : `GroupPositionMap`
            The Siril move, or the shift written as a map.
        """
        if self.position_map is not None:
            return self.position_map
        return GroupPositionMap.from_shift(
            (image_shape[-2], image_shape[-1]), self.shift_rows_pixels, self.shift_columns_pixels
        )


def _to_plane(image: np.ndarray) -> np.ndarray:
    """Reduce a possibly colour image to one plane for measuring.

    Returns
    -------
    plane : `numpy.ndarray`
        The image itself when it is 2-D, otherwise the mean over its first
        (colour) axis, as `float32`.
    """
    array = np.asarray(image, dtype=np.float32)
    return array if array.ndim == 2 else array.mean(axis=0)


def _center_crop(plane: np.ndarray, crop_fraction: float) -> np.ndarray:
    """Take the central share of a 2-D array, along each axis separately.

    Parameters
    ----------
    plane : `numpy.ndarray`
        A 2-D array. It does not need to be square.
    crop_fraction : `float`
        The share of each dimension to keep, from 0 (exclusive) to 1. A
        value of 0.5 keeps the central half of the rows and the central half
        of the columns.

    Returns
    -------
    cropped : `numpy.ndarray`
        A view onto the central region of `plane`.
    """
    height, width = plane.shape
    row_margin = round(height * (1.0 - crop_fraction) / 2.0)
    column_margin = round(width * (1.0 - crop_fraction) / 2.0)
    return plane[row_margin : height - row_margin, column_margin : width - column_margin]


def _filtered(plane: np.ndarray) -> np.ndarray:
    """Remove wide structure and tame the brightest stars.

    Returns
    -------
    filtered : `numpy.ndarray`
        The plane minus its Gaussian blur, clipped at
        `ALIGNMENT_CLIP_SIGMAS` robust sigmas.
    """
    alignment_clip_sigmas = resolve(ALIGNMENT_CLIP_SIGMAS).value
    filtered = plane - gaussian_filter(plane, resolve(ALIGNMENT_HIGH_PASS_SIGMA_PIXELS).value)
    sigma = robust_sigma(filtered)
    if sigma > 0:
        np.clip(
            filtered,
            -alignment_clip_sigmas * sigma,
            alignment_clip_sigmas * sigma,
            out=filtered,
        )
    return filtered


def find_zero_order_position(plane: np.ndarray) -> tuple[float, float] | None:
    """Find the star that casts a slitless spectrum in a stacked image.

    Parameters
    ----------
    plane : `numpy.ndarray`
        A 2-D stacked image (already cropped to where the star can be).

    Returns
    -------
    position : `tuple` [`float`, `float`] or `None`
        The star's (row, column), or `None` when the centre of the image
        has no bright spot, or has a second spot too bright to tell which is
        the star (see `ZERO_ORDER_MAX_RIVAL_FRACTION`).
    """
    zero_order_search_half_width_pixels = resolve(ZERO_ORDER_SEARCH_HALF_WIDTH_PIXELS).value
    smooth = gaussian_filter(
        np.nan_to_num(np.asarray(plane, dtype=np.float32)), resolve(ZERO_ORDER_SMOOTHING_SIGMA_PIXELS).value
    )
    rows, columns = np.ogrid[: smooth.shape[0], : smooth.shape[1]]
    in_search_window = (np.abs(rows - (smooth.shape[0] - 1) / 2.0) <= zero_order_search_half_width_pixels) & (
        np.abs(columns - (smooth.shape[1] - 1) / 2.0) <= zero_order_search_half_width_pixels
    )
    searched = np.where(in_search_window, smooth, 0.0)
    peak_row, peak_column = np.unravel_index(int(np.argmax(searched)), searched.shape)
    peak = float(searched[peak_row, peak_column])
    if peak <= 0:
        return None
    distance_squared = (rows - peak_row) ** 2 + (columns - peak_column) ** 2
    far_away = distance_squared > resolve(ZERO_ORDER_RIVAL_SEPARATION_PIXELS).value ** 2
    rival = float(searched[far_away & in_search_window].max(initial=0.0))
    if rival > resolve(ZERO_ORDER_MAX_RIVAL_FRACTION).value * peak:
        return None
    half = resolve(ZERO_ORDER_CENTROID_HALF_WINDOW_PIXELS).value
    row_slice = slice(max(peak_row - half, 0), min(peak_row + half + 1, smooth.shape[0]))
    column_slice = slice(max(peak_column - half, 0), min(peak_column + half + 1, smooth.shape[1]))
    window = smooth[row_slice, column_slice]
    weights = np.where(window >= resolve(ZERO_ORDER_CENTROID_FRACTION_OF_PEAK).value * peak, window, 0.0)
    window_rows, window_columns = np.mgrid[row_slice, column_slice]
    total = float(weights.sum())
    return (
        float((weights * window_rows).sum() / total),
        float((weights * window_columns).sum() / total),
    )


@dataclass(frozen=True)
class ZeroOrderPosition:
    """Where the zero-order target is and which search found it.

    Attributes
    ----------
    row : `float`
        The target's row (the y position), in pixels.
    column : `float`
        The target's column (the x position), in pixels.
    is_extended_target : `bool`
        `True` when the point-source search found no single clear spot and
        the broad-glow search (see `find_extended_zero_order_position`)
        supplied the position. Such a position marks the middle of a glow
        tens of pixels wide, so it is good to a few pixels, not a fraction
        of a pixel. `False` for a point-source position.
    """

    row: float
    column: float
    is_extended_target: bool = False

    @property
    def row_column(self) -> tuple[float, float]:
        """Give the position as a (row, column) pair.

        Returns
        -------
        position : `tuple` [`float`, `float`]
            The row and the column.
        """
        return (self.row, self.column)


def find_extended_zero_order_position(plane: np.ndarray) -> tuple[float, float] | None:
    """Find the centre of an extended target, such as a globular cluster.

    The steps are:

    1. Average the image in blocks of `EXTENDED_TARGET_BLOCK_PIXELS`.
    2. Subtract the median of the blocks (the sky) and smooth with a Gaussian
       of `extended_target_smoothing_sigma_pixels`.
    3. List the local maxima: smoothed pixels no lower than any pixel within
       `EXTENDED_TARGET_INDEPENDENT_PEAK_SIGMAS` smoothing widths.
    4. Take the brightest local maximum inside the central search window
       (`ZERO_ORDER_SEARCH_HALF_WIDTH_PIXELS`).
    5. Find the brightest other local maximum inside the wider rival window
       (`extended_target_rival_half_width_pixels`).
    6. Refuse the peak when the rival is brighter than
       `EXTENDED_TARGET_MAX_RIVAL_FRACTION` of it.
    7. Return the brightness-weighted centre of the pixels around the peak
       that are at least `EXTENDED_TARGET_CENTROID_FRACTION_OF_PEAK` of it.

    A smooth sky gradient has no local maximum inside the window, so it gives
    `None`, as does a blank image or a frame with two equally bright targets.

    Parameters
    ----------
    plane : `numpy.ndarray`
        A 2-D image. The target is expected near its centre.

    Returns
    -------
    position : `tuple` [`float`, `float`] or `None`
        The target's (row, column), or `None` when the window has no peak or
        a rival is too bright for the peak to be trusted.
    """
    zero_order_search_half_width_pixels = resolve(ZERO_ORDER_SEARCH_HALF_WIDTH_PIXELS).value
    smoothing_sigma_pixels = extended_target_smoothing_sigma_pixels()
    block = resolve(EXTENDED_TARGET_BLOCK_PIXELS).value
    image = np.nan_to_num(np.asarray(plane, dtype=np.float32))
    block_rows, block_columns = image.shape[0] // block, image.shape[1] // block
    if block_rows < 1 or block_columns < 1:
        return None
    blocks = (
        image[: block_rows * block, : block_columns * block]
        .reshape(block_rows, block, block_columns, block)
        .mean(axis=(1, 3))
    )
    smooth = gaussian_filter(blocks, smoothing_sigma_pixels / block) - float(np.median(blocks))
    reach_blocks = round(
        resolve(EXTENDED_TARGET_INDEPENDENT_PEAK_SIGMAS).value * smoothing_sigma_pixels / block
    )
    is_peak = (maximum_filter(smooth, size=2 * reach_blocks + 1) == smooth) & (smooth > 0)
    peak_rows, peak_columns = np.nonzero(is_peak)
    heights = smooth[peak_rows, peak_columns]
    # Block (i, j) covers pixels block * i to block * i + block - 1.
    rows_px = peak_rows * block + (block - 1) / 2.0
    columns_px = peak_columns * block + (block - 1) / 2.0
    away_rows = np.abs(rows_px - (image.shape[0] - 1) / 2.0)
    away_columns = np.abs(columns_px - (image.shape[1] - 1) / 2.0)
    in_search_window = (away_rows <= zero_order_search_half_width_pixels) & (
        away_columns <= zero_order_search_half_width_pixels
    )
    if not in_search_window.any():
        return None
    best = int(np.nonzero(in_search_window)[0][np.argmax(heights[in_search_window])])
    rival_half_width_pixels = extended_target_rival_half_width_pixels()
    in_rival_window = (
        (away_rows <= rival_half_width_pixels)
        & (away_columns <= rival_half_width_pixels)
        & (np.arange(heights.size) != best)
    )
    rival = float(heights[in_rival_window].max(initial=0.0))
    if rival > resolve(EXTENDED_TARGET_MAX_RIVAL_FRACTION).value * heights[best]:
        return None
    row_slice = slice(max(peak_rows[best] - reach_blocks, 0), peak_rows[best] + reach_blocks + 1)
    column_slice = slice(max(peak_columns[best] - reach_blocks, 0), peak_columns[best] + reach_blocks + 1)
    window = smooth[row_slice, column_slice]
    weights = np.where(
        window >= resolve(EXTENDED_TARGET_CENTROID_FRACTION_OF_PEAK).value * heights[best], window, 0.0
    )
    window_rows, window_columns = np.mgrid[row_slice, column_slice]
    total = float(weights.sum())
    return (
        float((weights * window_rows).sum() / total) * block + (block - 1) / 2.0,
        float((weights * window_columns).sum() / total) * block + (block - 1) / 2.0,
    )


def locate_zero_order(plane: np.ndarray) -> ZeroOrderPosition | None:
    """Find the zero-order target, trying a point source first.

    This first runs `find_zero_order_position`, whose behaviour is unchanged.
    When that finds no single clear spot, it runs
    `find_extended_zero_order_position`. The result says which search
    supplied the position, so a caller can treat a few-pixel position
    differently from a sub-pixel one.

    Parameters
    ----------
    plane : `numpy.ndarray`
        A 2-D image (already cropped to where the target can be).

    Returns
    -------
    position : `ZeroOrderPosition` or `None`
        The position and whether it came from the extended-target search, or
        `None` when neither search finds a clear target.
    """
    point = find_zero_order_position(plane)
    if point is not None:
        return ZeroOrderPosition(point[0], point[1], is_extended_target=False)
    extended = find_extended_zero_order_position(plane)
    if extended is None:
        return None
    return ZeroOrderPosition(extended[0], extended[1], is_extended_target=True)


def detect_star_centroids(
    plane: np.ndarray, count: int | None = None, skip_saturated: bool = True
) -> np.ndarray:
    """Find the centres of the brightest isolated stars in a stacked image.

    Parameters
    ----------
    plane : `numpy.ndarray`
        A 2-D stacked image.
    count : `int`, optional
        How many of the brightest stars to return.
    skip_saturated : `bool`, optional
        Leave out the stars above `REFINEMENT_SATURATION_FRACTION` of the
        image's 99.99th percentile. The default is `True`. Set it to `False`
        to ask whether a star is present at a place: the brightest stars of a
        single frame are cut by this limit, but the same stars in a stack of
        many frames are not, so a check that a stack's stars are in a frame
        must keep them.

    Returns
    -------
    centroids : `numpy.ndarray`
        An array of shape ``(n, 2)`` with each star's (row, column), brightest
        first. Saturated stars (unless `skip_saturated` is `False`), and stars
        near the frame edge, are left out (see
        `REFINEMENT_SATURATION_FRACTION` and
        `REFINEMENT_EDGE_MARGIN_PIXELS`). Empty when no star is found.
    """
    if count is None:
        count = resolve(REFINEMENT_STAR_COUNT).value
    refinement_centroid_floor_fraction = resolve(REFINEMENT_CENTROID_FLOOR_FRACTION).value
    image = np.nan_to_num(np.asarray(plane, dtype=np.float64))
    flat = image - gaussian_filter(image, 20)
    smooth = gaussian_filter(flat, 1.5)
    sigma = robust_sigma(smooth)
    if sigma <= 0:
        return np.empty((0, 2))
    top = float(np.percentile(image, 99.99))
    is_peak = (
        (maximum_filter(smooth, size=15) == smooth)
        & (smooth > resolve(REFINEMENT_DETECTION_SIGMAS).value * sigma)
        & (image < resolve(REFINEMENT_SATURATION_FRACTION).value * top if skip_saturated else True)
    )
    rows, columns = np.nonzero(is_peak)
    margin = resolve(REFINEMENT_EDGE_MARGIN_PIXELS).value
    inside = (
        (rows > margin)
        & (rows < image.shape[0] - margin)
        & (columns > margin)
        & (columns < image.shape[1] - margin)
    )
    rows, columns = rows[inside], columns[inside]
    brightest_first = np.argsort(smooth[rows, columns])[::-1][:count]
    half = resolve(REFINEMENT_CENTROID_HALF_WINDOW_PIXELS).value
    centroids = []
    for row, column in zip(rows[brightest_first], columns[brightest_first], strict=True):
        window = flat[row - half : row + half + 1, column - half : column + half + 1]
        weights = np.clip(window - refinement_centroid_floor_fraction * window.max(), 0.0, None)
        total = float(weights.sum())
        if total <= 0:
            continue
        window_rows, window_columns = np.mgrid[row - half : row + half + 1, column - half : column + half + 1]
        centroids.append((
            float((weights * window_rows).sum() / total),
            float((weights * window_columns).sum() / total),
        ))
    return np.array(centroids).reshape(-1, 2)


def measure_alignment(
    reference: np.ndarray,
    image: np.ndarray,
    crop_fraction: float | None = None,
    prefer_star_position: bool = False,
) -> AlignmentResult:
    """Find the shift that puts `image` onto `reference`.

    Parameters
    ----------
    reference : `numpy.ndarray`
        The stack to line up with, 2-D or colour (channels first).
    image : `numpy.ndarray`
        The stack to move, the same shape as `reference`.
    crop_fraction : `float` or `None`, optional
        When given, only the central share of each dimension (see
        `_center_crop`) is used to measure the offset, though the offset
        found still applies to the whole image. Cropping to the same window
        in both images does not change the offset between them; it only
        keeps frame edges and empty sky from influencing it. `None` measures
        on the whole frame, as before.
    prefer_star_position : `bool`, optional
        When `True`, the offset is first taken from the zero-order star's
        position in each image (see `find_zero_order_position`), which
        works when the two images look very different. If either image has
        no clear star, the images are correlated as usual.

    Returns
    -------
    result : `AlignmentResult`
        The shift and how far it can be trusted.

    Raises
    ------
    InvalidArgumentError
        If the two images do not have the same shape.
    """
    if np.shape(reference) != np.shape(image):
        raise InvalidArgumentError("The images to align must have the same shape.")
    reference_plane = _to_plane(reference)
    image_plane = _to_plane(image)
    if crop_fraction is not None:
        reference_plane = _center_crop(reference_plane, crop_fraction)
        image_plane = _center_crop(image_plane, crop_fraction)
    fixed = _filtered(reference_plane)
    moving = _filtered(image_plane)
    star_positions = None
    if prefer_star_position:
        reference_star = find_zero_order_position(reference_plane)
        image_star = find_zero_order_position(image_plane)
        if reference_star is not None and image_star is not None:
            star_positions = (reference_star, image_star)
    if star_positions is not None:
        shift = np.array([
            star_positions[0][0] - star_positions[1][0],
            star_positions[0][1] - star_positions[1][1],
        ])
    else:
        shift, _, _ = phase_cross_correlation(
            fixed, moving, upsample_factor=resolve(ALIGNMENT_UPSAMPLE_FACTOR).value, normalization=None
        )
    is_star_based = star_positions is not None
    method = "zero_order" if is_star_based else "correlation"
    moved = shift_image(moving, shift, order=1, mode="constant", cval=0.0)
    covered = (
        shift_image(np.ones_like(moving), shift, order=1, mode="constant", cval=0.0) > COVERED_PIXEL_THRESHOLD
    )
    if not covered.any():
        return AlignmentResult(float(shift[0]), float(shift[1]), 0.0, is_star_based, method)
    correlation = float(np.corrcoef(fixed[covered], moved[covered])[0, 1])
    if not np.isfinite(correlation):
        correlation = 0.0
    return AlignmentResult(float(shift[0]), float(shift[1]), correlation, is_star_based, method)


def apply_shift(
    image: np.ndarray, shift_rows_pixels: float, shift_columns_pixels: float, order: int | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Move an image by a shift, and say which pixels the move filled in.

    Parameters
    ----------
    image : `numpy.ndarray`
        The image, 2-D or colour (channels first).
    shift_rows_pixels : `float`
        The shift along the rows.
    shift_columns_pixels : `float`
        The shift along the columns.
    order : `int`, optional
        The spline order of the resampling. By default
        `GROUP_RESAMPLE_SPLINE_ORDER` (3, cubic) for an image; pass 1 for a
        map of counts that must not overshoot.

    Returns
    -------
    shifted : `numpy.ndarray`
        The moved image (`float32`), zero where the move brought in nothing.
    covered : `numpy.ndarray`
        A 2-D boolean mask, `True` where `shifted` holds real data.
    """
    negligible_shift_pixels = resolve(NEGLIGIBLE_SHIFT_PIXELS).value
    if order is None:
        order = int(resolve(GROUP_RESAMPLE_SPLINE_ORDER).value)
    array = np.asarray(image, dtype=np.float32)
    plane_shape = array.shape[-2:]
    covered = (
        shift_image(
            np.ones(plane_shape, dtype=np.float32),
            (shift_rows_pixels, shift_columns_pixels),
            order=1,
            mode="constant",
            cval=0.0,
        )
        > COVERED_PIXEL_THRESHOLD
    )
    if (
        abs(shift_rows_pixels) < negligible_shift_pixels
        and abs(shift_columns_pixels) < negligible_shift_pixels
    ):
        return array.copy(), np.ones(plane_shape, dtype=bool)
    offsets = (0.0,) * (array.ndim - 2) + (shift_rows_pixels, shift_columns_pixels)
    shifted = shift_image(array, offsets, order=order, mode="constant", cval=0.0).astype(np.float32)
    return shifted, covered


def apply_alignment(
    image: np.ndarray, result: AlignmentResult, order: int | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Move an image onto the reference grid by the move of an alignment.

    A shift is `apply_shift`. A Siril move reads the image where the move's
    position map says (see `GroupPositionMap.resample`); this is how the
    group's rejection map follows the group's stack.

    Parameters
    ----------
    image : `numpy.ndarray`
        The image, 2-D or colour (channels first).
    result : `AlignmentResult`
        The move to apply.
    order : `int`, optional
        The spline order of the resampling. By default
        `GROUP_RESAMPLE_SPLINE_ORDER` (3, cubic) for an image; pass 1 for a
        map of counts that must not overshoot.

    Returns
    -------
    moved : `numpy.ndarray`
        The moved image (`float32`), zero where the move brought in nothing.
    covered : `numpy.ndarray`
        A 2-D boolean mask, `True` where `moved` holds real data.
    """
    if order is None:
        order = int(resolve(GROUP_RESAMPLE_SPLINE_ORDER).value)
    if result.position_map is None:
        return apply_shift(image, result.shift_rows_pixels, result.shift_columns_pixels, order=order)
    return result.position_map.resample(np.asarray(image, dtype=np.float32), order)


def align_images_to_reference(
    images: list[np.ndarray],
    reference_index: int,
    crop_fraction: float | None = None,
    prefer_star_position: bool = False,
) -> tuple[list[np.ndarray | None], list[np.ndarray | None], list[AlignmentResult | None]]:
    """Line up every image with the reference image by a shift.

    Parameters
    ----------
    images : `list` [`numpy.ndarray`]
        The group stacks, all the same shape.
    reference_index : `int`
        Which image the others are moved onto. It is returned unchanged.
    crop_fraction : `float` or `None`, optional
        Passed to `measure_alignment` for every pair; see there. The images
        themselves are never cropped, only the region used to measure the
        offset between them.
    prefer_star_position : `bool`, optional
        Passed to `measure_alignment` for every pair; see there.

    Returns
    -------
    aligned : `list` [`numpy.ndarray` or `None`]
        Each image on the reference's pixel grid, or `None` for an image whose
        offset could not be trusted.
    covered : `list` [`numpy.ndarray` or `None`]
        For each image, the mask of pixels that hold real data (all `True` for
        the reference), or `None` where the image was left out.
    results : `list` [`AlignmentResult` or `None`]
        The measured offsets; `None` for the reference itself.
    """
    aligned: list[np.ndarray | None] = []
    covered_masks: list[np.ndarray | None] = []
    results: list[AlignmentResult | None] = []
    if 0 <= reference_index < len(images) and np.ndim(images[reference_index]) >= 2:
        # The group stacks are the run's own images, so their size is the
        # image size that size-based values follow.
        record_basis(RunBasis.IMAGE_SHORT_SIDE_PX, float(min(np.shape(images[reference_index])[-2:])))
    for index, image in enumerate(images):
        if index == reference_index:
            aligned.append(np.asarray(image, dtype=np.float32))
            covered_masks.append(np.ones(np.shape(image)[-2:], dtype=bool))
            results.append(None)
            continue
        try:
            result = measure_alignment(
                images[reference_index],
                image,
                crop_fraction=crop_fraction,
                prefer_star_position=prefer_star_position,
            )
        except InvalidArgumentError:
            # Group stacks of different pixel dimensions (different
            # binning/ROI/camera setup) can't be aligned at all. Treat this
            # exactly like an untrusted correlation -- leave the image out
            # rather than letting the whole call fail, so the caller can
            # name and skip just this group.
            aligned.append(None)
            covered_masks.append(None)
            results.append(AlignmentResult(shift_rows_pixels=0.0, shift_columns_pixels=0.0, correlation=0.0))
            continue
        results.append(result)
        if not result.trusted:
            aligned.append(None)
            covered_masks.append(None)
            continue
        shifted, covered = apply_alignment(image, result)
        aligned.append(shifted)
        covered_masks.append(covered)
    return aligned, covered_masks, results
