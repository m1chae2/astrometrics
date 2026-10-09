"""Checks a period result against held-out data and against aliases.

A period search always finds a best period. The false-alarm probability says
how often noise would look this strong; it does not say that the pattern is
real, repeats, or is at the right period. Three checks ask more:

- **Hold-out.** Leave one night out, find the period again from the other
  nights, and see whether that period predicts the night that was left out.
  A pattern that is only a feature of the nights it was found from fails.
  Each night is tried in turn; most must agree.
- **Alias.** Observing once a night puts echoes of every signal at the
  frequency of the nightly schedule above and below it. If an echo is nearly as
  strong as the best peak, the period cannot be told from its alias.
- **Dips.** For a repeating dip, the hold-out predicts when the dips fall in
  the left-out night from the other nights' period and epoch, and looks for
  them there.

Each check returns a `GateResult`: ``passed``, ``failed`` or ``not_checked``
(too few nights, or no held-out night could test the prediction).
`apply_verdict_checks` then combines them with the rule decided for the
audit: a failed check lowers the verdict one level; a check that could not run
does not lower it and marks the result unconfirmed.
"""

import math
from collections.abc import Sequence

import numpy as np
from astropy.timeseries import LombScargle

from astrometricslib.models.gate_result import (
    GateResult,
    GateStatus,
    failed_gate,
    passed_gate,
    unchecked_gate,
)
from astrometricslib.models.stellar_source import PeriodogramResult, TransitCandidate
from astrometricslib.pipelines.photometry.processing.periodicity_search import (
    VERDICT_DETECTED,
    VERDICT_NOT_DETECTED,
    VERDICT_POSSIBLE,
    box_search,
)

HOLDOUT_GATE_NAME = "holdout_nights"
ALIAS_GATE_NAME = "alias_ambiguity"

# A gap in the measurements longer than this separates one night from the next.
NIGHT_GAP_DAYS = 0.25

# The fewest nights a hold-out needs: one to leave out and two to find a period
# from, so that the training nights show a repeat at all.
MINIMUM_NIGHTS = 3

# A night must have at least this many measurements to be left out, and the
# others together at least this many to find a period from.
MINIMUM_HELD_OUT_POINTS = 8
MINIMUM_TRAINING_POINTS = 15

# How much of the period the training search may range around the full-data
# period, as a fraction of the frequency. The search finds the best period
# near the full-data one; the check is whether it is the same period.
_FREQUENCY_WINDOW = 0.25

# A held-out night agrees when the model from the other nights predicts it
# better than a model of the same shape at a random phase does in all but this
# share of random phases (a one-sided 5% test).
_PERMUTATION_COUNT = 199
_AGREEMENT_P_VALUE = 0.05

# The share of testable nights that must agree.
_AGREEING_SHARE = 0.5

# A dip is called seen in a held-out night when its depth is at least this many
# standard errors, and it needs this many points inside the predicted dips.
_DIP_Z_SCORE = 2.0
_MINIMUM_POINTS_IN_DIP = 3

# An alias counts as a rival when its power is at least this share of the best
# peak's, and the window (the schedule's own response) has a peak at least this
# share of its strongest at the frequency offset.
_RIVAL_ALIAS_POWER_SHARE = 0.9
_SIGNIFICANT_WINDOW_PEAK_SHARE = 0.5

# How many of the schedule's strongest frequencies are tried as alias offsets.
_WINDOW_PEAKS_TRIED = 3

_VERDICT_ORDER = (VERDICT_NOT_DETECTED, VERDICT_POSSIBLE, VERDICT_DETECTED)


def split_into_nights(time_days: np.ndarray, gap_days: float = NIGHT_GAP_DAYS) -> list[np.ndarray]:
    """Group measurement indices into nights.

    Parameters
    ----------
    time_days : `numpy.ndarray`
        The measurement times, in days.
    gap_days : `float`, optional
        The gap that separates two nights.

    Returns
    -------
    nights : `list` [`numpy.ndarray`]
        The indices of each night's measurements, in time order.
    """
    order = np.argsort(time_days)
    if order.size == 0:
        return []
    breaks = np.where(np.diff(time_days[order]) > gap_days)[0] + 1
    return [group for group in np.split(order, breaks) if group.size]


