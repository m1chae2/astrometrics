"""Asks whether a run's variability statistic can see variables at all.

The photometry pipeline flags a star when its scatter (the coefficient of
variation, CV) is above a cutoff built from its field. Nothing tested whether
that scatter rises for stars that really vary. On the library (measured
2026-10-09, `scripts/measure_variability_cutoff.py`) it did not: known
variables had no more scatter than other stars of the same brightness
(AUC 0.49), because the photometry's own scatter (median 0.29 mag across
targets) is larger than most variables' amplitudes.

Two checks make that visible for every run:

- `discrimination`: among the stars with a light curve in the run, does the
  CV of the stars the catalogs list as variable tend to exceed the CV of the
  stars no catalog lists? The measure is the AUC, the chance that a random
  catalogued variable has more scatter than a random unlisted star. A value of
  0.5 is no skill. It must exceed 0.5 by enough that chance would give that
  much in fewer than 5% of fields.
- `minimum_detectable_amplitude_mag`: how big a sinusoidal variable would have
  to be before its scatter clears the run's cutoff.

Both describe the run's own field. A catalogued variable that is brighter than
its field is quieter, so the AUC is, if anything, pessimistic.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.stats import rankdata

# The fewest known variables and unlisted stars a field needs for an AUC to
# mean anything. With 10 and 30 the standard error of an AUC at chance is
# about 0.10, so the test below is not overstrict on a small field.
MINIMUM_KNOWN_VARIABLES = 10
MINIMUM_UNLISTED_STARS = 30

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
    def sees_known_variables(self) -> bool:
        """Say whether the AUC beats chance at the 5% level.

        Returns
        -------
        passes : `bool`
            True when `auc` is at least `required_auc`.
        """
        return self.auc >= self.required_auc


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


def discrimination(known_cvs: Sequence[float], unlisted_cvs: Sequence[float]) -> DiscriminationResult | None:
    """Test whether scatter separates known variables from unlisted stars.

    Parameters
    ----------
    known_cvs : `Sequence` [`float`]
        The CV of each star the catalogs list as variable.
    unlisted_cvs : `Sequence` [`float`]
        The CV of each star no catalog lists.

    Returns
    -------
    result : `DiscriminationResult` or `None`
        `None` when either group is too small to say anything.
    """
    if len(known_cvs) < MINIMUM_KNOWN_VARIABLES or len(unlisted_cvs) < MINIMUM_UNLISTED_STARS:
        return None
    auc = area_under_curve(known_cvs, unlisted_cvs)
    if auc is None:
        return None
    return DiscriminationResult(
        auc=auc,
        required_auc=required_auc(len(known_cvs), len(unlisted_cvs)),
        known_variables=len(known_cvs),
        unlisted_stars=len(unlisted_cvs),
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
