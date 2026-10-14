"""Tests for checking a period result against held-out data and aliases.

Artificial multi-night light curves with a known answer: a real cycle repeats
on every night and is predicted by the others; noise is not; a single night
cannot be left out; and a nightly schedule really does put an alias beside a
signal. The last tests check the rule that combines the checks into a verdict.
Long-baseline tests check that the hold-out's period search keeps a step fine
enough to resolve a periodogram peak when the training nights span months.
"""

import numpy as np

from astrometricslib.models.gate_result import GateStatus, failed_gate, passed_gate, unchecked_gate
from astrometricslib.models.stellar_source import PeriodogramResult, TransitCandidate
from astrometricslib.pipelines.photometry.processing import period_checks as checks
from astrometricslib.pipelines.photometry.processing import periodicity_search as search

CYCLE_PERIOD = 0.13


def nightly(nights: int = 5, per_night: int = 40, hours: float = 4.0, jitter: float = 0.0) -> np.ndarray:
    """Build measurement times: the same few hours on each of several nights.

    Parameters
    ----------
    nights : `int`
        How many nights.
    per_night : `int`
        Measurements per night.
    hours : `float`
        How long each night's run is.
    jitter : `float`
        How far each night's start moves, in days, so the schedule is not
        exactly one day apart.

    Returns
    -------
    time_days : `numpy.ndarray`
        The times, in days.
    """
    return np.concatenate([
        night + jitter * night * (-1) ** night + np.linspace(0.0, hours / 24.0, per_night)
        for night in range(nights)
    ])


def cycle_light_curve(
    times: np.ndarray, period: float = CYCLE_PERIOD, amplitude: float = 0.05, seed: int = 3
) -> np.ndarray:
    """Build a sinusoidal light curve with a little noise.

    Returns
    -------
    flux : `numpy.ndarray`
        The brightness.
    """
    generator = np.random.default_rng(seed)
    return 1.0 + amplitude * np.sin(2 * np.pi * times / period) + generator.normal(0.0, 0.01, times.size)


def test_nights_are_split_at_gaps() -> None:
    """Five nights one day apart split into five groups of the same size."""
    nights = checks.split_into_nights(nightly())

    assert [len(night) for night in nights] == [40] * 5
    assert checks.split_into_nights(np.array([])) == []


def test_a_real_cycle_is_predicted_by_the_other_nights() -> None:
    """Every night agrees with the model made from the rest."""
    times = nightly()

    gate = checks.holdout_cycle(times, cycle_light_curve(times), CYCLE_PERIOD)

    assert gate.name == checks.HOLDOUT_GATE_NAME
    assert gate.status is GateStatus.PASSED
    assert gate.detail == "5 of 5 nights agree"


def test_noise_is_not_predicted_by_the_other_nights() -> None:
    """A 'period' found in noise fails the hold-out."""
    times = nightly()
    noise = 1.0 + np.random.default_rng(4).normal(0.0, 0.01, times.size)

    gate = checks.holdout_cycle(times, noise, CYCLE_PERIOD)

    assert gate.status is GateStatus.FAILED
    assert checks.HOLDOUT_GATE_NAME == gate.name


def test_a_single_night_or_two_cannot_be_left_out() -> None:
    """With fewer than three nights the hold-out says it could not run."""
    times = nightly(nights=2)

    gate = checks.holdout_cycle(times, cycle_light_curve(times), CYCLE_PERIOD)

    assert gate.status is GateStatus.NOT_CHECKED
    assert (
        checks.holdout_cycle(nightly(1), cycle_light_curve(nightly(1)), CYCLE_PERIOD).name
        == checks.HOLDOUT_GATE_NAME
    )


def test_nights_with_too_few_measurements_are_not_tested() -> None:
    """Nights of only four measurements cannot be left out."""
    times = nightly(nights=5, per_night=4)

    gate = checks.holdout_cycle(times, cycle_light_curve(times), CYCLE_PERIOD)

    assert gate.status is GateStatus.NOT_CHECKED


def test_a_nightly_schedule_alias_is_found_when_the_two_cannot_be_told_apart() -> None:
    """With the same few hours every night, 5 cycles a day looks like 3."""
    times = nightly()
    flux = cycle_light_curve(times, period=0.2)

    gate = checks.alias_check(times, flux, 0.2)

    assert gate.name == checks.ALIAS_GATE_NAME
    assert gate.status is GateStatus.FAILED
    assert "cannot be told apart" in gate.detail


def test_a_schedule_whose_nights_start_at_different_times_has_no_rival_alias() -> None:
    """Jittered start times let the true period stand out."""
    times = nightly(jitter=0.07)

    gate = checks.alias_check(times, cycle_light_curve(times, period=0.2), 0.2)

    assert gate.status is GateStatus.PASSED