def _harmonic_model(time_days: np.ndarray, flux: np.ndarray, period_days: float) -> tuple[np.ndarray, float]:
    """Fit a mean and one sinusoid of a fixed period.

    Returns
    -------
    coefficients, mean : `tuple` [`numpy.ndarray`, `float`]
        The sine and cosine amplitudes, and the mean level.
    """
    phase = 2.0 * math.pi * time_days / period_days
    design = np.column_stack([np.ones_like(time_days), np.sin(phase), np.cos(phase)])
    solution, *_ = np.linalg.lstsq(design, flux, rcond=None)
    return solution[1:], float(solution[0])


def _skill(
    held_t: np.ndarray, held_f: np.ndarray, period: float, coefficients: np.ndarray, mean: float, shift: float
) -> float:
    """Score a sinusoid's prediction of a held-out night.

    Returns
    -------
    skill : `float`
        One minus the model's squared error over the squared error of the
        training mean; above zero is better than predicting the mean.
    """
    phase = 2.0 * math.pi * held_t / period + shift
    predicted = mean + coefficients[0] * np.sin(phase) + coefficients[1] * np.cos(phase)
    error_mean = float(np.sum((held_f - mean) ** 2))
    return 1.0 - float(np.sum((held_f - predicted) ** 2)) / error_mean if error_mean > 0 else 0.0


def _train_cycle_period(train_t: np.ndarray, train_f: np.ndarray, period_days: float) -> float | None:
    """Find the best period near the full-data one from the training nights.

    Returns
    -------
    period : `float` or `None`
        The strongest period within the frequency window, or `None` if the
        training span is too short to resolve it.
    """
    span = float(train_t.max() - train_t.min())
    if span <= 0:
        return None
    centre = 1.0 / period_days
    frequency = np.linspace(centre * (1 - _FREQUENCY_WINDOW), centre * (1 + _FREQUENCY_WINDOW), 2000)
    power = LombScargle(train_t, train_f).power(frequency)
    return float(1.0 / frequency[int(np.argmax(power))])


def holdout_cycle(time_days: np.ndarray, flux: np.ndarray, period_days: float, seed: int = 0) -> GateResult:
    """Leave each night out and test whether the others predict it.

    Parameters
    ----------
    time_days : `numpy.ndarray`
        The measurement times, in days.
    flux : `numpy.ndarray`
        The brightness.
    period_days : `float`
        The period found from all the data.
    seed : `int`, optional
        Seed for the random phases of the comparison.

    Returns
    -------
    gate : `GateResult`
        ``not_checked`` with fewer than `MINIMUM_NIGHTS` nights or when no
        night can be tested; ``passed`` when more than half the testable nights
        agree; otherwise ``failed``.
    """
    time_days = np.asarray(time_days, dtype=float)
    flux = np.asarray(flux, dtype=float)
    source = f"each night left out in turn; more than {_AGREEING_SHARE:.0%} must agree (5% level)"
    nights = split_into_nights(time_days)
    if len(nights) < MINIMUM_NIGHTS:
        return unchecked_gate(
            HOLDOUT_GATE_NAME,
            f"only {len(nights)} night(s); {MINIMUM_NIGHTS} are needed to leave one out",
            source,
        )
    generator = np.random.default_rng(seed)
    tested = agreed = 0
    for held in nights:
        training_index = np.concatenate([night for night in nights if night is not held])
        if held.size < MINIMUM_HELD_OUT_POINTS or training_index.size < MINIMUM_TRAINING_POINTS:
            continue
        train_t, train_f = time_days[training_index], flux[training_index]
        found = _train_cycle_period(train_t, train_f, period_days)
        if found is None:
            continue
        tested += 1
        # The other nights must find the same period, to within their own
        # frequency resolution (one over their span).
        if abs(1.0 / found - 1.0 / period_days) > 1.0 / float(train_t.max() - train_t.min()):
            continue
        coefficients, mean = _harmonic_model(train_t, train_f, found)
        observed = _skill(time_days[held], flux[held], found, coefficients, mean, 0.0)
        if observed <= 0.0:
            continue
        random_skills = [
            _skill(
                time_days[held], flux[held], found, coefficients, mean, generator.uniform(0.0, 2.0 * math.pi)
            )
            for _ in range(_PERMUTATION_COUNT)
        ]
        p_value = (1 + sum(skill >= observed for skill in random_skills)) / (1 + _PERMUTATION_COUNT)
        agreed += p_value <= _AGREEMENT_P_VALUE
    if tested < 2:
        return unchecked_gate(
            HOLDOUT_GATE_NAME,
            "fewer than two nights had enough measurements to be left out and tested",
            source,
        )
    share = agreed / tested
    if share > _AGREEING_SHARE:
        return passed_gate(
            HOLDOUT_GATE_NAME, share, _AGREEING_SHARE, source, f"{agreed} of {tested} nights agree"
        )
    return failed_gate(
        HOLDOUT_GATE_NAME,
        f"the other nights predict only {agreed} of {tested} left-out nights, so the pattern "
        "may be a feature of the nights it was found from",
        share,
        _AGREEING_SHARE,
        source,
    )


