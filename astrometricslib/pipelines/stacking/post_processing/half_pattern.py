"""Measures the fine-scale pattern that two half stacks of one target share.

The half-stack difference (`stack_noise.measure_difference_noise`) subtracts
one half stack from the other. It cancels everything the frames of both halves
share, among them a pattern that is fixed on the sensor and that calibration
did not remove, and it keeps the random noise. This file measures the other
side: what the two halves have in common at the scale of a few pixels, in
sky that holds no star or faint source. Nothing here repeats the difference.

Why the common part can be a fixed pattern. Two halves hold different
exposures, so their random noise is independent. A pattern that sits at the
same place on the sensor in every exposure (a hot pixel, a column offset,
flat-field noise the master flat did not remove) is in every exposure and
therefore in both halves, provided both halves sample the same pointings.
After registration it lands on the stack where the frames' pointings put it,
and a pointing drift smears it along the drift. The halves alternate in time,
so a pointing held for several exposures (a drift, or a dither made every N
exposures) gives frames to both halves and the pattern is shared. Only a
dither that gives every exposure its own pointing makes the halves sample
different pointings and share less of it. The covariance
between the halves' fine-scale residuals is the variance of the part that
stays coherent in both halves. The covariance is found with no need to know
the random noise.

What else is shared. Sky structure at the same scale (very faint stars, fine
nebula) is also common to the two halves. Sources found by the faint-source
mask (`stack_noise.analyze_faint_sources`) are masked, but faint ones below its
threshold stay. So the covariance is the part of any fixed pattern that stays
coherent in both halves, plus faint sky; it is not a measurement of the sensor
pattern. Sky structure does not move with the pointing; a sensor pattern is
smeared along it. The walking-noise rows tell the two apart: they compare the
correlation of the halves at one lag along the drift with the correlation at
the same lag across it. A smeared sensor pattern is correlated along the drift
and not across it. Sky structure is correlated the same way in both
directions, except that stars elongated along the drift (a trail) are also
correlated more along it and add a positive excess.

Steps. Both halves are put through a Gaussian high-pass filter of
`FINE_SCALE_SIGMA_PX`. Pixels near a faint source, near the empty edge, or
within three filter widths of either are left out. The rest are split into
square tiles of `TILE_PX`. The sums over each tile give the covariance, the
variance of the halves' difference and the lagged covariances, and a
bootstrap over the tiles gives their errors. The registration reference
frame is in both halves (`subset_noise_run.split_into_halves`); its random
noise is shared too and is taken out of the covariance with the same
equal-frame-noise model that the variance factor of the difference uses. That
correction is a model, not a measurement, and the reference is the frame with
the heaviest weight, so equal noise is least likely to hold for it.

The numbers are in the stack's own units. The ratio divides the shared
variance by the random variance of the whole library stack at the same scale,
which is the difference's variance divided by the variance factor and by the
library stack's effective frame count. It says how the shared part compares
with the random noise that all the frames together leave. The three parts of
the ratio (the covariance, the difference's variance and the reference-frame
correction) are returned beside it so each can be stored and checked.

Planes are read as 32-bit floats. The working arrays are 32-bit, and pixels
are counted from the masks, not from an array of ones, which keeps the peak
memory near ten copies of the image (register entry STK-45).
"""

import logging
import math
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import binary_erosion, gaussian_filter, shift

from astrometricslib.models.quality.measurement import QualityMeasurement, QualitySubject
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_measurement,
)
from astrometricslib.pipelines.stacking.metrics import (
    STACKING_HALF_FINE_SCALE_DIFFERENCE_VARIANCE_STACK_UNIT_SQUARED,
    STACKING_HALF_SHARED_FINE_SCALE_COVARIANCE_STACK_UNIT_SQUARED,
    STACKING_HALF_SHARED_FINE_SCALE_REFERENCE_CORRECTION_STACK_UNIT_SQUARED,
    STACKING_HALF_SHARED_FINE_SCALE_RMS_STACK_UNIT,
    STACKING_HALF_SHARED_FINE_SCALE_VARIANCE_RATIO,
    STACKING_METRICS,
    STACKING_WALKING_NOISE_ACROSS_CORRELATION,
    STACKING_WALKING_NOISE_ALONG_CORRELATION,
    STACKING_WALKING_NOISE_CORRELATION_EXCESS,
)
from astrometricslib.pipelines.stacking.post_processing.stack_noise import analyze_faint_sources

