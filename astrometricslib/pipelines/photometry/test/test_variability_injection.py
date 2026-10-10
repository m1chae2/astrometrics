"""Purpose: Injection test and AUC measurement for the variability indices.

Description: Review item S18 found that the coefficient of variation (CV) of
normalized flux, with a field-wide cutoff, had no power to tell variables
from constants (AUC 0.46 to 0.49 against catalog variables). These tests
build synthetic fields with `make_variability_field`: constant stars across
six magnitudes of brightness, plus 5 percent that carry an injected sinusoid
or eclipsing dip of 0.01 to 0.3 mag. Each field runs through the real
normalization, detrending and `identify_variable_stars`. The tests check
that:

* the variability score ranks the injected variables above the constants
  (AUC above 0.9 over six fields), and at most 2 percent of the constants
  are flagged;
* the score beats the CV by a wide margin on the same fields;
* every large sinusoid on a bright star is flagged, while a 0.01 mag
  signal on a faint star, below its noise, is not;
* a field with 5 percent of points at five times the noise still flags
  at most 2 percent of its constants.

Run this file as a script to print the AUC table that the photometry README
quotes::

    .venv/bin/python -m \
        astrometricslib.pipelines.photometry.test.test_variability_injection

The table is measured on synthetic data, not on the sky.
"""

import sys
from dataclasses import dataclass

import numpy as np
import pytest

from astrometricslib.pipelines.photometry.post_processing.variability_skill import area_under_curve
from astrometricslib.pipelines.photometry.processing.variability_analyzer import VariabilityAnalyzer
from astrometricslib.pipelines.photometry.processing.variability_indices import (
    extract_series,
    reduced_chi_square,
)
from astrometricslib.test.synthetic.variability_field import (
    SyntheticVariabilityField,
    make_variability_field,
)

# The seeds of the fields pooled in the test, and of those in the table.
TEST_SEEDS = tuple(range(100, 106))
TABLE_SEEDS = tuple(range(100, 124))

AUC_FLOOR = 0.9
MAXIMUM_CONSTANT_FLAG_FRACTION = 0.02

# Names of the columns of the table, in print order.
INDEX_NAMES = (
    "cv",
    "chi2_propagated_errors",
    "chi2_scaled_errors",
    "stetson_j",
    "excess_scatter",
    "min_ratio_of_three",
    "score",
)


@dataclass
class FieldRun:
    """A synthetic field after the pipeline has flagged it.

    Attributes
    ----------
    field : `SyntheticVariabilityField`
        The field and its truth.
    analyzer : `VariabilityAnalyzer`
        The analyzer after normalizing, detrending and flagging.
    indices : `dict` [`str`, `numpy.ndarray`]
        For the stars that have indices: each name in `INDEX_NAMES` mapped
        to one value per star.
    is_variable : `numpy.ndarray`
        The truth for those stars.
    """

    field: SyntheticVariabilityField
    analyzer: VariabilityAnalyzer
    indices: dict[str, np.ndarray]
    is_variable: np.ndarray


def run_field(seed: int, **options: float) -> FieldRun:
    """Build a field and run it through the pipeline's variability steps.

    Parameters
    ----------
    seed : `int`
        Seed of the field.
    **options
        Passed to `make_variability_field`.

    Returns
    -------
    run : `FieldRun`
        The field, the analyzer and the index of every star.
    """
    field = make_variability_field(seed=seed, **options)
    analyzer = VariabilityAnalyzer()
    analyzer.stellar_objects = field.stars
    analyzer.timestamp_to_path = {
        stamp: f"frame_{index:03d}.fits" for index, stamp in enumerate(field.timestamps)
    }
    analyzer.normalize_light_curves()
    analyzer.detrend_light_curves_airmass()
    analyzer.identify_variable_stars()
    thresholds = analyzer.variability_thresholds
    columns: dict[str, list[float]] = {name: [] for name in INDEX_NAMES}
    truth: list[bool] = []
    for index, star in enumerate(field.stars):
        photometry = star.photometry
        series = extract_series(star)
        if photometry.variability_score is None or series is None or series.errors is None:
            continue
        columns["cv"].append(photometry.coefficient_of_variation)
        columns["chi2_propagated_errors"].append(reduced_chi_square(series.values, series.errors))
        columns["chi2_scaled_errors"].append(photometry.reduced_chi_square)
        columns["stetson_j"].append(photometry.stetson_j)
        columns["excess_scatter"].append(photometry.excess_scatter)
        columns["min_ratio_of_three"].append(
            min(
                photometry.reduced_chi_square / thresholds.reduced_chi_square,
                photometry.stetson_j / thresholds.stetson_j,
                photometry.excess_scatter / thresholds.excess_scatter,
            )
        )
        columns["score"].append(photometry.variability_score)
        truth.append(bool(field.is_variable[index]))
    return FieldRun(
        field, analyzer, {name: np.array(values) for name, values in columns.items()}, np.array(truth)
    )


def auc_of(scores: np.ndarray, is_variable: np.ndarray) -> float:
    """Give the chance a variable outscores a constant.

    Returns
    -------
    auc : `float`
        The Mann-Whitney probability, ties counting half.
    """
    return float(area_under_curve(scores[is_variable], scores[~is_variable]))


@pytest.fixture(scope="module")
def runs() -> list[FieldRun]:
    """Run the test fields once for the whole module.

    Returns
    -------
    runs : `list` [`FieldRun`]
        One run per seed in `TEST_SEEDS`.
    """
    return [run_field(seed) for seed in TEST_SEEDS]


