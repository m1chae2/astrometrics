"""Asks whether a run's variability score can see variables at all.

The photometry pipeline gives each star a variability score (see
`variability_indices`) and flags the stars whose score is above 1. Nothing
tested whether the score rises for stars that really vary. On the library
(measured 2026-10-09, `scripts/measure_variability_cutoff.py`) the old
score, the coefficient of variation (CV, the standard deviation over the
mean), did not: known variables had no more scatter than other stars of the
same brightness (AUC 0.49), because the photometry's own scatter (median
0.29 mag across targets) is larger than most variables' amplitudes.

Two checks make that visible for every run:

- `discrimination`: among the stars with a light curve in the run, does the
  score of the stars the catalogs list as variable tend to exceed the score
  of the stars no catalog lists? The measure is the AUC, the chance that a
  random catalogued variable has a higher score than a random unlisted star.
  A value of 0.5 is no skill. The AUC must exceed 0.5 by enough that chance
  would give that much in fewer than 5% of fields, and it must reach
  `MINIMUM_DISCRIMINATION_AUC`.
- `minimum_detectable_amplitude_mag`: how big a sinusoidal variable would have
  to be before its scatter clears the run's noise level. With a noise model,
  `minimum_detectable_amplitude_from_noise_mag` gives it from the field's
  expected scatter; `minimum_detectable_amplitude_mag` gives it from the CV
  cutoff for a field with no noise model.

Both describe the run's own field. A catalogued variable that is brighter than
its field is quieter, so the AUC is, if anything, pessimistic.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.stats import rankdata

from astrometricslib.pipelines.photometry.processing.variability_indices import EXCESS_SCATTER_THRESHOLD

# The fewest known variables and unlisted stars a field needs for an AUC to
# mean anything. With 10 and 30 the standard error of an AUC at chance is
# about 0.10, so the test below is not overstrict on a small field.
MINIMUM_KNOWN_VARIABLES = 10
MINIMUM_UNLISTED_STARS = 30

# The AUC the variability score must reach on the catalogued variables of a
# field, whatever the group sizes. A design choice, not a measurement: it is
# the "lifts the AUC above 0.7" success level of review item S18, and sits
# well above the 0.46 to 0.49 the CV gave on the library. A field that falls
# below it has a score that cannot be trusted to rank variables.
MINIMUM_DISCRIMINATION_AUC = 0.7

# The one-sided 5% point of the standard normal distribution. The AUC must
# exceed 0.5 by this many standard errors.
_ONE_SIDED_FIVE_PERCENT_Z = 1.645

# For a sinusoid of semi-amplitude A the scatter (standard deviation) is
# A / sqrt(2), so the peak-to-peak amplitude is 2 sqrt(2) times the scatter.
# Peak-to-peak brightness change as a fraction of the mean is converted to
# magnitudes with 2.5 / ln(10) = 1.0857 (valid for small changes).
_PEAK_TO_PEAK_PER_SCATTER = 2.0 * math.sqrt(2.0)
_MAGNITUDES_PER_FRACTION = 1.0857


@dataclass(frozen=True)
class DiscriminationResult:
    """Whether the scatter statistic separates known variables from the rest.

    Attributes
    ----------
    auc : `float`
        The chance a random known variable has more scatter than a random
        unlisted star.
    required_auc : `float`
        The AUC needed to beat chance at the 5% level for these group sizes.
        The result must also reach `MINIMUM_DISCRIMINATION_AUC`.
    known_variables : `int`
        How many catalogued variables were compared.
    unlisted_stars : `int`
        How many unlisted stars they were compared with.
    """

    auc: float
    required_auc: float
    known_variables: int
    unlisted_stars: int

    @property
    def needed_auc(self) -> float:
        """Give the AUC the field has to reach.

        Returns
        -------
        needed : `float`
            The larger of `required_auc` and `MINIMUM_DISCRIMINATION_AUC`.
        """
        return max(self.required_auc, MINIMUM_DISCRIMINATION_AUC)

    @property
    def sees_known_variables(self) -> bool:
        """Say whether the AUC beats chance and reaches the floor.

        Returns
        -------
        passes : `bool`
            True when `auc` is at least `needed_auc`.
        """
        return self.auc >= self.needed_auc


def area_under_curve(positives: Sequence[float], negatives: Sequence[float]) -> float | None:
    """Work out the chance a positive has a higher value than a negative.

    Parameters
    ----------
    positives : `Sequence` [`float`]
        Values for the stars that really vary.
    negatives : `Sequence` [`float`]
        Values for the stars the catalogs do not list.

    Returns
    -------
    auc : `float` or `None`
        The Mann-Whitney probability (ties count half), or `None` if either
        group is empty.
    """
    if not len(positives) or not len(negatives):
        return None
    ranks = rankdata(np.concatenate([positives, negatives]))
    count = len(positives)
    return float((float(np.sum(ranks[:count])) - count * (count + 1) / 2.0) / (count * len(negatives)))


def required_auc(known_variables: int, unlisted_stars: int) -> float:
    """Give the AUC needed to beat chance at the one-sided 5% level.

    Parameters
    ----------
    known_variables : `int`
        The size of the first group.
    unlisted_stars : `int`
        The size of the second group.

    Returns
    -------
    auc : `float`
        0.5 plus 1.645 standard errors of an AUC at chance.
    """
    standard_error = math.sqrt(
        (known_variables + unlisted_stars + 1) / (12.0 * known_variables * unlisted_stars)
    )
    return 0.5 + _ONE_SIDED_FIVE_PERCENT_Z * standard_error


def discrimination(
    known_scores: Sequence[float], unlisted_scores: Sequence[float]
) -> DiscriminationResult | None:
    """Test whether the score separates known variables from unlisted stars.

    Parameters
    ----------
    known_scores : `Sequence` [`float`]
        The variability score of each star the catalogs list as variable.
    unlisted_scores : `Sequence` [`float`]
        The variability score of each star no catalog lists.

    Returns
    -------
    result : `DiscriminationResult` or `None`
        `None` when either group is too small to say anything.
    """
    if len(known_scores) < MINIMUM_KNOWN_VARIABLES or len(unlisted_scores) < MINIMUM_UNLISTED_STARS:
        return None
    auc = area_under_curve(known_scores, unlisted_scores)
    if auc is None:
        return None
    return DiscriminationResult(
        auc=auc,
        required_auc=required_auc(len(known_scores), len(unlisted_scores)),
        known_variables=len(known_scores),
        unlisted_stars=len(unlisted_scores),
    )


def minimum_detectable_amplitude_mag(cutoff_cv: float) -> float:
    """Give the smallest sinusoidal variable that clears a cutoff.

    Parameters
    ----------
    cutoff_cv : `float`
        The run's cutoff on the coefficient of variation.

    Returns
    -------
    amplitude_mag : `float`
        The peak-to-peak amplitude, in magnitudes, of a sinusoid whose scatter
        equals the cutoff. A smaller variable is not flagged unless noise adds
        to it.
    """
    return _PEAK_TO_PEAK_PER_SCATTER * float(cutoff_cv) * _MAGNITUDES_PER_FRACTION


def minimum_detectable_amplitude_from_noise_mag(
    expected_rms_mag: float, excess_threshold: float = EXCESS_SCATTER_THRESHOLD
) -> float:
    """Give the smallest sinusoidal variable the candidate rule can flag.

    A constant star of the field's typical brightness scatters by
    ``expected_rms_mag``. A sinusoid of semi-amplitude ``a`` adds a scatter
    ``a / sqrt(2)`` in quadrature. The candidate rule needs the total to
    reach ``excess_threshold`` times the expected scatter, so
    ``a / sqrt(2) = expected_rms_mag * sqrt(excess_threshold**2 - 1)``, and
    the peak-to-peak amplitude is ``2 a``. This is the amplitude at which the
    excess-scatter condition is just met; the star must also pass the
    chi-square and Stetson J thresholds, so a real star needs slightly more.

    Parameters
    ----------
    expected_rms_mag : `float`
        The noise model's scatter for a typical star of the field, in
        magnitudes.
    excess_threshold : `float`, optional
        The excess-scatter threshold of the candidate rule.

    Returns
    -------
    amplitude_mag : `float`
        The peak-to-peak amplitude, in magnitudes.
    """
    return _PEAK_TO_PEAK_PER_SCATTER * float(expected_rms_mag) * math.sqrt(excess_threshold**2 - 1.0)