logger = logging.getLogger(__name__)

__all__ = [
    "BOOTSTRAP_COUNT",
    "FINE_SCALE_SIGMA_PX",
    "MINIMUM_PIXELS",
    "MINIMUM_TILES",
    "PATTERN_ROW_NAMES",
    "SOURCE_CLEARANCE_PX",
    "TILE_PX",
    "WALKING_LAG_PX",
    "HalfPatternOutcome",
    "HalfPatternResult",
    "half_pattern_measurements",
    "measure_half_pattern",
]

_SPEC_BY_NAME = {spec.name: spec for spec in STACKING_METRICS}

# The width of the Gaussian whose smooth image is taken away. Sensor patterns
# of interest (hot pixels, column offsets, flat-field grain) are 1 to 3 pixels
# wide, and stars are about 2 pixels wide (FWHM, near Nyquist, from the
# observatory's good nights). 3 px keeps scales up to about 7 px FWHM out
# of the result, which includes a star's wings and the pattern of a smooth
# sky.
FINE_SCALE_SIGMA_PX = 3.0

# Pixels closer than this to a flagged faint source are left out. A Gaussian of
# sigma 3 px has fallen to 1% of its peak at 3 sigma (9 px); one more pixel
# covers the flagged pixel's own width, so the clearance is 10 px. The aperture
# rule uses 13 px for a 12 px ring, which this measurement does not have.
SOURCE_CLEARANCE_PX = 3.0 * FINE_SCALE_SIGMA_PX + 1.0

# The tiles the bootstrap resamples. 64 px is wider than 20 filter widths of a
# hot pixel's correlation (3 px) so the tiles are close to independent, and
# gives about 2,200 tiles on a 3008 x 3008 image, enough for a stable
# bootstrap.
TILE_PX = 64

# Fewest tiles holding usable pixels for the errors to mean anything. With
# fewer than 30 the bootstrap of the tile sums is too coarse.
MINIMUM_TILES = 30

# Fewest usable pixels. A covariance of fewer than 1e5 pixels has an error of
# about 0.3 of the variance, from 1 / sqrt(n).
MINIMUM_PIXELS = 100_000

# The number of bootstrap resamples of the tiles. 400 give the error of a
# standard deviation to about 3.5% (1 / sqrt(2 x 400)).
BOOTSTRAP_COUNT = 400
BOOTSTRAP_SEED = 20261024

# The lag, in pixels, at which the halves' correlation along and across the
# drift is compared. It has to be beyond the filter width (a Gaussian
# high-pass leaves negative side lobes within about two widths of a source),
# and shorter than the pointing range along the drift or a smear cannot
# reach it. 6 px is twice the filter width.
WALKING_LAG_PX = 6.0

# The pointing range along the drift must be at least this many lags for the
# comparison to be made: a smear of length L carries correlation out to a lag
# of about L. At 2 the smear is twice the lag.
MINIMUM_EXTENT_IN_LAGS = 2.0

# A shifted pixel counts only when all four pixels it is interpolated from are
# in the mask. The mask is shifted as numbers 0 to 1 and a pixel needs this
# value.
SHIFTED_MASK_LEVEL = 0.999

# What a measured ratio row says about its error: the bootstrap covers the
# scatter of the pixels, not the two numbers the denominator and the
# correction are scaled by.
RATIO_ERROR_NOTE = (
    "Ratio = (covariance - reference-frame correction) / (difference variance / (variance factor F x "
    "effective frame count N)); its error is the bootstrap over tiles and leaves out the uncertainty of F "
    "and N."
)
# What a measured correction row says about its model.
CORRECTION_NOTE = (
    "Model: every frame has the average frame's noise (sigma_f^2 = difference variance / F); the "
    "reference frame has the heaviest weight, so this is the weakest for it."
)

