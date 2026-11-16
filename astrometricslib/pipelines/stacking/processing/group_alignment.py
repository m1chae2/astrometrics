"""Line up the stacked image of each exposure group before they are combined.

Siril registers the frames of one group to that group's own reference frame,
so two group stacks of the same field are not lined up with each other. On the
2026 NGC 2244, NGC 2903 and NGC 2403 sessions they were off by 6 to 7 pixels
(2.5, -6.4), (5.1, -6.2) and (0.6, 7.0), and combining them as they were would
smear every star. This module finds the offset of each group stack against a
reference group stack and moves it onto the reference.

The first measurement is a shift. Frames in one session share a rotation,
and a shift-only alignment matches what the spectral registration already
assumes (see `run_siril_stack`). Stacks of different nights are not that
simple: the camera sits at a slightly different angle and the focus changes
the scale a little. For imaging stacks the shift is therefore refined by
matching the stars of the two stacks and fitting a shift, a rotation and a
scale (see `refine_alignment_with_stars`). A group whose offset cannot be
found is reported, so the caller can leave it out rather than blur the stack
with it.

Everything here works on arrays and returns arrays; reading and writing files
is left to the caller.
"""

import math
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import affine_transform, gaussian_filter, maximum_filter
from scipy.ndimage import shift as shift_image
from scipy.spatial import cKDTree
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
from astrometricslib.pipelines.stacking.processing.star_pattern_candidates import (
    candidate_similarity_transforms,
)

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

# A pixel counts as covered by the shifted image where a mask of ones, shifted
# the same way, is still this close to one. (Linear interpolation of the mask
# makes the border pixels partial values.)
COVERED_PIXEL_THRESHOLD = 0.999

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

# Refining the offset with the stars themselves (imaging stacks only).
#
# A shift cannot describe two stacks of different nights. On M 57 (2026-10-03)
# the 30 s group and the 60 s group differ by -0.040 degrees of rotation and
# +0.022% of scale. On M 27 the 30 s and 60 s groups differ from the 127.8 s
# group by 0.053 and -0.004 degrees and by -0.012% and -0.052% of scale. With
# the best shift alone, stars were still 0.86 px (M 57) and 1.09 px and
# 0.61 px (M 27) off on average (root mean square), up to 1.9 px toward the
# edges of the frame. Fitting a shift, a rotation and a scale to the 368 to
# 375 stars matched in each pair left 0.05 to 0.06 px. The constants below
# were chosen on those three pairs; no other targets have been checked.