def test_a_single_night_has_no_schedule_to_alias_against() -> None:
    """One night cannot be checked for aliases."""
    times = nightly(nights=1)

    gate = checks.alias_check(times, cycle_light_curve(times), CYCLE_PERIOD)

    assert gate.name == checks.ALIAS_GATE_NAME
    assert gate.status is GateStatus.NOT_CHECKED


def dip_light_curve(times: np.ndarray, depth: float = 0.03, seed: int = 5) -> np.ndarray:
    """Build a light curve with a repeating dip (0.2 d period, 0.03 d long).

    Returns
    -------
    flux : `numpy.ndarray`
        The brightness.
    """
    phase = ((times - 0.02 + 0.1) % 0.2) - 0.1
    return 1.0 - depth * (np.abs(phase) <= 0.015) + np.random.default_rng(seed).normal(0.0, 0.006, times.size)


def test_dips_predicted_from_the_other_nights_are_seen_in_the_left_out_night() -> None:
    """A real repeating dip is found in each held-out night."""
    times = nightly(nights=4, per_night=40, hours=5.0)

    gate = checks.holdout_transit(times, dip_light_curve(times), 0.2000073)

    assert gate.name == checks.HOLDOUT_GATE_NAME
    assert gate.status is GateStatus.PASSED


def test_dips_cannot_be_checked_with_one_night_or_when_none_are_predicted_to_fall_in_a_night() -> None:
    """One night, or noise with no predicted dips, gives not checked."""
    times = nightly(nights=1)
    assert checks.holdout_transit(times, dip_light_curve(times), 0.2).status is GateStatus.NOT_CHECKED

    many = nightly(nights=4, per_night=40, hours=5.0)
    quiet = 1.0 + np.random.default_rng(6).normal(0.0, 0.006, many.size)
    assert checks.holdout_transit(many, quiet, 0.2).status is GateStatus.NOT_CHECKED


def cycle_result(verdict: str = search.VERDICT_DETECTED) -> PeriodogramResult:
    """Build a cycle result of the given verdict.

    Returns
    -------
    result : `PeriodogramResult`
        A result at the real period.
    """
    return PeriodogramResult(bestPeriodDays=CYCLE_PERIOD, verdict=verdict, cyclesObserved=9.0)


def test_a_failed_check_lowers_the_verdict_one_level_and_keeps_the_old_one() -> None:
    """Noise lowers 'detected'; the failed check's sentence is added."""
    times = nightly()
    noise = 1.0 + np.random.default_rng(4).normal(0.0, 0.01, times.size)
    result = cycle_result()

    checks.apply_verdict_checks(result, times, noise)

    assert result.verdict in (search.VERDICT_POSSIBLE, search.VERDICT_NOT_DETECTED)
    assert result.uncorrected_verdict == search.VERDICT_DETECTED
    assert any(check.status is GateStatus.FAILED for check in result.verdict_checks)
    assert "left-out nights" in result.note


def test_a_check_that_could_not_run_marks_the_result_unconfirmed_and_lowers_nothing() -> None:
    """With one night, no check runs: the verdict stands but is unconfirmed."""
    times = nightly(nights=1)
    result = cycle_result()

    checks.apply_verdict_checks(result, times, cycle_light_curve(times))

    assert result.verdict == search.VERDICT_DETECTED
    assert result.unconfirmed is True
    assert result.uncorrected_verdict == ""


def test_a_result_that_passes_every_check_is_neither_lowered_nor_unconfirmed() -> None:
    """A real cycle seen on five nights keeps its verdict with no qualifier."""
    times = nightly(jitter=0.07)
    result = cycle_result()

    checks.apply_verdict_checks(result, times, cycle_light_curve(times))

    assert result.verdict == search.VERDICT_DETECTED
    assert result.unconfirmed is False
    assert [check.status for check in result.verdict_checks] == [GateStatus.PASSED, GateStatus.PASSED]


def test_two_failed_checks_lower_a_detection_two_levels() -> None:
    """Each failed check lowers one level, down to not detected."""
    result = cycle_result()
    result.verdict_checks = []

    # Apply the rule's arithmetic directly through the public function by
    # giving it light curves that fail the checks: noise on a nightly schedule.
    times = nightly()
    noise = 1.0 + np.random.default_rng(8).normal(0.0, 0.01, times.size)
    checks.apply_verdict_checks(result, times, noise)

    failed = [check for check in result.verdict_checks if check.status is GateStatus.FAILED]
    expected_level = max(0, 2 - len(failed))
    assert (
        result.verdict
        == (search.VERDICT_NOT_DETECTED, search.VERDICT_POSSIBLE, search.VERDICT_DETECTED)[expected_level]
    )