PATTERN_NOT_POSITIVE_REASON = (
    "the covariance of the halves is not above zero, so no pattern shows above the scatter of the estimate"
)


@dataclass(frozen=True)
class HalfPatternResult:
    """The fine-scale pattern shared by the two halves.

    Parameters
    ----------
    pixel_count : `int`
        Pixels used (in the mask).
    tile_count : `int`
        Tiles holding at least one used pixel.
    shared_variance : `float`
        The covariance of the halves' high-pass residuals, with the shared
        frame's random part taken out, in the stack's units squared.
    shared_variance_error : `float`
        Its bootstrap standard deviation.
    covariance : `float`
        The covariance before the shared frame's random part is taken out
        (c), in the stack's units squared.
    covariance_error : `float`
        Its bootstrap standard deviation.
    difference_variance : `float`
        The variance of the difference of the halves' high-pass residuals
        (D), in the stack's units squared. The ratio's denominator is D
        divided by the variance factor and by the effective frame count.
    difference_variance_error : `float`
        Its bootstrap standard deviation.
    reference_correction : `float`
        The shared frame's random part that was taken out of the covariance
        (D / F x a x b), in the stack's units squared.
    reference_correction_error : `float`
        Its bootstrap standard deviation.
    random_variance_full_stack : `float`
        The random variance of the library stack at the same scale, in the
        stack's units squared.
    variance_ratio : `float`
        ``shared_variance`` divided by ``random_variance_full_stack``.
    variance_ratio_error : `float`
        Its bootstrap standard deviation.
    rms : `float` or `None`
        The square root of ``shared_variance`` when it is above zero, else
        `None`.
    rms_error : `float` or `None`
        Its bootstrap standard deviation.
    along_correlation, across_correlation, excess : `float` or `None`
        The correlation coefficient of the halves at a lag of ``lag_px``
        along the drift axis and across it, and their difference. `None`
        when the lag could not be taken (see ``walking_reason``).
    along_error, across_error, excess_error : `float` or `None`
        Their bootstrap standard deviations.
    lag_px : `float`
        The lag used.
    walking_reason : `str`
        Why there are no lagged correlations. Empty when there are.
    """

    pixel_count: int
    tile_count: int
    shared_variance: float
    shared_variance_error: float
    covariance: float
    covariance_error: float
    difference_variance: float
    difference_variance_error: float
    reference_correction: float
    reference_correction_error: float
    random_variance_full_stack: float
    variance_ratio: float
    variance_ratio_error: float
    rms: float | None
    rms_error: float | None
    along_correlation: float | None = None
    across_correlation: float | None = None
    excess: float | None = None
    along_error: float | None = None
    across_error: float | None = None
    excess_error: float | None = None
    lag_px: float = WALKING_LAG_PX
    walking_reason: str = ""


@dataclass(frozen=True)
class HalfPatternOutcome:
    """A pattern measurement or the reason there is none.

    Parameters
    ----------
    result : `HalfPatternResult` or `None`
        The numbers.
    reason : `str`
        Why `result` is `None`.
    """

    result: HalfPatternResult | None
    reason: str = ""


def _fine_scale(plane: np.ndarray, usable: np.ndarray) -> np.ndarray:
    """Take the smooth part out of an image, leaving scales of a few pixels.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The image.
    usable : `numpy.ndarray`
        Which pixels hold data. The others are filled with the median of the
        usable ones before filtering, so the empty edge does not look like a
        step.

    Returns
    -------
    residual : `numpy.ndarray`
        The image minus its Gaussian smoothing, 32-bit.
    """
    filled = np.where(usable, plane, np.median(plane[usable])).astype(np.float32)
    residual = filled - gaussian_filter(filled, FINE_SCALE_SIGMA_PX)
    return residual.astype(np.float32, copy=False)


