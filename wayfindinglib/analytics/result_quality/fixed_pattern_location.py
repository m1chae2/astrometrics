"""Purpose: Say where a stack's fixed-pattern noise is, and what explains it.

Description: One number cannot say whether the extra noise of a stack is a
gradient, a flat-field residual, faint real light or banding. The odd-even
difference (see ``noise_growth.py``) gives two numbers at each empty aperture:

* the sum value, taken on the two half stacks added (A + B). It holds the
  random noise of both halves and whatever both halves share, which includes
  the fixed-pattern noise.
* the difference value, taken on A - B. What the halves share cancels, so it
  holds the random noise only.

The two halves are independent, so A + B and A - B have the same random
variance: ``Var(A + B) = Var(A) + Var(B) = Var(A - B)``. No rescaling is
needed to compare the sum with the difference aperture by aperture. (The
factor ``1 / sqrt(N_eff F)`` that the full-stack equivalent uses turns a
difference into the full stack's noise. It would multiply the sum and the
difference alike, so it cancels in every test below.) The excess

    e_i = s_i^2 - d_i^2

is zero on average when there is no fixed pattern, and it is positive where
a fixed pattern adds to the sum. A fitted plane is first taken out of the sums
and of the differences, so a gradient (tested on its own, shape 2) does not
leak into the radial and near-target tests.

Four shapes are tested (decision D21). Each test gives a significance in
standard errors, and a shape counts as found at `SHAPE_SIGMAS`:

1. Radial trend. The Spearman rank correlation of ``e_i`` with the aperture's
   distance from the image center, two-sided. A flat-field or vignetting
   residual grows or shrinks with the radius. Significance:
   ``rho sqrt(n - 1)``.
2. Gradient. A plane ``a + b x + c y`` is fitted to the sums and to the
   differences by least squares. The two slope pairs are independent, so the
   difference of the slopes has the sum of their covariances; the Wald
   statistic of that difference has two degrees of freedom and is turned into
   standard errors through its tail probability. A sky gradient is in the sum
   and not in the difference.
3. Near the target. The Spearman correlation of ``e_i`` with the distance from
   the target, one-sided (the excess is larger nearby). Faint real light
   around the target, such as a nebula, is not noise that a calibration frame
   can remove. This is explained in the loss account and gives no advice.
4. Banding. The scatter of the row (and of the column) averages of the sum
   image over that of the difference image, on the log scale, one-sided. The
   line-pattern rows of the odd-even difference job store the four scatters.

When the target sits near the image center, the distance from the target and
the distance from the center carry the same information. Each is then tested
with the other held fixed (a partial rank correlation) unless the two are
nearly the same (`COLLINEAR_DISTANCE_CORRELATION`). In that case a central
excess is called "near the target" and the radial finding is marked ambiguous
and left out of the advice, because asking for new flats for faint real light
would waste an evening.

Calibration frames as a known part (decision D22). The master flat's noise and
the master dark's noise enter every frame alike, so they are fixed-pattern
noise that the library predicts. `explain_with_calibration_masters` says how
much of the measured fixed-pattern variance they explain and what is left.
All three are variances in the stack's own units squared
(``stacking.flat_noise_in_aperture_stack_unit`` and
``stacking.expected_master_dark_noise_stack_unit`` are noises of an aperture
sum in the unit of ``stacking.empty_aperture_noise_stack_unit``, which the
fixed-pattern noise is also in), so they add directly.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from enum import StrEnum

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from scipy import stats

from wayfindinglib.analytics.result_quality.noise_growth import (
    MODEL_AGREEMENT_SIGMAS,
    aperture_noise_relative_error,
)

_MODEL_CONFIG = ConfigDict(populate_by_name=True, alias_generator=to_camel)

FIXED_PATTERN_LOCATION_ENTRY = "WAY-D32"
"""Register row behind the shape tests (a design estimate; a row of its own is
to be added)."""

SHAPE_SIGMAS = 2.0
"""A shape is called found when its test is above 0 by this many standard
errors. It equals `MODEL_AGREEMENT_SIGMAS` on purpose, so the shape tests and
the test that found the fixed-pattern noise use one width. Four shapes are
tested, so on a stack with no pattern the chance that some shape passes by
luck is about 4 times 2.3 percent (about 9 percent) one-sided, a little more
for the two-sided radial test; the advice is a recommendation to look, not an
order. Design estimate (decision D21, 2026-10-26); not tuned on data, register
row to be added."""

MINIMUM_SHAPE_APERTURES = 20
"""The aperture tests need at least this many apertures with both values. The
rank correlation's standard error ``1 / sqrt(n - 1)`` is then 0.23, so a
correlation of 0.46 is the least that reaches `SHAPE_SIGMAS`. The same minimum
as the number of lines the line-pattern scatter needs (``MINIMUM_LINES`` in
astrometricslib). Design estimate."""

COLLINEAR_DISTANCE_CORRELATION = 0.9
"""The distance from the target and the distance from the center are called
the same measurement when their rank correlation is at least this. Above it
the partial correlation divides by ``sqrt(1 - 0.81) = 0.44`` or less and
amplifies noise more than 2 times. Design estimate."""

MAXIMUM_REPORTED_SIGMAS = 40.0
"""A significance is capped here, because a tail probability below the
double-precision floor would otherwise read as infinity."""

FIXED_PATTERN_SHAPE_ADVICE_ENTRY = "WAY-D32"
"""Register row behind the advice each shape gives (a design estimate)."""


class FixedPatternShape(StrEnum):
    """The four places a fixed pattern can show up."""

    RADIAL_TREND = "radial_trend"
    GRADIENT = "gradient"
    NEAR_TARGET = "near_target"
    BANDING = "banding"


SHAPE_MEANING = {
    FixedPatternShape.RADIAL_TREND: (
        "a trend with distance from the image center (a flat-field or vignetting residual)"
    ),
    FixedPatternShape.GRADIENT: "a gradient across the image (a sky or background gradient)",
    FixedPatternShape.NEAR_TARGET: (
        "a rise near the target (faint real light around it, not a calibration fault)"
    ),
    FixedPatternShape.BANDING: "bands along rows or columns (line pattern noise)",
}
"""What each shape means, as a phrase for the loss account."""


class FixedPatternAperture(BaseModel):
    """One empty aperture's place and its two values.

    Parameters
    ----------
    x, y : `float`
        The aperture's column and row on the stack, in pixels.
    distance_from_center_px : `float`
        The distance from the image center, in pixels.
    distance_from_target_px : `float` or `None`
        The distance from the target, or ``None`` when its position is unknown.
    sum_value : `float`
        The sky-subtracted aperture sum on A + B, in stack units.
    difference_value : `float`
        The sky-subtracted aperture sum on A - B, in stack units.
    """

    model_config = _MODEL_CONFIG

    x: float
    y: float
    distance_from_center_px: float
    distance_from_target_px: float | None = None
    sum_value: float
    difference_value: float


class LinePatternScatters(BaseModel):
    """The scatter of the row and column averages of the sum and difference.

    Parameters
    ----------
    difference_row, difference_column, sum_row, sum_column : `float`
        The four ``stacking.odd_even_*_average_scatter_stack_unit`` values.
    row_count, column_count : `int`
        How many rows (columns) gave a mean, from the rows' sample counts.
    """

    model_config = _MODEL_CONFIG

    difference_row: float = Field(gt=0.0)
    difference_column: float = Field(gt=0.0)
    sum_row: float = Field(gt=0.0)
    sum_column: float = Field(gt=0.0)
    row_count: int = Field(ge=2)
    column_count: int = Field(ge=2)


class ShapeTest(BaseModel):
    """The result of one shape test.

    Parameters
    ----------
    shape : `FixedPatternShape`
        The shape.
    tested : `bool`
        False when the test could not be made (too few apertures, no target
        position, no line-pattern rows); `reason` then says why.
    sigmas : `float` or `None`
        How many standard errors the test found, in the direction that means
        "fixed pattern" (negative values are kept). ``None`` when not tested.
    significant : `bool`
        True when `sigmas` is at least `SHAPE_SIGMAS`.
    ambiguous : `bool`
        True when the finding cannot be told from a rise near the target
        (radial trend only). An ambiguous test is not `significant`.
    statistic : `str`
        The statistic in words, with its value.
    reason : `str`
        One sentence saying what was found or why the test was not made.
    """

    model_config = _MODEL_CONFIG

    shape: FixedPatternShape
    tested: bool = True
    sigmas: float | None = None
    significant: bool = False
    ambiguous: bool = False
    statistic: str = ""
    reason: str


class FixedPatternLocation(BaseModel):
    """Where a stack's fixed-pattern noise is, by the four shape tests.

    Parameters
    ----------
    aperture_count : `int`
        The apertures used by the aperture tests.
    tests : `list` [`ShapeTest`]
        One result for each shape.
    """

    model_config = _MODEL_CONFIG

    aperture_count: int = Field(ge=0)
    tests: list[ShapeTest]

    @property
    def significant_shapes(self) -> list[FixedPatternShape]:
        """List the shapes that were found.

        Returns
        -------
        shapes : `list` [`FixedPatternShape`]
            The significant shapes, in the order of the tests.
        """
        return [test.shape for test in self.tests if test.significant]

    def test_for(self, shape: FixedPatternShape) -> ShapeTest | None:
        """Give the test of one shape.

        Parameters
        ----------
        shape : `FixedPatternShape`
            The shape.

        Returns
        -------
        test : `ShapeTest` or `None`
            Its result, or ``None`` when there is none.
        """
        return next((test for test in self.tests if test.shape == shape), None)

    def sentence(self) -> str:
        """Say where the fixed pattern is, in one or two sentences.

        Returns
        -------
        text : `str`
            The significant shapes with their significance, or a sentence
            saying that none of the tested shapes was found.
        """
        found = [test for test in self.tests if test.significant]
        if found:
            parts = [f"{SHAPE_MEANING[test.shape]} ({test.sigmas:.1f} standard errors)" for test in found]
            return "The fixed pattern shows " + "; and ".join(parts) + "."
        ambiguous = [test for test in self.tests if test.ambiguous]
        tested = [test for test in self.tests if test.tested]
        suffix = ""
        if ambiguous:
            suffix = (
                " A trend from the center cannot be told from a rise near the target, because the target "
                "is near the center."
            )
        if not tested:
            return "Where the fixed pattern is could not be tested: " + self.tests[0].reason
        return (
            f"None of the tested shapes (trend from the center, gradient, rise near the target, banding) "
            f"was found at {SHAPE_SIGMAS:g} standard errors.{suffix}"
        )


class CalibrationNoiseBreakdown(BaseModel):
    """How much of the fixed-pattern variance the calibration masters explain.

    Parameters
    ----------
    fixed_pattern_variance : `float`
        The measured fixed-pattern variance, in stack units squared.
    fixed_pattern_variance_error : `float`
        Its one standard error, in the same unit.
    flat_variance, dark_variance : `float` or `None`
        The master flat's and the master dark's predicted variance in the
        aperture (the stored noise squared), in stack units squared; ``None``
        when the stack has no such value.
    explained_variance : `float`
        The sum of the two known variances.
    remaining_variance : `float`
        `fixed_pattern_variance` minus `explained_variance`. Negative when the
        masters' prediction is larger than the whole measured excess.
    remaining_sigmas : `float`
        `remaining_variance` over `fixed_pattern_variance_error`.
    consistent_with_zero : `bool`
        True when `remaining_variance` is within `MODEL_AGREEMENT_SIGMAS`
        standard errors of 0 on either side.
    """

    model_config = _MODEL_CONFIG

    fixed_pattern_variance: float
    fixed_pattern_variance_error: float
    flat_variance: float | None = None
    dark_variance: float | None = None
    explained_variance: float
    remaining_variance: float
    remaining_sigmas: float
    consistent_with_zero: bool

    def sentence(self) -> str:
        """Say how much the masters explain and what is left.

        Returns
        -------
        text : `str`
            One or two sentences with the shares and the remainder. The dark
            figure is called a lower bound.
        """
        total = self.fixed_pattern_variance
        pieces = []
        if self.flat_variance is not None:
            pieces.append(f"the master flat's noise {100 * self.flat_variance / total:.0f}%")
        if self.dark_variance is not None:
            pieces.append(
                f"the master dark's noise {100 * self.dark_variance / total:.0f}% (a lower bound: the dark "
                "current's shot noise is not counted)"
            )
        text = (
            "Of the fixed-pattern variance, " + " and ".join(pieces) + " explain "
            f"{100 * self.explained_variance / total:.0f}% together; "
            f"{self.remaining_variance:.3g} (stack units squared, "
            f"{self.remaining_sigmas:.1f} standard errors) "
            "is left."
        )
        if self.consistent_with_zero:
            text += " The remaining fixed-pattern noise is consistent with zero."
        elif self.remaining_variance < 0.0:
            text += " The masters' predicted noise is larger than the measured excess."
        return text


class FixedPatternAccount(BaseModel):
    """What is known about one stack's fixed-pattern noise: where, and why.

    Parameters
    ----------
    location : `FixedPatternLocation` or `None`
        The shape tests, when the stack has a per-aperture record.
    calibration : `CalibrationNoiseBreakdown` or `None`
        The masters' share, when the stack has a measured fixed-pattern
        variance and a master noise.
    """

    model_config = _MODEL_CONFIG

    location: FixedPatternLocation | None = None
    calibration: CalibrationNoiseBreakdown | None = None

    def sentences(self) -> list[str]:
        """List the sentences that describe the fixed pattern.

        Returns
        -------
        sentences : `list` [`str`]
            The location sentence, then the calibration sentence, each only
            when it exists.
        """
        found = []
        if self.location is not None:
            found.append(self.location.sentence())
        if self.calibration is not None:
            found.append(self.calibration.sentence())
        return found

    @property
    def text(self) -> str:
        """Join the sentences into one paragraph.

        Returns
        -------
        text : `str`
            The sentences, or an empty string when there are none.
        """
        return " ".join(self.sentences())


def apertures_from_record(record: Mapping[str, object] | None) -> list[FixedPatternAperture]:
    """Read the apertures of a stored per-aperture record.

    Parameters
    ----------
    record : `Mapping` or `None`
        What ``astrometricslib.load_odd_even_aperture_record`` returns.

    Returns
    -------
    apertures : `list` [`FixedPatternAperture`]
        One entry for each aperture with finite numbers; empty for ``None``.
    """
    if not record:
        return []
    entries = record.get("apertures")
    if not isinstance(entries, list):
        return []
    apertures = []
    for entry in entries:
        try:
            target_distance = entry.get("distance_from_target_px")
            aperture = FixedPatternAperture(
                x=float(entry["x"]),
                y=float(entry["y"]),
                distance_from_center_px=float(entry["distance_from_center_px"]),
                distance_from_target_px=None if target_distance is None else float(target_distance),
                sum_value=float(entry["sum"]),
                difference_value=float(entry["difference"]),
            )
        except KeyError, TypeError, ValueError, AttributeError:
            continue
        values = (aperture.x, aperture.y, aperture.distance_from_center_px, aperture.sum_value)
        if all(math.isfinite(value) for value in (*values, aperture.difference_value)):
            apertures.append(aperture)
    return apertures


def _sigmas_from_z(z_value: float) -> float:
    """Cap a significance so that it stays finite.

    Parameters
    ----------
    z_value : `float`
        A significance in standard errors.

    Returns
    -------
    sigmas : `float`
        The same, limited to `MAXIMUM_REPORTED_SIGMAS` in size.
    """
    return float(max(-MAXIMUM_REPORTED_SIGMAS, min(MAXIMUM_REPORTED_SIGMAS, z_value)))


def _rank_correlation(first: np.ndarray, second: np.ndarray) -> float:
    """Take the Spearman rank correlation of two samples.

    Parameters
    ----------
    first, second : `numpy.ndarray`
        Two samples of the same length.

    Returns
    -------
    rho : `float`
        The Pearson correlation of the average ranks; 0 when either sample is
        constant.
    """
    ranks_first = stats.rankdata(first)
    ranks_second = stats.rankdata(second)
    if np.isclose(np.ptp(ranks_first), 0.0) or np.isclose(np.ptp(ranks_second), 0.0):
        return 0.0
    return float(np.corrcoef(ranks_first, ranks_second)[0, 1])


def _partial_rank_correlation(first: np.ndarray, second: np.ndarray, control: np.ndarray) -> float:
    """Take the rank correlation of two samples with a third held fixed.

    Parameters
    ----------
    first, second, control : `numpy.ndarray`
        Three samples of the same length.

    Returns
    -------
    rho : `float`
        ``(r12 - r13 r23) / sqrt((1 - r13^2)(1 - r23^2))`` from the three
        rank correlations; 0 when the control is the same as one sample.
    """
    r_first_second = _rank_correlation(first, second)
    r_first_control = _rank_correlation(first, control)
    r_second_control = _rank_correlation(second, control)
    denominator = math.sqrt(max(0.0, (1.0 - r_first_control**2) * (1.0 - r_second_control**2)))
    if denominator < 1e-9:
        return 0.0
    return float(np.clip((r_first_second - r_first_control * r_second_control) / denominator, -1.0, 1.0))


def _plane_fit(
    columns: np.ndarray, rows: np.ndarray, values: np.ndarray
) -> tuple[np.ndarray, float, np.ndarray]:
    """Fit ``a + b x + c y`` to values by least squares.

    Parameters
    ----------
    columns, rows : `numpy.ndarray`
        The positions, already centered and scaled to about unit size.
    values : `numpy.ndarray`
        The values.

    Returns
    -------
    slopes, variance, covariance_factor : `tuple`
        The slopes ``(b, c)``, the residual variance (``RSS / (n - 3)``), and
        the lower-right 2 x 2 block of ``(X'X)^-1``, so the slopes' covariance
        is ``variance * covariance_factor``.
    """
    design = np.column_stack([np.ones_like(columns), columns, rows])
    coefficients, _residuals, _rank, _singular = np.linalg.lstsq(design, values, rcond=None)
    residual = values - design @ coefficients
    degrees = max(1, len(values) - 3)
    variance = float(residual @ residual) / degrees
    factor = np.linalg.pinv(design.T @ design)[1:, 1:]
    return coefficients[1:], variance, factor


def _centered_positions(apertures: Sequence[FixedPatternAperture]) -> tuple[np.ndarray, np.ndarray]:
    """Give the aperture positions centered and scaled to about unit size.

    Parameters
    ----------
    apertures : `Sequence` [`FixedPatternAperture`]
        The apertures.

    Returns
    -------
    columns, rows : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        The columns and rows, minus their means and divided by the larger
        range, so a plane fit is well conditioned.
    """
    columns = np.array([a.x for a in apertures])
    rows = np.array([a.y for a in apertures])
    scale = max(float(np.ptp(columns)), float(np.ptp(rows)), 1.0)
    return (columns - columns.mean()) / scale, (rows - rows.mean()) / scale


def _plane_free_excess(apertures: Sequence[FixedPatternAperture]) -> np.ndarray:
    """Give ``s^2 - d^2`` after taking a fitted plane out of each value.

    A gradient adds a position-dependent offset to the sums. Left in, it would
    raise ``s^2`` far from the plane's zero line and look like a trend with
    the distance from the center. The plane (with its constant) is therefore
    fitted to the sums and to the differences separately and removed from
    each before the squares are taken. The gradient is tested on its own
    (`_gradient_test`).

    Parameters
    ----------
    apertures : `Sequence` [`FixedPatternAperture`]
        The apertures.

    Returns
    -------
    excess : `numpy.ndarray`
        ``s_res^2 - d_res^2`` for each aperture.
    """
    columns, rows = _centered_positions(apertures)
    design = np.column_stack([np.ones_like(columns), columns, rows])
    residuals = []
    for values in (
        np.array([a.sum_value for a in apertures]),
        np.array([a.difference_value for a in apertures]),
    ):
        coefficients, _residual, _rank, _singular = np.linalg.lstsq(design, values, rcond=None)
        residuals.append(values - design @ coefficients)
    return residuals[0] ** 2 - residuals[1] ** 2


def _radial_and_target_tests(apertures: Sequence[FixedPatternAperture]) -> tuple[ShapeTest, ShapeTest]:
    """Test the trend from the center and the rise near the target.

    Parameters
    ----------
    apertures : `Sequence` [`FixedPatternAperture`]
        At least `MINIMUM_SHAPE_APERTURES` apertures.

    Returns
    -------
    radial, near_target : `tuple` [`ShapeTest`, `ShapeTest`]
        The two results. The near-target test is not made without target
        distances.
    """
    excess = _plane_free_excess(apertures)
    center = np.array([a.distance_from_center_px for a in apertures])
    with_target = [a for a in apertures if a.distance_from_target_px is not None]
    use_target = len(with_target) == len(apertures) and len(apertures) >= MINIMUM_SHAPE_APERTURES
    target = (
        np.array([a.distance_from_target_px for a in apertures], dtype=float) if use_target else np.array([])
    )
    count = len(apertures)
    collinear = use_target and abs(_rank_correlation(center, target)) >= COLLINEAR_DISTANCE_CORRELATION
    if use_target and not collinear:
        radial_rho = _partial_rank_correlation(excess, center, target)
        radial_z = radial_rho * math.sqrt(max(1, count - 3))
        radial_note = " with the distance from the target held fixed"
    else:
        radial_rho = _rank_correlation(excess, center)
        radial_z = radial_rho * math.sqrt(count - 1)
        radial_note = ""
    radial_sigmas = _sigmas_from_z(abs(radial_z))
    direction = "grows" if radial_rho > 0.0 else "falls"
    radial = ShapeTest(
        shape=FixedPatternShape.RADIAL_TREND,
        sigmas=radial_sigmas,
        significant=radial_sigmas >= SHAPE_SIGMAS,
        statistic=(
            f"rank correlation of the excess with the distance from the center{radial_note}: "
            f"{radial_rho:+.2f}"
        ),
        reason=(
            f"The excess {direction} with the distance from the center "
            f"(rank correlation {radial_rho:+.2f}, {radial_sigmas:.1f} standard errors, {count} apertures)."
        ),
    )
    if not use_target:
        near = ShapeTest(
            shape=FixedPatternShape.NEAR_TARGET,
            tested=False,
            reason="the target's position on the stack is not known for every aperture.",
        )
        return radial, near
    if collinear:
        near_rho = _rank_correlation(excess, target)
        near_z = -near_rho * math.sqrt(count - 1)
        note = ""
    else:
        near_rho = _partial_rank_correlation(excess, target, center)
        near_z = -near_rho * math.sqrt(max(1, count - 3))
        note = " with the distance from the center held fixed"
    near_sigmas = _sigmas_from_z(near_z)
    near = ShapeTest(
        shape=FixedPatternShape.NEAR_TARGET,
        sigmas=near_sigmas,
        significant=near_sigmas >= SHAPE_SIGMAS,
        statistic=f"rank correlation of the excess with the distance from the target{note}: {near_rho:+.2f}",
        reason=(
            f"The excess {'grows' if near_rho < 0.0 else 'falls'} toward the target "
            f"(rank correlation with the distance {near_rho:+.2f}, {near_sigmas:.1f} standard errors)."
        ),
    )
    if collinear and near.significant and radial.significant:
        radial = radial.model_copy(
            update={
                "significant": False,
                "ambiguous": True,
                "reason": radial.reason
                + " It cannot be told from the rise near the target, because the target is near the center.",
            }
        )
    return radial, near


def _gradient_test(apertures: Sequence[FixedPatternAperture]) -> ShapeTest:
    """Test whether the sums hold a plane that the differences do not.

    Parameters
    ----------
    apertures : `Sequence` [`FixedPatternAperture`]
        At least `MINIMUM_SHAPE_APERTURES` apertures.

    Returns
    -------
    test : `ShapeTest`
        The gradient test: the slope pair of the sums minus that of the
        differences, as a Wald statistic with 2 degrees of freedom.
    """
    columns, rows = _centered_positions(apertures)
    sums = np.array([a.sum_value for a in apertures])
    differences = np.array([a.difference_value for a in apertures])
    slopes_sum, variance_sum, factor = _plane_fit(columns, rows, sums)
    slopes_difference, variance_difference, _factor = _plane_fit(columns, rows, differences)
    delta = slopes_sum - slopes_difference
    covariance = (variance_sum + variance_difference) * factor
    try:
        wald = float(delta @ np.linalg.solve(covariance, delta))
    except np.linalg.LinAlgError:
        return ShapeTest(
            shape=FixedPatternShape.GRADIENT,
            tested=False,
            reason="the apertures lie on a line, so no plane fits.",
        )
    probability = float(stats.chi2.sf(wald, 2))
    sigmas = _sigmas_from_z(float(stats.norm.isf(max(probability, 1e-300))))
    return ShapeTest(
        shape=FixedPatternShape.GRADIENT,
        sigmas=sigmas,
        significant=sigmas >= SHAPE_SIGMAS,
        statistic=(
            "Wald statistic of the plane's slope difference (sum minus difference): "
            f"{wald:.1f}, 2 degrees of freedom"
        ),
        reason=(
            f"The plane fitted to the sums differs from the one fitted to the differences "
            f"(Wald statistic {wald:.1f}, {sigmas:.1f} standard errors)."
        ),
    )


def _banding_test(scatters: LinePatternScatters | None) -> ShapeTest:
    """Test whether the sum's line averages scatter more than the difference's.

    Parameters
    ----------
    scatters : `LinePatternScatters` or `None`
        The four stored scatters, or ``None`` when they were not stored.

    Returns
    -------
    test : `ShapeTest`
        The larger of the row and column log ratios over its error. The error
        of each scatter is `aperture_noise_relative_error` of its line count
        (the same ``1.17 / sqrt(n)``), and the two errors add in quadrature.
    """
    if scatters is None:
        return ShapeTest(
            shape=FixedPatternShape.BANDING,
            tested=False,
            reason="the line-pattern rows of the odd-even difference are not stored for this stack.",
        )
    results = []
    for name, sum_value, difference_value, count in (
        ("rows", scatters.sum_row, scatters.difference_row, scatters.row_count),
        ("columns", scatters.sum_column, scatters.difference_column, scatters.column_count),
    ):
        error = math.sqrt(2.0) * aperture_noise_relative_error(count)
        ratio = sum_value / difference_value
        results.append((math.log(ratio) / error, name, ratio))
    sigmas, name, ratio = max(results)
    sigmas = _sigmas_from_z(sigmas)
    return ShapeTest(
        shape=FixedPatternShape.BANDING,
        sigmas=sigmas,
        significant=sigmas >= SHAPE_SIGMAS,
        statistic=f"scatter of the {name} averages, sum over difference: {ratio:.2f}",
        reason=(
            f"The {name} averages of the sum scatter {ratio:.2f} times as much as those of the difference "
            f"({sigmas:.1f} standard errors)."
        ),
    )


def locate_fixed_pattern(
    apertures: Sequence[FixedPatternAperture], scatters: LinePatternScatters | None = None
) -> FixedPatternLocation:
    """Test the four shapes of a stack's fixed pattern.

    Parameters
    ----------
    apertures : `Sequence` [`FixedPatternAperture`]
        The apertures of the stack's per-aperture record
        (`apertures_from_record`).
    scatters : `LinePatternScatters`, optional
        The stored line-pattern scatters. Without them the banding test is
        not made.

    Returns
    -------
    location : `FixedPatternLocation`
        The four results. With fewer than `MINIMUM_SHAPE_APERTURES` apertures
        the three aperture tests are not made.
    """
    count = len(apertures)
    if count < MINIMUM_SHAPE_APERTURES:
        too_few = (
            f"only {count} apertures have both values; the aperture tests need {MINIMUM_SHAPE_APERTURES}."
        )
        tests = [
            ShapeTest(shape=shape, tested=False, reason=too_few)
            for shape in (
                FixedPatternShape.RADIAL_TREND,
                FixedPatternShape.GRADIENT,
                FixedPatternShape.NEAR_TARGET,
            )
        ]
        tests.append(_banding_test(scatters))
        return FixedPatternLocation(aperture_count=count, tests=tests)
    radial, near = _radial_and_target_tests(apertures)
    return FixedPatternLocation(
        aperture_count=count, tests=[radial, _gradient_test(apertures), near, _banding_test(scatters)]
    )


def explain_with_calibration_masters(
    fixed_pattern_variance: float,
    fixed_pattern_variance_error: float,
    flat_noise: float | None,
    dark_noise: float | None,
) -> CalibrationNoiseBreakdown | None:
    """Say how much of the fixed-pattern variance the masters explain.

    Parameters
    ----------
    fixed_pattern_variance : `float`
        The measured fixed-pattern variance, in stack units squared
        (``StackFixedPatternNoise.fixed_pattern_variance``).
    fixed_pattern_variance_error : `float`
        Its one standard error, in the same unit. The masters' predictions
        carry no stored error, so this is the error of the remainder too.
    flat_noise : `float` or `None`
        ``stacking.flat_noise_in_aperture_stack_unit``, a noise in stack
        units. Its square is the master flat's variance.
    dark_noise : `float` or `None`
        ``stacking.expected_master_dark_noise_stack_unit``, a noise in stack
        units and a lower bound.

    Returns
    -------
    breakdown : `CalibrationNoiseBreakdown` or `None`
        ``None`` when the variance is not positive or neither master has a
        noise.
    """
    if fixed_pattern_variance <= 0.0 or fixed_pattern_variance_error <= 0.0:
        return None
    flat_variance = flat_noise**2 if flat_noise is not None and flat_noise > 0.0 else None
    dark_variance = dark_noise**2 if dark_noise is not None and dark_noise > 0.0 else None
    if flat_variance is None and dark_variance is None:
        return None
    explained = (flat_variance or 0.0) + (dark_variance or 0.0)
    remaining = fixed_pattern_variance - explained
    sigmas = remaining / fixed_pattern_variance_error
    return CalibrationNoiseBreakdown(
        fixed_pattern_variance=fixed_pattern_variance,
        fixed_pattern_variance_error=fixed_pattern_variance_error,
        flat_variance=flat_variance,
        dark_variance=dark_variance,
        explained_variance=explained,
        remaining_variance=remaining,
        remaining_sigmas=sigmas,
        consistent_with_zero=abs(sigmas) <= MODEL_AGREEMENT_SIGMAS,
    )


__all__ = [
    "COLLINEAR_DISTANCE_CORRELATION",
    "MINIMUM_SHAPE_APERTURES",
    "SHAPE_SIGMAS",
    "CalibrationNoiseBreakdown",
    "FixedPatternAccount",
    "FixedPatternAperture",
    "FixedPatternLocation",
    "FixedPatternShape",
    "LinePatternScatters",
    "ShapeTest",
    "apertures_from_record",
    "explain_with_calibration_masters",
    "locate_fixed_pattern",
]