def pooled(runs: list[FieldRun], name: str) -> tuple[np.ndarray, np.ndarray]:
    """Pool one index over several fields.

    Returns
    -------
    values, is_variable : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        The index of every star of every field, and the truth.
    """
    return (
        np.concatenate([run.indices[name] for run in runs]),
        np.concatenate([run.is_variable for run in runs]),
    )


def test_the_score_recovers_the_injected_variables_with_an_auc_above_nine_tenths(
    runs: list[FieldRun],
) -> None:
    """Sinusoids and eclipses of 0.01 to 0.3 mag rank above the constants."""
    score, is_variable = pooled(runs, "score")

    assert is_variable.sum() >= 50
    assert auc_of(score, is_variable) > AUC_FLOOR


def test_at_most_two_percent_of_the_constant_stars_are_flagged(runs: list[FieldRun]) -> None:
    """The candidate rule rarely flags a constant star, at any brightness."""
    flagged_constants = 0
    constants = 0
    for run in runs:
        flagged_ids = {star.id for star in run.analyzer.identify_variable_stars()}
        for index, star in enumerate(run.field.stars):
            if not run.field.is_variable[index]:
                constants += 1
                flagged_constants += star.id in flagged_ids

    assert constants > 1000
    assert flagged_constants / constants <= MAXIMUM_CONSTANT_FLAG_FRACTION


def test_the_score_ranks_far_better_than_the_cv_on_the_same_fields(runs: list[FieldRun]) -> None:
    """The CV mixes noise with variability; the score does not."""
    score_auc = auc_of(*pooled(runs, "score"))
    cv_auc = auc_of(*pooled(runs, "cv"))

    assert score_auc - cv_auc > 0.05


def test_every_large_sinusoid_on_a_bright_star_is_flagged(
    runs: list[FieldRun],
) -> None:
    """Every sinusoid of 0.1 mag or more on a bright star is flagged."""
    missed = []
    for run in runs:
        flagged_ids = {star.id for star in run.analyzer.identify_variable_stars()}
        half = np.median(run.field.true_magnitudes)
        for entry in run.field.injected:
            star = run.field.stars[entry.star_index]
            bright = run.field.true_magnitudes[entry.star_index] < half
            if (
                bright
                and entry.kind == "sinusoid"
                and entry.amplitude_mag >= 0.1
                and star.id not in flagged_ids
            ):
                missed.append((entry, star.id))

    assert missed == []


def test_variables_below_the_noise_are_not_flagged() -> None:
    """A 0.01 mag signal on a faint star is below its noise: unflagged."""
    run = run_field(7, variable_fraction=0.1, amplitude_range_mag=(0.01, 0.011))
    flagged_ids = {star.id for star in run.analyzer.identify_variable_stars()}
    faint_half = np.median(run.field.true_magnitudes)
    faint_injected = [
        entry
        for entry in run.field.injected
        if run.field.true_magnitudes[entry.star_index] > faint_half + 1.0
    ]

    assert faint_injected
    assert all(run.field.stars[entry.star_index].id not in flagged_ids for entry in faint_injected)


def test_a_field_with_outliers_in_the_points_still_flags_few_constants() -> None:
    """With 5 percent of points at five times the noise, few constants flag."""
    run = run_field(3, outlier_fraction=0.05)
    flagged = {star.id for star in run.analyzer.identify_variable_stars()}
    constants = [star for index, star in enumerate(run.field.stars) if not run.field.is_variable[index]]

    assert sum(star.id in flagged for star in constants) / len(constants) <= MAXIMUM_CONSTANT_FLAG_FRACTION


def measure_auc_table(seeds: tuple[int, ...] = TABLE_SEEDS) -> dict[str, tuple[float, float]]:
    """Measure the AUC of each index over several synthetic fields.

    Parameters
    ----------
    seeds : `tuple` [`int`], optional
        Seeds of the fields.

    Returns
    -------
    table : `dict` [`str`, `tuple` [`float`, `float`]]
        For each name in `INDEX_NAMES`: the mean AUC over the fields and its
        standard deviation between fields.
    """
    aucs: dict[str, list[float]] = {name: [] for name in INDEX_NAMES}
    for seed in seeds:
        run = run_field(seed)
        for name in INDEX_NAMES:
            aucs[name].append(auc_of(run.indices[name], run.is_variable))
    return {name: (float(np.mean(values)), float(np.std(values))) for name, values in aucs.items()}


def measure_flag_rates(seeds: tuple[int, ...] = TABLE_SEEDS) -> tuple[float, float]:
    """Measure the share of constants and of variables the rule flags.

    Parameters
    ----------
    seeds : `tuple` [`int`], optional
        Seeds of the fields.

    Returns
    -------
    rates : `tuple` [`float`, `float`]
        The share of constant stars flagged, and the share of injected
        variables flagged, over all the fields.
    """
    constants = flagged_constants = variables = flagged_variables = 0
    for seed in seeds:
        run = run_field(seed)
        flagged = run.indices["score"] > 1.0
        constants += int((~run.is_variable).sum())
        flagged_constants += int((flagged & ~run.is_variable).sum())
        variables += int(run.is_variable.sum())
        flagged_variables += int((flagged & run.is_variable).sum())
    return flagged_constants / constants, flagged_variables / variables


if __name__ == "__main__":
    table = measure_auc_table()
    sys.stdout.write(f"AUC over {len(TABLE_SEEDS)} synthetic fields (mean, sd between fields)\n")
    for index_name, (mean, spread) in table.items():
        sys.stdout.write(f"{index_name:26s} {mean:.3f}  {spread:.3f}\n")
    constant_rate, variable_rate = measure_flag_rates()
    sys.stdout.write(
        f"flagged: {constant_rate:.4f} of constants, {variable_rate:.3f} of injected variables\n"
    )