def _tile_sums(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Add up an image over the mask in square tiles.

    Parameters
    ----------
    values : `numpy.ndarray`
        The image.
    mask : `numpy.ndarray`
        Boolean, the pixels to add.

    Returns
    -------
    sums : `numpy.ndarray`
        One float64 sum for each tile, flattened. The right and bottom
        remainders narrower than a tile are left out.
    """
    rows = (values.shape[0] // TILE_PX) * TILE_PX
    columns = (values.shape[1] // TILE_PX) * TILE_PX
    masked = np.where(mask[:rows, :columns], values[:rows, :columns], 0.0)
    tiles = masked.reshape(rows // TILE_PX, TILE_PX, columns // TILE_PX, TILE_PX)
    return tiles.sum(axis=(1, 3), dtype=np.float64).ravel()


def _tile_counts(mask: np.ndarray) -> np.ndarray:
    """Count the mask's pixels in square tiles.

    Parameters
    ----------
    mask : `numpy.ndarray`
        Boolean, the pixels to count.

    Returns
    -------
    counts : `numpy.ndarray`
        One float64 count for each tile, flattened, laid out as `_tile_sums`
        lays out its sums. No array of ones is made.
    """
    rows = (mask.shape[0] // TILE_PX) * TILE_PX
    columns = (mask.shape[1] // TILE_PX) * TILE_PX
    tiles = mask[:rows, :columns].reshape(rows // TILE_PX, TILE_PX, columns // TILE_PX, TILE_PX)
    return tiles.sum(axis=(1, 3), dtype=np.int64).ravel().astype(np.float64)


def _lagged(
    residual: np.ndarray, mask: np.ndarray, row_lag: float, column_lag: float
) -> tuple[np.ndarray, np.ndarray]:
    """Move an image and its mask by a lag, with bilinear interpolation.

    Parameters
    ----------
    residual : `numpy.ndarray`
        The image.
    mask : `numpy.ndarray`
        Which pixels are in the sample.
    row_lag, column_lag : `float`
        The lag. A pixel at position x receives the value from x + lag.

    Returns
    -------
    moved, moved_mask : `numpy.ndarray`
        The moved image and the pixels all of whose interpolation neighbors
        were in the mask.
    """
    offset = (-row_lag, -column_lag)
    moved = shift(residual, offset, order=1, mode="constant", cval=0.0)
    moved_mask = (
        shift(mask.astype(np.float32), offset, order=1, mode="constant", cval=0.0) >= SHIFTED_MASK_LEVEL
    )
    return moved, moved_mask


def _bootstrap_indices(tile_count: int) -> np.ndarray:
    """Draw the tiles of every bootstrap resample.

    Parameters
    ----------
    tile_count : `int`
        Tiles available.

    Returns
    -------
    indices : `numpy.ndarray`
        An array of shape (`BOOTSTRAP_COUNT`, tile_count) of tile numbers,
        drawn with replacement from a generator with a fixed seed.
    """
    generator = np.random.default_rng(BOOTSTRAP_SEED)
    return generator.integers(0, tile_count, size=(BOOTSTRAP_COUNT, tile_count))


def measure_half_pattern(
    odd_plane: np.ndarray,
    even_plane: np.ndarray,
    *,
    variance_factor: float | None,
    shared_fractions: tuple[float | None, float | None],
    effective_count: float | None,
    drift_axis_deg: float | None,
    drift_extent_px: float | None,
    drift_reason: str = "",
) -> HalfPatternOutcome:
    """Measure what two half stacks share at the scale of a few pixels.

    Parameters
    ----------
    odd_plane, even_plane : `numpy.ndarray`
        The two half stacks as 2-D images on one pixel grid.
    variance_factor : `float` or `None`
        The difference's variance in one frame's variances
        (`subset_noise_run.difference_variance_factor`).
    shared_fractions : `tuple` [`float` or `None`, `float` or `None`]
        The registration reference frame's share of the odd and of the even
        half's weight.
    effective_count : `float` or `None`
        The library stack's effective frame count.
    drift_axis_deg : `float` or `None`
        The drift axis from the column axis toward the row axis, in degrees.
    drift_extent_px : `float` or `None`
        The range the pointings cover along the axis.
    drift_reason : `str`, optional
        Why there is no axis, when there is none.

    Returns
    -------
    outcome : `HalfPatternOutcome`
        The numbers, or the reason there are none. The lagged correlations
        are missing, with the reason, when the drift axis or extent is
        unknown or the extent is shorter than `MINIMUM_EXTENT_IN_LAGS` lags.
    """
    if odd_plane.shape != even_plane.shape:
        return HalfPatternOutcome(None, "the two half stacks are on different pixel grids")
    if variance_factor is None or effective_count is None or None in shared_fractions:
        return HalfPatternOutcome(
            None,
            "the halves' weights are unknown, so the random noise of the shared frame cannot be taken out "
            "and the stack's random noise cannot be scaled",
        )
    usable = np.isfinite(odd_plane) & np.isfinite(even_plane) & (odd_plane != 0) & (even_plane != 0)
    if not usable.any():
        return HalfPatternOutcome(None, "the half stacks hold no usable pixels")
    detection = odd_plane + even_plane
    detection *= 0.5
    detection[~usable] = 0.0
    distance = analyze_faint_sources(detection).distance
    del detection
    margin = math.ceil(3.0 * FINE_SCALE_SIGMA_PX)
    mask = binary_erosion(usable, iterations=margin) & (distance > SOURCE_CLEARANCE_PX)
    del distance
    pixel_count = int(mask.sum())
    if pixel_count < MINIMUM_PIXELS:
        return HalfPatternOutcome(
            None,
            f"only {pixel_count} pixels are left clear of sources and the edge; "
            f"at least {MINIMUM_PIXELS} are needed",
        )
    odd = _fine_scale(odd_plane, usable)
    even = _fine_scale(even_plane, usable)
    del usable
    odd -= np.float32(odd[mask].mean(dtype=np.float64))
    even -= np.float32(even[mask].mean(dtype=np.float64))

    # Per-tile sums at lag 0: count, odd x even, odd squared, even squared.
    count = _tile_counts(mask)
    cross = _tile_sums(odd * even, mask)
    odd_square = _tile_sums(odd * odd, mask)
    even_square = _tile_sums(even * even, mask)
    used = count > 0
    if int(used.sum()) < MINIMUM_TILES:
        return HalfPatternOutcome(
            None, f"only {int(used.sum())} tiles hold usable pixels; {MINIMUM_TILES} are needed"
        )
    count, cross, odd_square, even_square = (array[used] for array in (count, cross, odd_square, even_square))
    shared_odd, shared_even = (float(value) for value in shared_fractions if value is not None)

    def lag_zero_terms(selection: np.ndarray) -> tuple[float, float, float, float, float, float]:
        """Work out the pattern and random variances from tile sums.

        Parameters
        ----------
        selection : `numpy.ndarray`
            Tile numbers (with repeats for a bootstrap draw).

        Returns
        -------
        terms : `tuple` [`float`, `float`, `float`, `float`, `float`, `float`]
            The covariance (c), the difference's variance (D), the reference
            frame's random part (D / F x a x b), the pattern variance (c minus
            that part), the stack's random variance (D / F / N), and the
            geometric mean of the halves' variances.
        """
        total = count[selection].sum()
        covariance = cross[selection].sum() / total
        difference_variance = (
            odd_square[selection].sum() + even_square[selection].sum() - 2.0 * cross[selection].sum()
        ) / total
        frame_variance = difference_variance / variance_factor
        correction = frame_variance * shared_odd * shared_even
        random_variance = frame_variance / effective_count
        halves = math.sqrt((odd_square[selection].sum() / total) * (even_square[selection].sum() / total))
        return (
            float(covariance),
            float(difference_variance),
            float(correction),
            float(covariance - correction),
            float(random_variance),
            float(halves),
        )

    everything = np.arange(count.size)
    covariance, difference_variance, correction, pattern, random_variance, halves = lag_zero_terms(everything)
    if not (random_variance > 0.0 and halves > 0.0):
        return HalfPatternOutcome(None, "the halves' difference has no variance")
    draws = _bootstrap_indices(count.size)
    covariance_draws = np.empty(BOOTSTRAP_COUNT)
    difference_draws = np.empty(BOOTSTRAP_COUNT)
    correction_draws = np.empty(BOOTSTRAP_COUNT)
    pattern_draws = np.empty(BOOTSTRAP_COUNT)
    ratio_draws = np.empty(BOOTSTRAP_COUNT)
    for index in range(BOOTSTRAP_COUNT):
        (
            covariance_draws[index],
            difference_draws[index],
            correction_draws[index],
            pattern_draws[index],
            draw_random,
            _,
        ) = lag_zero_terms(draws[index])
        ratio_draws[index] = pattern_draws[index] / draw_random if draw_random > 0 else math.nan
    rms = math.sqrt(pattern) if pattern > 0.0 else None
    rms_draws = np.sqrt(np.clip(pattern_draws, 0.0, None))
    result_kwargs = {
        "pixel_count": pixel_count,
        "tile_count": int(count.size),
        "shared_variance": pattern,
        "shared_variance_error": float(np.std(pattern_draws, ddof=1)),
        "covariance": covariance,
        "covariance_error": float(np.std(covariance_draws, ddof=1)),
        "difference_variance": difference_variance,
        "difference_variance_error": float(np.std(difference_draws, ddof=1)),
        "reference_correction": correction,
        "reference_correction_error": float(np.std(correction_draws, ddof=1)),
        "random_variance_full_stack": random_variance,
        "variance_ratio": pattern / random_variance,
        "variance_ratio_error": float(np.nanstd(ratio_draws, ddof=1)),
        "rms": rms,
        "rms_error": float(np.std(rms_draws, ddof=1)) if rms is not None else None,
    }

    walking_reason = ""
    if drift_axis_deg is None or drift_extent_px is None:
        walking_reason = drift_reason or "the drift axis of the frames is unknown"
    elif drift_extent_px < MINIMUM_EXTENT_IN_LAGS * WALKING_LAG_PX:
        walking_reason = (
            f"the pointings cover {drift_extent_px:.1f} px along the drift, less than "
            f"{MINIMUM_EXTENT_IN_LAGS:.0f} lags of {WALKING_LAG_PX:.0f} px, so a smear cannot reach the lag"
        )
    if walking_reason:
        return HalfPatternOutcome(HalfPatternResult(**result_kwargs, walking_reason=walking_reason))

    axis = math.radians(float(drift_axis_deg))
    along = (WALKING_LAG_PX * math.sin(axis), WALKING_LAG_PX * math.cos(axis))  # (row, column) lag
    across = (WALKING_LAG_PX * math.cos(axis), -WALKING_LAG_PX * math.sin(axis))
    lagged_counts: list[np.ndarray] = []
    lagged_cross: list[np.ndarray] = []
    for lag in (along, across):
        counts_sum = np.zeros(used.sum())
        cross_sum = np.zeros(used.sum())
        for sign in (1.0, -1.0):
            moved, moved_mask = _lagged(even, mask, sign * lag[0], sign * lag[1])
            both = mask & moved_mask
            counts_sum += _tile_counts(both)[used]
            cross_sum += _tile_sums(odd * moved, both)[used]
        lagged_counts.append(counts_sum)
        lagged_cross.append(cross_sum)

    def correlations(selection: np.ndarray) -> tuple[float, float]:
        """Work out the along and across correlations from tile sums.

        Parameters
        ----------
        selection : `numpy.ndarray`
            Tile numbers.

        Returns
        -------
        correlations : `tuple` [`float`, `float`]
            The correlation coefficients along and across the drift.
        """
        halves_draw = lag_zero_terms(selection)[5]
        values = []
        for counts_sum, cross_sum in zip(lagged_counts, lagged_cross, strict=True):
            total = counts_sum[selection].sum()
            values.append(
                float(cross_sum[selection].sum() / total / halves_draw)
                if total > 0 and halves_draw > 0
                else math.nan
            )
        return values[0], values[1]

    along_value, across_value = correlations(everything)
    along_draws = np.empty(BOOTSTRAP_COUNT)
    across_draws = np.empty(BOOTSTRAP_COUNT)
    for index in range(BOOTSTRAP_COUNT):
        along_draws[index], across_draws[index] = correlations(draws[index])
    excess_draws = along_draws - across_draws
    return HalfPatternOutcome(
        HalfPatternResult(
            **result_kwargs,
            along_correlation=along_value,
            across_correlation=across_value,
            excess=along_value - across_value,
            along_error=float(np.nanstd(along_draws, ddof=1)),
            across_error=float(np.nanstd(across_draws, ddof=1)),
            excess_error=float(np.nanstd(excess_draws, ddof=1)),
        )
    )


PATTERN_ROW_NAMES = (
    STACKING_HALF_SHARED_FINE_SCALE_RMS_STACK_UNIT,
    STACKING_HALF_SHARED_FINE_SCALE_VARIANCE_RATIO,
    STACKING_HALF_SHARED_FINE_SCALE_COVARIANCE_STACK_UNIT_SQUARED,
    STACKING_HALF_FINE_SCALE_DIFFERENCE_VARIANCE_STACK_UNIT_SQUARED,
    STACKING_HALF_SHARED_FINE_SCALE_REFERENCE_CORRECTION_STACK_UNIT_SQUARED,
    STACKING_WALKING_NOISE_ALONG_CORRELATION,
    STACKING_WALKING_NOISE_ACROSS_CORRELATION,
    STACKING_WALKING_NOISE_CORRELATION_EXCESS,
)


def half_pattern_measurements(
    context: PipelineRunContext,
    subject: QualitySubject,
    outcome: HalfPatternOutcome | None,
    reason: str,
    note: str = "",
) -> list[QualityMeasurement]:
    """Build the eight rows of the half-stack pattern measurement.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The library stack.
    outcome : `HalfPatternOutcome` or `None`
        What the measurement gave, or `None` when it was not tried.
    reason : `str`
        Why it was not tried or gave nothing. Used when ``outcome`` has no
        reason of its own.
    note : `str`, optional
        What was done, kept on each measured row.

    Returns
    -------
    rows : `list` of `QualityMeasurement`
        The root-mean-square size and the variance ratio of what the halves
        share, the three parts of the ratio (the covariance, the difference's
        variance and the reference-frame correction), then the along-drift and
        across-drift correlations and their difference. Each is not measured,
        with the reason, when it is missing. The bootstrap error of the ratio
        leaves out the uncertainty of the variance factor and of the
        effective frame count, and its row says so.
    """
    result = outcome.result if outcome is not None else None
    missing = (outcome.reason if outcome is not None else "") or reason or "the pattern was not measured"
    if result is None:
        return [
            build_measurement(_SPEC_BY_NAME[name], None, context, subject, reason=missing)
            for name in PATTERN_ROW_NAMES
        ]
    walking = result.walking_reason or "the lagged correlation was not measured"
    values = (
        (result.rms, result.rms_error, PATTERN_NOT_POSITIVE_REASON, ""),
        (result.variance_ratio, result.variance_ratio_error, missing, RATIO_ERROR_NOTE),
        (result.covariance, result.covariance_error, missing, ""),
        (result.difference_variance, result.difference_variance_error, missing, ""),
        (result.reference_correction, result.reference_correction_error, missing, CORRECTION_NOTE),
        (result.along_correlation, result.along_error, walking, ""),
        (result.across_correlation, result.across_error, walking, ""),
        (result.excess, result.excess_error, walking, ""),
    )
    detail = (
        f"{note} {result.pixel_count} pixels in {result.tile_count} tiles of {TILE_PX} px, "
        f"Gaussian high-pass sigma {FINE_SCALE_SIGMA_PX:.0f} px, lag {result.lag_px:.0f} px."
    ).strip()
    return [
        build_measurement(
            _SPEC_BY_NAME[name],
            value,
            context,
            subject,
            uncertainty=error,
            sample_count=result.pixel_count,
            reason=value_reason,
            provenance=f"{detail} {extra}".strip(),
        )
        for name, (value, error, value_reason, extra) in zip(PATTERN_ROW_NAMES, values, strict=True)
    ]