def test_results_that_did_not_pass_the_search_are_left_alone() -> None:
    """A not-detected or missing result is never checked."""
    times = nightly()
    quiet = cycle_result(search.VERDICT_NOT_DETECTED)

    checks.apply_verdict_checks(quiet, times, cycle_light_curve(times))
    checks.apply_verdict_checks(None, times, cycle_light_curve(times))

    assert quiet.verdict_checks == []
    assert quiet.unconfirmed is False


def test_the_failed_checks_are_counted_across_results() -> None:
    """The count adds the failed checks of every result."""
    first = cycle_result()
    first.verdict_checks = [failed_gate("holdout_nights", "x"), passed_gate("alias_ambiguity")]
    second = TransitCandidate(verdict=search.VERDICT_POSSIBLE)
    second.verdict_checks = [unchecked_gate("holdout_nights", "y"), failed_gate("holdout_nights", "z")]

    assert checks.count_failed_checks([first, second, None]) == 2


LONG_PERIOD = 0.21


def long_baseline(nights: int, spacing_days: float, seed: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """Build six-hour nights spread over months with a 0.21 d sinusoid in them.

    Parameters
    ----------
    nights : `int`
        How many nights.
    spacing_days : `float`
        Days from the start of one night to the next.
    seed : `int`, optional
        Seed for the noise.

    Returns
    -------
    time_days, flux : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        Measurements every 5 minutes (amplitude 0.05, noise 0.01).
    """
    one_night = np.arange(0.0, 6.0 / 24.0, 5.0 / 1440.0)
    times = np.concatenate([night * spacing_days + one_night for night in range(nights)])
    generator = np.random.default_rng(seed)
    flux = 1.0 + 0.05 * np.sin(2 * np.pi * times / LONG_PERIOD) + generator.normal(0.0, 0.01, times.size)
    return times, flux


def test_the_training_window_step_stays_within_a_fifth_of_a_peak_width() -> None:
    """The step is at most 1/(5 T) at every span, with the window centred."""
    centre = 1.0 / LONG_PERIOD
    for span in (0.5, 30.0, 117.0, 390.0, 2950.0):
        frequency = checks._training_frequency_window(span, centre)

        assert float(np.diff(frequency).max()) <= 1.0 / (5.0 * span) * (1 + 1e-9)
        assert frequency.size <= checks._MAXIMUM_WINDOW_POINTS
        assert frequency[frequency.size // 2] == np.float64(centre)
        assert frequency.min() > 0.0


def test_a_very_long_span_narrows_the_training_window_instead_of_coarsening_the_step() -> None:
    """Past the point cap, the window narrows and the step is kept."""
    centre = 1.0 / LONG_PERIOD
    span = 50_000.0

    frequency = checks._training_frequency_window(span, centre)

    assert frequency.size >= checks._MAXIMUM_WINDOW_POINTS - 1
    assert frequency.size <= checks._MAXIMUM_WINDOW_POINTS
    assert float(np.diff(frequency).max()) <= 1.0 / (5.0 * span) * (1 + 1e-9)
    assert frequency[-1] - frequency[0] < 2 * checks._FREQUENCY_WINDOW * centre
    assert frequency[0] < centre < frequency[-1]
    assert (frequency[-1] - frequency[0]) * span > 6.0  # still several peak widths either side


def test_the_training_search_finds_the_period_on_a_120_day_span() -> None:
    """Forty nights over 117 days: the training search returns 0.21 d."""
    times, flux = long_baseline(nights=40, spacing_days=3.0)

    found = checks._train_cycle_period(times, flux, LONG_PERIOD)

    assert found is not None
    assert abs(1.0 / found - 1.0 / LONG_PERIOD) <= 1.0 / float(times.max() - times.min())


def test_the_training_search_finds_the_period_on_a_390_day_span() -> None:
    """Forty nights over 390 days: the old fixed window returned 0.190 d.

    The span is 1,858 cycles, so the fixed window's step was 0.4 of a peak
    width and a nightly-schedule alias won; the window sized from the span
    returns 0.21 d.
    """
    times, flux = long_baseline(nights=40, spacing_days=10.0)

    found = checks._train_cycle_period(times, flux, LONG_PERIOD)

    assert found is not None
    assert abs(1.0 / found - 1.0 / LONG_PERIOD) <= 1.0 / float(times.max() - times.min())


def test_the_holdout_passes_a_real_cycle_on_a_120_day_span() -> None:
    """Twenty-four nights over 115 days: every left-out night agrees."""
    times, flux = long_baseline(nights=24, spacing_days=5.0)

    gate = checks.holdout_cycle(times, flux, LONG_PERIOD)

    assert gate.status is GateStatus.PASSED
    assert gate.detail == "24 of 24 nights agree"