def holdout_transit(
    time_days: np.ndarray, flux: np.ndarray, period_days: float, shuffle_count: int = 1
) -> GateResult:
    """Leave each night out and look for the dips the others predict in it.

    Parameters
    ----------
    time_days : `numpy.ndarray`
        The measurement times, in days.
    flux : `numpy.ndarray`
        The brightness.
    period_days : `float`
        The period found from all the data.
    shuffle_count : `int`, optional
        Noise-only versions for the training searches. They only need the
        best period, epoch and duration, not a false-alarm probability, so
        one is enough and the search is much faster.

    Returns
    -------
    gate : `GateResult`
        ``not_checked`` with fewer than `MINIMUM_NIGHTS` nights or when no
        left-out night has the predicted dips in it; ``passed`` when more than
        half the nights that do show them; otherwise ``failed``.
    """
    time_days = np.asarray(time_days, dtype=float)
    flux = np.asarray(flux, dtype=float)
    source = f"dips predicted from the other nights seen at {_DIP_Z_SCORE:g} standard errors in most nights"
    nights = split_into_nights(time_days)
    if len(nights) < MINIMUM_NIGHTS:
        return unchecked_gate(
            HOLDOUT_GATE_NAME,
            f"only {len(nights)} night(s); {MINIMUM_NIGHTS} are needed to leave one out",
            source,
        )
    tested = seen = 0
    for held in nights:
        training_index = np.concatenate([night for night in nights if night is not held])
        if held.size < MINIMUM_HELD_OUT_POINTS or training_index.size < MINIMUM_TRAINING_POINTS:
            continue
        trained = box_search(time_days[training_index], flux[training_index], shuffle_count=shuffle_count)
        if not trained.period_days or abs(trained.period_days - period_days) > period_days**2 / float(
            time_days[training_index].max() - time_days[training_index].min()
        ):
            continue
        held_t, held_f = time_days[held], flux[held] / np.median(flux)
        half_duration = trained.transit_duration_hours / 48.0
        offset = (
            held_t - trained.epoch_t0 + 0.5 * trained.period_days
        ) % trained.period_days - 0.5 * trained.period_days
        in_dip = np.abs(offset) <= half_duration
        if in_dip.sum() < _MINIMUM_POINTS_IN_DIP or (~in_dip).sum() < MINIMUM_HELD_OUT_POINTS:
            continue
        tested += 1
        depth = float(np.mean(held_f[~in_dip]) - np.mean(held_f[in_dip]))
        scatter = float(np.std(held_f[~in_dip])) or 1e-12
        standard_error = scatter * math.sqrt(1.0 / in_dip.sum() + 1.0 / (~in_dip).sum())
        seen += depth / standard_error >= _DIP_Z_SCORE
    if tested == 0:
        return unchecked_gate(
            HOLDOUT_GATE_NAME,
            "no left-out night had the predicted dips falling in it, so none could test them",
            source,
        )
    share = seen / tested
    if share > _AGREEING_SHARE:
        return passed_gate(
            HOLDOUT_GATE_NAME, share, _AGREEING_SHARE, source, f"dips seen in {seen} of {tested} nights"
        )
    return failed_gate(
        HOLDOUT_GATE_NAME,
        f"the dips predicted from the other nights are seen in only {seen} of {tested} left-out nights",
        share,
        _AGREEING_SHARE,
        source,
    )