# The brightest this many stars of each stack are matched. On the three pairs
# above, 368 to 375 of 400 found a partner (92 to 94%).
REFINEMENT_STAR_COUNT = TunedParameter(
    name="stacking.refinement_star_count",
    default=400,
    unit="stars",
    description="Number of the brightest stars of each stack that are matched in the refinement.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-14",
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

# A star in one stack matches one in the other when, after the first shift,
# they lie within this many pixels. The largest miss of the first shift on the
# three pairs was 1.9 px, and 3 px is far below the spacing of the 400
# brightest stars (about 150 px).
#
# Stands for how far a star can sit from its partner after the first shift, so
# it is a multiple of the star width and follows it.
REFINEMENT_MATCH_RADIUS_PIXELS = TunedParameter(
    name="stacking.refinement_match_radius_pixels",
    default=factor_for(3.0, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description=(
        "Distance within which a star in one stack matches a star in the other after the first shift."
    ),
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-D65",
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=3.0,
)

# At least this many matched stars are needed. The fit has four unknowns and
# real pairs gave 368 to 375, so 30 is a wide margin that still refuses a
# match made from a handful of chance coincidences.
REFINEMENT_MINIMUM_PAIRS = TunedParameter(
    name="stacking.refinement_minimum_pairs",
    default=30,
    unit="pairs",
    description="Fewest matched stars needed for the refinement fit.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-14",
)

# The fit is repeated after dropping stars farther than this many robust sigmas
# from it, up to this many times, so a mismatched pair does not tilt it.
REFINEMENT_CLIP_SIGMAS = TunedParameter(
    name="stacking.refinement_clip_sigmas",
    default=3.0,
    unit="robust sigmas",
    description="Distance, in robust sigmas, beyond which a matched pair is dropped from the fit.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D65",
)
REFINEMENT_CLIP_PASSES = TunedParameter(
    name="stacking.refinement_clip_passes",
    default=3,
    unit="passes",
    description="Most times the fit is repeated after dropping poorly matched stars.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D65",
)

# A rotation or scale outside these limits is not believed and the plain shift
# is kept. The largest seen was 0.053 degrees and 0.052% (0.0005); the limits
# are about ten times that, so a real camera change passes and a wrong match
# (or a different camera setup) does not.
REFINEMENT_MAX_ROTATION_DEGREES = TunedParameter(
    name="stacking.refinement_max_rotation_degrees",
    default=0.5,
    unit="degrees",
    description="Largest rotation the refinement fit may report before it is not believed.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-14",
)
REFINEMENT_MAX_SCALE_DEVIATION = TunedParameter(
    name="stacking.refinement_max_scale_deviation",
    default=0.005,
    unit="fraction",
    description="Largest change of scale the refinement fit may report before it is not believed.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-14",
)

# The fit is believed only if the stars it leaves behind are this close to
# their partners (root mean square). The three real pairs left 0.05 to 0.06 px.
#
# Stands for how closely matched star centers can agree, so it is a multiple of
# the star width and follows it.
REFINEMENT_MAX_RESIDUAL_PIXELS = TunedParameter(
    name="stacking.refinement_max_residual_pixels",
    default=factor_for(0.5, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description=(
        "Largest root-mean-square leftover between matched stars for the refinement fit to be believed."
    ),
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-14",
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=0.5,
)

# Star-pattern matching (`find_alignment_from_star_patterns`) finds the move
# with no first guess, so it also finds moves that correlation cannot. Measured
# on the real group stacks of 2026-10-13, where correlation had failed
# (0.01 to 0.26): M 106 (turn -0.40 degrees), NGC 7023 (+0.16), M 51 300 s
# (-6.28), NGC 2024 (-179.59) and NGC 6992 (+179.53) -- the last two are the
# 180 degree flip of a German equatorial mount at the meridian. Scales were
# 0.9996 to 1.0014. So any rotation is allowed, but the scale limit
# (`REFINEMENT_MAX_SCALE_DEVIATION`, 0.005, about 3.5 times the largest scale
# seen) and the residual limit (`REFINEMENT_MAX_RESIDUAL_PIXELS`) still apply.
# This many rounds of pair-and-fit follow the best guess, each pairing the
# stars again with the improved fit (a guess from three stars is off by a few
# pixels at the frame edge; one round usually fixes it).
PATTERN_REFIT_PASSES = TunedParameter(
    name="stacking.pattern_refit_passes",
    default=3,
    unit="passes",
    description="Times the star-pattern fit is repeated with a wider search of matches.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D14",
)

# The leftover allowed after a star-pattern match is looser than
# `REFINEMENT_MAX_RESIDUAL_PIXELS`. The refinement starts from a correlation
# shift, which only exists when the stacks are alike. A pattern match is
# checked by many stars instead, so a loose leftover does not make it doubtful:
# a chance match of 30 or more stars within 3 px is not credible among 400
# stars on 24 million pixels, and the fit error is the leftover divided by the
# square root of the pair count. Leftovers measured on the 2026-10-13 pairs:
# 0.13 px (NGC 7023, 249 pairs), 0.31 (NGC 2024, 350; NGC 6992, 193), 0.44
# (M 106, 249) and 1.20 (M 51 300 s, 223). M 106's leftover is smooth lens
# distortion (a quadratic fit leaves 0.05 px, so edge stars are about 1 px
# off); M 51 300 s's is random scatter of the star centres (the four halves of
# the frame agree to under 0.1 px), a soft stack, not a poor fit. 1.5 px is
# just over the largest seen. It stays far under the 3 px pairing radius.
#
# Stands for how well star centers can be measured, so it is a multiple of the
# star width and follows it.
PATTERN_MAX_RESIDUAL_PIXELS = TunedParameter(
    name="stacking.pattern_max_residual_pixels",
    default=factor_for(1.5, RunBasis.STAR_WIDTH_PX),
    unit="× star width",
    description="Largest root-mean-square leftover after a star-pattern match for it to be believed.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-15",
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=1.5,
)


@dataclass
class AlignmentResult:
    """How one image lines up with the reference.

    Attributes
    ----------
    shift_rows_pixels : `float`
        How far the image must move along the rows (the y axis) to line up
        with the reference. Positive moves it toward higher rows.
    shift_columns_pixels : `float`
        How far the image must move along the columns (the x axis).
    correlation : `float`
        How well the two filtered images agree once the image is moved. Near
        one is a match; near zero means the offset found is not real.
    is_star_based : `bool`
        `True` when the offset is the difference between the two images'
        zero-order star positions (see `find_zero_order_position`) rather
        than the result of correlating the images. Such an offset is trusted
        without the correlation test, since the images may look very
        different (a saturated long exposure against a faint short one).
    rotation_degrees : `float`
        How far the image must turn about its centre, in degrees, to line up
        with the reference. Positive turns from the +column axis toward the
        +row axis. Zero unless `refine_alignment_with_stars` found a rotation.
    scale : `float`
        The factor by which the image must be enlarged about its centre to
        line up with the reference. One unless the refinement found a scale.
    star_pairs : `int`
        How many matched stars the refinement used. Zero without refinement.
    residual_pixels : `float` or `None`
        How far the matched stars still lie from their partners after the
        move (root mean square), when the refinement ran.
    is_star_pattern : `bool`
        `True` when the move was found by matching patterns of stars with no
        first guess (see `find_alignment_from_star_patterns`). Such a move is
        trusted without the correlation test, since it was checked against
        every star and left them close to their partners.

    Notes
    -----
    With a rotation or a scale, the shifts are those of the image centre, and
    the rotation and scale are applied about that centre.
    """

    shift_rows_pixels: float
    shift_columns_pixels: float
    correlation: float
    is_star_based: bool = False
    rotation_degrees: float = 0.0
    scale: float = 1.0
    star_pairs: int = 0
    residual_pixels: float | None = None
    is_star_pattern: bool = False

    @property
    def has_rotation_or_scale(self) -> bool:
        """Whether the move is more than a shift.

        Returns
        -------
        has_rotation_or_scale : `bool`
            `True` when the refinement found a rotation or a scale.
        """
        return not (math.isclose(self.rotation_degrees, 0.0, abs_tol=1e-12) and math.isclose(self.scale, 1.0))

    @property
    def trusted(self) -> bool:
        """Whether the correlation is high enough to believe the offset.

        Returns
        -------
        trusted : `bool`
            `True` for a star-based offset or a star-pattern match, otherwise
            `True` at or above `MINIMUM_ALIGNMENT_CORRELATION`.
        """
        return (
            self.is_star_based
            or self.is_star_pattern
            or self.correlation >= resolve(MINIMUM_ALIGNMENT_CORRELATION).value
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
    sigma = 1.4826 * float(np.median(np.abs(filtered - np.median(filtered))))
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
    sigma = 1.4826 * float(np.median(np.abs(smooth - np.median(smooth))))
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


def _pair_nearest(
    reference_stars: np.ndarray, image_stars: np.ndarray, predicted: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Pair image stars with reference stars near their predicted places.

    A pair is kept when the predicted place lies within
    `REFINEMENT_MATCH_RADIUS_PIXELS` of a reference star, and each reference
    star is used once (by the closest of the image stars that claim it).

    Returns
    -------
    reference_points, image_points : `numpy.ndarray`
        The matched (row, column) positions, one row per pair, the image's
        positions as they were before the move.
    """
    if len(reference_stars) == 0 or len(image_stars) == 0:
        return np.empty((0, 2)), np.empty((0, 2))
    distances, nearest = cKDTree(reference_stars).query(
        predicted, distance_upper_bound=resolve(REFINEMENT_MATCH_RADIUS_PIXELS).value
    )
    claimed: dict[int, tuple[float, int]] = {}
    for image_index, (distance, reference_index) in enumerate(zip(distances, nearest, strict=True)):
        if not np.isfinite(distance):
            continue
        if reference_index not in claimed or distance < claimed[reference_index][0]:
            claimed[int(reference_index)] = (float(distance), image_index)
    reference_indices = np.array(list(claimed.keys()), dtype=int)
    image_indices = np.array([claimed[index][1] for index in reference_indices], dtype=int)
    return reference_stars[reference_indices], image_stars[image_indices]


def _match_stars(
    reference_stars: np.ndarray,
    image_stars: np.ndarray,
    shift_rows_pixels: float,
    shift_columns_pixels: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Pair each star of the image with the nearest star of the reference.

    The image's stars are first moved by the given shift, then paired as in
    `_pair_nearest`.

    Returns
    -------
    reference_points, image_points : `numpy.ndarray`
        The matched (row, column) positions, one row per pair, the image's
        positions as they were before the shift.
    """
    if len(reference_stars) == 0 or len(image_stars) == 0:
        return np.empty((0, 2)), np.empty((0, 2))
    predicted = image_stars + np.array([shift_rows_pixels, shift_columns_pixels])
    return _pair_nearest(reference_stars, image_stars, predicted)


def _fit_similarity(
    reference_points: np.ndarray, image_points: np.ndarray
) -> tuple[complex, complex, np.ndarray] | None:
    """Fit ``reference = a * image + b`` to matched star positions.

    Positions are written as complex numbers ``column + 1j * row``, so that
    multiplying by ``a`` turns by ``angle(a)`` and enlarges by ``abs(a)``. The
    fit is repeated after dropping stars far from it (see
    `REFINEMENT_CLIP_SIGMAS`).

    Returns
    -------
    fit : `tuple` or `None`
        ``(a, b, kept)``: the two complex numbers and a boolean mask of the
        pairs the final fit used. `None` when fewer than
        `REFINEMENT_MINIMUM_PAIRS` pairs remain.
    """
    refinement_clip_passes = resolve(REFINEMENT_CLIP_PASSES).value
    refinement_minimum_pairs = resolve(REFINEMENT_MINIMUM_PAIRS).value
    refinement_clip_sigmas = resolve(REFINEMENT_CLIP_SIGMAS).value
    image_complex = image_points[:, 1] + 1j * image_points[:, 0]
    reference_complex = reference_points[:, 1] + 1j * reference_points[:, 0]
    kept = np.ones(len(image_complex), dtype=bool)
    a = b = 0j
    for _ in range(refinement_clip_passes):
        if kept.sum() < refinement_minimum_pairs:
            return None
        design = np.vstack([image_complex[kept], np.ones(int(kept.sum()), dtype=complex)]).T
        (a, b), *_ = np.linalg.lstsq(design, reference_complex[kept], rcond=None)
        residual = np.abs(reference_complex - (a * image_complex + b))
        spread = 1.4826 * float(np.median(residual[kept]))
        limit = max(refinement_clip_sigmas * spread, 1e-6)
        new_kept = residual <= limit
        if np.array_equal(new_kept, kept):
            break
        kept = new_kept
    if kept.sum() < refinement_minimum_pairs:
        return None
    return complex(a), complex(b), kept


def refine_alignment_with_stars(
    reference: np.ndarray,
    image: np.ndarray,
    initial: AlignmentResult,
    reference_stars: np.ndarray | None = None,
    image_stars: np.ndarray | None = None,
) -> AlignmentResult:
    """Improve a shift by fitting the stars' shift, rotation and scale.

    The stars of both images are found, paired using the initial shift, and a
    shift, a rotation and a scale (a similarity transform) are fitted to the
    pairs. The fit replaces the initial result only when it is believable and
    leaves the stars closer together than the initial shift did: at least
    `REFINEMENT_MINIMUM_PAIRS` pairs, a rotation and scale within the limits,
    and a leftover under `REFINEMENT_MAX_RESIDUAL_PIXELS`. Otherwise the
    initial result is returned unchanged.

    Parameters
    ----------
    reference : `numpy.ndarray`
        The stack to line up with, 2-D or colour (channels first).
    image : `numpy.ndarray`
        The stack to move, the same shape as `reference`.
    initial : `AlignmentResult`
        The shift measured by `measure_alignment`. It must be accurate to
        within `REFINEMENT_MATCH_RADIUS_PIXELS` for the stars to pair up.
    reference_stars : `numpy.ndarray`, optional
        The reference's star centres from `detect_star_centroids`, when the
        caller has them already (one reference serves many images).
    image_stars : `numpy.ndarray`, optional
        The image's star centres, when the caller has them already.

    Returns
    -------
    result : `AlignmentResult`
        The refined result, with the shifts given for the image centre and the
        rotation, scale, pair count and leftover filled in, or `initial`.
    """
    if reference_stars is None:
        reference_stars = detect_star_centroids(_to_plane(reference))
    if image_stars is None:
        image_stars = detect_star_centroids(_to_plane(image))
    reference_points, image_points = _match_stars(
        reference_stars, image_stars, initial.shift_rows_pixels, initial.shift_columns_pixels
    )
    if len(reference_points) < resolve(REFINEMENT_MINIMUM_PAIRS).value:
        return initial
    fit = _fit_similarity(reference_points, image_points)
    if fit is None:
        return initial
    a, b, kept = fit
    rotation_degrees = float(np.degrees(np.angle(a)))
    if (
        abs(rotation_degrees) > resolve(REFINEMENT_MAX_ROTATION_DEGREES).value
        or abs(abs(a) - 1.0) > resolve(REFINEMENT_MAX_SCALE_DEVIATION).value
    ):
        return initial
    image_complex = image_points[:, 1] + 1j * image_points[:, 0]
    reference_complex = reference_points[:, 1] + 1j * reference_points[:, 0]
    fitted_residual = float(
        np.sqrt(np.mean(np.abs(reference_complex[kept] - (a * image_complex[kept] + b)) ** 2))
    )
    shift_only = reference_points[kept] - (
        image_points[kept] + [initial.shift_rows_pixels, initial.shift_columns_pixels]
    )
    shift_only_residual = float(np.sqrt(np.mean((shift_only**2).sum(axis=1))))
    if (
        fitted_residual > resolve(REFINEMENT_MAX_RESIDUAL_PIXELS).value
        or fitted_residual >= shift_only_residual
    ):
        return initial
    # Express the move about the image centre: the centre goes to
    # a * centre + b, so it is displaced by that minus where it started.
    plane_shape = np.shape(image)[-2:]
    centre = (plane_shape[1] - 1) / 2.0 + 1j * (plane_shape[0] - 1) / 2.0
    centre_shift = a * centre + b - centre
    return AlignmentResult(
        shift_rows_pixels=float(centre_shift.imag),
        shift_columns_pixels=float(centre_shift.real),
        correlation=initial.correlation,
        is_star_based=initial.is_star_based,
        rotation_degrees=rotation_degrees,
        scale=float(abs(a)),
        star_pairs=int(kept.sum()),
        residual_pixels=fitted_residual,
    )


def find_alignment_from_star_patterns(
    reference: np.ndarray,
    image: np.ndarray,
    reference_stars: np.ndarray | None = None,
    image_stars: np.ndarray | None = None,
) -> AlignmentResult | None:
    """Find the shift, rotation and scale of a stack with no first guess.

    Triangles of stars are matched between the two star lists (see
    `star_pattern_candidates.candidate_similarity_transforms`). The guess that
    lines up the most stars is refined by pairing the stars again and fitting
    a similarity transform (see `_fit_similarity`). The result is returned only
    when it is believable: at least `REFINEMENT_MINIMUM_PAIRS` pairs, a scale
    within `REFINEMENT_MAX_SCALE_DEVIATION` of one, and a leftover under
    `PATTERN_MAX_RESIDUAL_PIXELS`. Any rotation is accepted, including the
    half turn of a mount flip.

    Parameters
    ----------
    reference : `numpy.ndarray`
        The stack to line up with, 2-D or colour (channels first).
    image : `numpy.ndarray`
        The stack to move, the same shape as `reference`.
    reference_stars, image_stars : `numpy.ndarray`, optional
        The star centres from `detect_star_centroids`, when the caller has
        them already.

    Returns
    -------
    result : `AlignmentResult` or `None`
        The move, with `is_star_pattern` set, its correlation `0.0` (never
        measured), and the shifts given for the image centre; `None` when no
        believable move was found.
    """
    refinement_minimum_pairs = resolve(REFINEMENT_MINIMUM_PAIRS).value
    pattern_refit_passes = resolve(PATTERN_REFIT_PASSES).value
    refinement_match_radius_pixels = resolve(REFINEMENT_MATCH_RADIUS_PIXELS).value
    if reference_stars is None:
        reference_stars = detect_star_centroids(_to_plane(reference))
    if image_stars is None:
        image_stars = detect_star_centroids(_to_plane(image))
    if min(len(reference_stars), len(image_stars)) < refinement_minimum_pairs:
        return None
    candidates = candidate_similarity_transforms(reference_stars, image_stars)
    if len(candidates) == 0:
        return None
    image_complex = image_stars[:, 1] + 1j * image_stars[:, 0]
    reference_tree = cKDTree(reference_stars)
    best_count = 0
    best: tuple[complex, complex] | None = None
    for a, b in candidates:
        predicted = a * image_complex + b
        distances, _ = reference_tree.query(
            np.column_stack([predicted.imag, predicted.real]),
            distance_upper_bound=refinement_match_radius_pixels,
        )
        count = int(np.isfinite(distances).sum())
        if count > best_count:
            best_count, best = count, (complex(a), complex(b))
    if best is None or best_count < refinement_minimum_pairs:
        return None
    a, b = best
    kept = np.zeros(0, dtype=bool)
    reference_points = image_points = np.empty((0, 2))
    for _ in range(pattern_refit_passes):
        predicted = a * image_complex + b
        reference_points, image_points = _pair_nearest(
            reference_stars, image_stars, np.column_stack([predicted.imag, predicted.real])
        )
        fit = _fit_similarity(reference_points, image_points) if len(reference_points) else None
        if fit is None:
            return None
        a, b, kept = fit
    if abs(abs(a) - 1.0) > resolve(REFINEMENT_MAX_SCALE_DEVIATION).value:
        return None
    pair_image = image_points[kept, 1] + 1j * image_points[kept, 0]
    pair_reference = reference_points[kept, 1] + 1j * reference_points[kept, 0]
    residual = float(np.sqrt(np.mean(np.abs(pair_reference - (a * pair_image + b)) ** 2)))
    if residual > resolve(PATTERN_MAX_RESIDUAL_PIXELS).value:
        return None
    plane_shape = np.shape(image)[-2:]
    centre = (plane_shape[1] - 1) / 2.0 + 1j * (plane_shape[0] - 1) / 2.0
    centre_shift = a * centre + b - centre
    return AlignmentResult(
        shift_rows_pixels=float(centre_shift.imag),
        shift_columns_pixels=float(centre_shift.real),
        correlation=0.0,
        rotation_degrees=float(np.degrees(np.angle(a))),
        scale=float(abs(a)),
        star_pairs=int(kept.sum()),
        residual_pixels=residual,
        is_star_pattern=True,
    )


def _stars_pair_up_with_shift(
    reference_stars: np.ndarray, image_stars: np.ndarray, result: AlignmentResult
) -> bool:
    """Say whether the stars of two stacks pair up under a measured shift.

    Returns
    -------
    pair_up : `bool`
        `True` when at least `REFINEMENT_MINIMUM_PAIRS` stars land within
        `REFINEMENT_MATCH_RADIUS_PIXELS` of a partner after the shift. When
        this is `False` for a shift that correlation trusted, the shift is
        wrong or the stacks are turned, and the stars should decide instead.
    """
    reference_points, _ = _match_stars(
        reference_stars, image_stars, result.shift_rows_pixels, result.shift_columns_pixels
    )
    return len(reference_points) >= resolve(REFINEMENT_MINIMUM_PAIRS).value


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
    moved = shift_image(moving, shift, order=1, mode="constant", cval=0.0)
    covered = (
        shift_image(np.ones_like(moving), shift, order=1, mode="constant", cval=0.0) > COVERED_PIXEL_THRESHOLD
    )
    if not covered.any():
        return AlignmentResult(float(shift[0]), float(shift[1]), 0.0, is_star_based)
    correlation = float(np.corrcoef(fixed[covered], moved[covered])[0, 1])
    if not np.isfinite(correlation):
        correlation = 0.0
    return AlignmentResult(float(shift[0]), float(shift[1]), correlation, is_star_based)


def apply_shift(
    image: np.ndarray, shift_rows_pixels: float, shift_columns_pixels: float, order: int = 3
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
        The spline order of the resampling: 3 (cubic) for an image, 1 for a
        map of counts that must not overshoot.

    Returns
    -------
    shifted : `numpy.ndarray`
        The moved image (`float32`), zero where the move brought in nothing.
    covered : `numpy.ndarray`
        A 2-D boolean mask, `True` where `shifted` holds real data.
    """
    negligible_shift_pixels = resolve(NEGLIGIBLE_SHIFT_PIXELS).value
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
    image: np.ndarray, result: AlignmentResult, order: int = 3
) -> tuple[np.ndarray, np.ndarray]:
    """Move an image onto the reference by the shift, rotation and scale found.

    With no rotation or scale this is `apply_shift`. Otherwise the image is
    turned and enlarged about its centre, then moved by the centre's shift.

    Parameters
    ----------
    image : `numpy.ndarray`
        The image, 2-D or colour (channels first).
    result : `AlignmentResult`
        The move to apply.
    order : `int`, optional
        The spline order of the resampling: 3 (cubic) for an image, 1 for a
        map of counts that must not overshoot.

    Returns
    -------
    moved : `numpy.ndarray`
        The moved image (`float32`), zero where the move brought in nothing.
    covered : `numpy.ndarray`
        A 2-D boolean mask, `True` where `moved` holds real data.
    """
    if not result.has_rotation_or_scale:
        return apply_shift(image, result.shift_rows_pixels, result.shift_columns_pixels, order=order)
    array = np.asarray(image, dtype=np.float32)
    plane_shape = array.shape[-2:]
    # The move maps a position z in the image to a * (z - c) + c + t in the
    # reference, where z = column + 1j * row, c is the centre and t its shift.
    # `affine_transform` wants the inverse: for each position in the output,
    # where to read the input, as a matrix and an offset in (row, column).
    a = result.scale * np.exp(1j * np.radians(result.rotation_degrees))
    inverse = 1.0 / a
    matrix = np.array([[inverse.real, inverse.imag], [-inverse.imag, inverse.real]])
    centre = np.array([(plane_shape[0] - 1) / 2.0, (plane_shape[1] - 1) / 2.0])
    shift = np.array([result.shift_rows_pixels, result.shift_columns_pixels])
    offset = centre - matrix @ (centre + shift)
    planes = array[np.newaxis] if array.ndim == 2 else array
    moved = np.stack([
        affine_transform(plane, matrix, offset=offset, order=order, mode="constant", cval=0.0)
        for plane in planes
    ]).astype(np.float32)
    covered = (
        affine_transform(
            np.ones(plane_shape, dtype=np.float32), matrix, offset=offset, order=1, mode="constant", cval=0.0
        )
        > COVERED_PIXEL_THRESHOLD
    )
    return (moved[0] if array.ndim == 2 else moved), covered


def align_images_to_reference(
    images: list[np.ndarray],
    reference_index: int,
    crop_fraction: float | None = None,
    prefer_star_position: bool = False,
    refine_with_stars: bool = False,
) -> tuple[list[np.ndarray | None], list[np.ndarray | None], list[AlignmentResult | None]]:
    """Line up every image with the reference image.

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
    refine_with_stars : `bool`, optional
        When `True`, each trusted shift is refined by fitting the stars'
        shift, rotation and scale (see `refine_alignment_with_stars`). An
        image whose shift is untrusted, or too far off for the stars to pair
        up, is lined up from patterns of stars instead (see
        `find_alignment_from_star_patterns`). Meant for imaging stacks;
        spectral stacks keep the plain shift.

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
    reference_stars: np.ndarray | None = None
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
        if refine_with_stars:
            if reference_stars is None:
                reference_stars = detect_star_centroids(_to_plane(images[reference_index]))
            image_stars = detect_star_centroids(_to_plane(image))
            if result.trusted:
                result = refine_alignment_with_stars(
                    images[reference_index],
                    image,
                    result,
                    reference_stars=reference_stars,
                    image_stars=image_stars,
                )
            if not result.trusted or (
                not result.star_pairs and not _stars_pair_up_with_shift(reference_stars, image_stars, result)
            ):
                # Correlation failed, or its shift is too far off for the
                # stars to pair up (a flip of the mount, a night-to-night
                # turn): find the move from patterns of stars instead. A
                # trusted shift under which the stars do pair up is kept even
                # when the refinement above declined to improve it, so
                # stacks that already line up give the same result as before.
                # If the pattern search fails too, `result` stays as it was
                # and the group is left out when it is untrusted.
                pattern_result = find_alignment_from_star_patterns(
                    images[reference_index],
                    image,
                    reference_stars=reference_stars,
                    image_stars=image_stars,
                )
                if pattern_result is not None:
                    pattern_result.correlation = result.correlation
                    result = pattern_result
        results.append(result)
        if not result.trusted:
            aligned.append(None)
            covered_masks.append(None)
            continue
        shifted, covered = apply_alignment(image, result)
        aligned.append(shifted)
        covered_masks.append(covered)
    return aligned, covered_masks, results