def alias_check(time_days: np.ndarray, flux: np.ndarray, period_days: float) -> GateResult:
    """Say whether the period could be an alias of the observing schedule.

    Parameters
    ----------
    time_days : `numpy.ndarray`
        The measurement times, in days.
    flux : `numpy.ndarray`
        The brightness.
    period_days : `float`
        The period found from all the data.

    Returns
    -------
    gate : `GateResult`
        ``failed`` when an alias of the schedule has nearly as much power as
        the best peak; ``passed`` when none does; ``not_checked`` when the
        data have no repeating schedule (a single night).
    """
    time_days = np.asarray(time_days, dtype=float)
    flux = np.asarray(flux, dtype=float)
    source = f"no alias of the schedule at {_RIVAL_ALIAS_POWER_SHARE:.0%} or more of the best peak's power"
    if len(split_into_nights(time_days)) < 2:
        return unchecked_gate(ALIAS_GATE_NAME, "a single night has no schedule to alias against", source)
    span = float(time_days.max() - time_days.min())
    best = 1.0 / period_days
    frequency = np.linspace(max(best * 0.2, 1.0 / span), best * 1.8, 6000)
    power = LombScargle(time_days, flux).power(frequency)
    window = LombScargle(time_days, np.ones_like(time_days), fit_mean=False, center_data=False).power(
        frequency
    )
    best_power = float(np.interp(best, frequency, power))
    resolution = 1.0 / span
    strongest = float(window.max()) or 1.0
    candidates = [
        index
        for index in range(1, len(window) - 1)
        if window[index] >= window[index - 1]
        and window[index] >= window[index + 1]
        and window[index] >= _SIGNIFICANT_WINDOW_PEAK_SHARE * strongest
        and frequency[index] > 2.0 * resolution
    ]
    offsets = sorted(
        (frequency[index] for index in candidates), key=lambda f: -float(np.interp(f, frequency, window))
    )
    for offset in offsets[:_WINDOW_PEAKS_TRIED]:
        for sign in (-1.0, 1.0):
            alias = best + sign * offset
            if alias <= 0 or alias < frequency[0] or alias > frequency[-1]:
                continue
            near = (frequency >= alias - resolution) & (frequency <= alias + resolution)
            ratio = float(power[near].max()) / best_power if best_power > 0 else 0.0
            if ratio >= _RIVAL_ALIAS_POWER_SHARE:
                return failed_gate(
                    ALIAS_GATE_NAME,
                    f"a period of {1.0 / alias:.4g} d fits {ratio:.0%} as well as {period_days:.4g} d: "
                    "the two differ by the observing schedule's own frequency, so they cannot be told apart",
                    ratio,
                    _RIVAL_ALIAS_POWER_SHARE,
                    source,
                )
    return passed_gate(ALIAS_GATE_NAME, limit=_RIVAL_ALIAS_POWER_SHARE, limit_source=source)


def apply_verdict_checks(
    result: PeriodogramResult | TransitCandidate | None, time_days: np.ndarray, flux: np.ndarray
) -> None:
    """Run the checks on a result and combine them into its verdict, in place.

    The rule: each failed check lowers the verdict one level (detected to
    possible, possible to not detected). A check that could not run lowers
    nothing and marks the result unconfirmed. Results that are not
    "detected" or "possible" are left as they are.

    Parameters
    ----------
    result : `PeriodogramResult`, `TransitCandidate` or `None`
        The search result to check.
    time_days : `numpy.ndarray`
        The light curve's times, in days.
    flux : `numpy.ndarray`
        The light curve's brightness.
    """
    if result is None or result.verdict not in (VERDICT_DETECTED, VERDICT_POSSIBLE):
        return
    if isinstance(result, TransitCandidate):
        period = result.period_days
        checks = [holdout_transit(time_days, flux, period)]
    else:
        period = result.best_period_days
        checks = [holdout_cycle(time_days, flux, period), alias_check(time_days, flux, period)]
    result.verdict_checks = checks
    result.unconfirmed = any(check.status is GateStatus.NOT_CHECKED for check in checks)
    failed = [check for check in checks if check.status is GateStatus.FAILED]
    if not failed:
        return
    if not result.uncorrected_verdict:
        result.uncorrected_verdict = result.verdict
    level = max(0, _VERDICT_ORDER.index(result.verdict) - len(failed))
    result.verdict = _VERDICT_ORDER[level]
    reasons = " ".join(check.detail for check in failed)
    result.note = f"{result.note} {reasons}".strip()


def count_failed_checks(results: Sequence[PeriodogramResult | TransitCandidate | None]) -> int:
    """Count the failed checks across several results.

    Returns
    -------
    count : `int`
        The number of failed verdict checks.
    """
    return sum(
        1
        for result in results
        if result is not None
        for check in result.verdict_checks
        if check.status is GateStatus.FAILED
    )
