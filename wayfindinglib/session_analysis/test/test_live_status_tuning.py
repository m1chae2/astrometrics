"""Purpose: Tests for the live-status threshold tuners.

Description: Checks each statistic on small synthetic series with a known
answer: the quiet level of a night, the grouping of over-limit samples into
excursions, the first-sample share, and the error of the star-count
baseline. Also checks that each tuner skips when nothing is stored.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.parameters import TuningInputs, TuningSkippedError
from wayfindinglib.session_analysis import live_status_tuning as tuning

_SETUP = SetupConfig(name="test", camera_name="Camera", optic_name="Optic")
"""A setup the tuners do not look at."""


def test_night_quiet_levels_give_the_median_and_the_percentile() -> None:
    """The errors 1 to 100 have the median 50.5 and a maximum of 100."""
    levels = tuning.night_quiet_levels([list(range(1, 101))], 100.0, 10)
    assert levels == [(pytest.approx(50.5), pytest.approx(100.0))]


def test_night_quiet_levels_skip_short_nights_and_zero_medians() -> None:
    """A night with too few samples, or with a median of zero, is left out."""
    assert tuning.night_quiet_levels([[1.0, 2.0]], 99.0, 10) == []
    assert tuning.night_quiet_levels([[0.0] * 20], 99.0, 10) == []


def test_excursion_runs_join_samples_that_are_close_in_time() -> None:
    """Samples 5 s apart are one excursion; one 100 s later is another."""
    times = [0.0, 5.0, 6.0, 106.0]
    errors = [1.0, 9.0, 8.0, 10.0]
    runs = tuning.excursion_runs(times, errors, 5.0, 20.0)
    assert [len(run) for run in runs] == [2, 1]


def test_first_sample_ratios_tell_a_jump_from_a_drift() -> None:
    """A jump starts at its peak (ratio 1); a drift starts at a tenth of it."""
    jump = [(0.0, 10.0), (1.0, 10.0)]
    drift = [(0.0, 1.0), (1.0, 5.0), (2.0, 10.0)]
    assert tuning.first_sample_ratios([jump, drift]) == pytest.approx([1.0, 0.1])


def test_join_threshold_sits_between_short_and_long_gaps() -> None:
    """Gaps of 2 to 4 s inside events and 600 s between them split."""
    threshold = tuning.join_threshold([2.0, 3.0, 4.0, 600.0, 650.0], 3.0)
    assert threshold is not None
    assert 4.0 < threshold < 600.0


def test_star_baseline_errors_prefer_a_longer_window_for_noisy_counts() -> None:
    """Noisy counts are predicted better by a median of more frames."""
    counts = [100, 130, 80, 120, 90, 110, 95, 105, 125, 85, 100, 115, 90, 108, 97, 103, 99, 101, 100, 100]
    errors = tuning.star_baseline_errors([counts], 8)
    assert set(errors) == set(range(2, 9))
    assert errors[8][0] < errors[2][0]


def test_star_baseline_errors_skip_a_window_longer_than_the_run() -> None:
    """A run of three frames can only test a window of two."""
    assert set(tuning.star_baseline_errors([[10, 10, 10]], 5)) == {2}


def _wayfinder(nights: tuple[frozenset[str], frozenset[str]]) -> SimpleNamespace:
    """Build a fake wayfinder that records no samples and no sessions.

    Parameters
    ----------
    nights : `tuple` [`frozenset` [`str`], `frozenset` [`str`]]
        What the record store reports as the recorded nights.

    Returns
    -------
    wayfinder : `types.SimpleNamespace`
        The fake.
    """
    records = SimpleNamespace(
        recorded_session_nights=lambda: nights, get_guiding_samples=lambda **_keywords: []
    )
    butler = SimpleNamespace(get_all=lambda _dataset: [])
    return SimpleNamespace(control=SimpleNamespace(_context=SimpleNamespace(records=records, butler=butler)))


def test_the_tuners_skip_when_nothing_is_stored() -> None:
    """Each tuner raises `TuningSkippedError` when nothing is recorded."""
    inputs = TuningInputs(_SETUP, None, None, {"wayfinder": _wayfinder((frozenset(), frozenset()))})
    for tune in (tuning.tune_guide_limit, tuning.tune_excursions, tuning.tune_star_baseline):
        with pytest.raises(TuningSkippedError):
            tune(inputs)


def test_the_tuners_skip_when_the_recorded_nights_cannot_be_read() -> None:
    """A store that cannot list its nights is a reason to skip, not a guess."""
    inputs = TuningInputs(_SETUP, None, None, {"wayfinder": _wayfinder(None)})  # type: ignore[arg-type]
    with pytest.raises(TuningSkippedError, match="cannot be read"):
        tuning.tune_guide_limit(inputs)


def test_dither_recovery_is_the_time_to_two_normal_samples() -> None:
    """The error is normal at the first sample whose next one is normal."""
    sample_times = [0.0, 4.0, 8.0, 12.0, 16.0, 20.0]
    sample_errors = [5.0, 1.0, 4.0, 1.0, 1.0, 1.0]
    # The sample at 4 s is low but the next is not, so recovery is at 12 s.
    assert tuning.dither_recovery_seconds([0.0], sample_times, sample_errors, 1.5) == [12.0]


def test_dither_recovery_leaves_out_a_dither_that_never_recovers() -> None:
    """A dither whose window holds no normal sample is left out."""
    assert tuning.dither_recovery_seconds([0.0], [0.0, 4.0], [5.0, 6.0], 1.5) == []


def test_dither_recovery_stops_at_the_next_dither() -> None:
    """A normal sample after the next dither belongs to that dither."""
    times = [0.0, 4.0, 100.0, 104.0]
    errors = [5.0, 5.0, 1.0, 1.0]
    assert tuning.dither_recovery_seconds([0.0, 100.0], times, errors, 1.5) == [0.0]


def test_sample_gaps_stay_inside_a_night() -> None:
    """The gap between one night and the next is not counted."""
    assert tuning.sample_gaps([[0.0, 2.0, 5.0], [1000.0, 1003.0]]) == [2.0, 3.0, 3.0]


def _recording_wayfinder(contexts: list, rows_by_night: dict[str, list[dict[str, float]]]) -> SimpleNamespace:
    """Build a fake wayfinder with Ekos contexts and guide samples.

    Parameters
    ----------
    contexts : `list`
        Fake Ekos session contexts.
    rows_by_night : `dict` [`str`, `list` [`dict`]]
        The guiding sample rows of each night.

    Returns
    -------
    wayfinder : `types.SimpleNamespace`
        The fake.
    """
    nights = frozenset(rows_by_night)
    records = SimpleNamespace(
        recorded_session_nights=lambda: (nights, frozenset()),
        get_guiding_samples=lambda session_id, **_keywords: rows_by_night.get(session_id, []),
    )
    butler = SimpleNamespace(get_all=lambda _dataset: contexts)
    return SimpleNamespace(control=SimpleNamespace(_context=SimpleNamespace(records=records, butler=butler)))


def _rows(times: list[float], errors: list[float]) -> list[dict[str, float]]:
    """Build guiding rows with the given total errors, all along RA.

    Parameters
    ----------
    times, errors : `list` [`float`]
        The time and total error of each sample.

    Returns
    -------
    rows : `list` [`dict`]
        Rows with the keys the tuners read.
    """
    return [{"timestamp": time, "dra": error, "ddec": 0.0} for time, error in zip(times, errors, strict=True)]


def test_the_early_exposure_tuner_takes_the_median_recovery_time() -> None:
    """Three dithers that recover after 8, 12 and 12 s give 12 s."""
    # A night of quiet guiding (error 1.0) with three 4 s-spaced dither bursts.
    times = [float(4 * step) for step in range(300)]
    errors = [1.0] * 300
    dither_times = [100.0, 400.0, 800.0]
    for dither_time, quiet_after in zip(dither_times, (8.0, 12.0, 12.0), strict=True):
        for step, time in enumerate(times):
            if dither_time <= time < dither_time + quiet_after:
                errors[step] = 9.0
    events = [SimpleNamespace(timestamp=time, state="Dithering successful") for time in dither_times]
    context = SimpleNamespace(session_id="2026-10-02", guide_state_events=events)
    wayfinder = _recording_wayfinder([context], {"2026-10-02": _rows(times, errors)})

    result = tuning.tune_early_exposure(TuningInputs(_SETUP, None, None, {"wayfinder": wayfinder}))

    assert result.values == {"wayfinding.early_exposure_seconds": pytest.approx(12.0)}
    assert result.sample == 3


def test_the_early_exposure_tuner_skips_without_dithers() -> None:
    """A night with no successful dither gives nothing to measure."""
    context = SimpleNamespace(session_id="2026-10-02", guide_state_events=[])
    wayfinder = _recording_wayfinder([context], {"2026-10-02": _rows([0.0, 4.0], [1.0, 1.0])})
    with pytest.raises(TuningSkippedError):
        tuning.tune_early_exposure(TuningInputs(_SETUP, None, None, {"wayfinder": wayfinder}))


def test_the_stale_record_tuner_is_three_times_the_longest_ordinary_gap() -> None:
    """Gaps of 4 s with a few 100 s pauses give 3 times the 99.9th."""
    times = [0.0]
    for index in range(2000):
        times.append(times[-1] + (100.0 if index % 500 == 499 else 4.0))
    wayfinder = _recording_wayfinder([], {"2026-10-02": _rows(times, [1.0] * len(times))})

    result = tuning.tune_stale_record(TuningInputs(_SETUP, None, None, {"wayfinder": wayfinder}))

    assert result.values == {"wayfinding.stale_record_seconds": pytest.approx(300.0)}
    assert result.sample == 2000


def test_the_stale_record_tuner_skips_with_too_few_gaps() -> None:
    """Fewer than a thousand gaps cannot give a 99.9th percentile."""
    wayfinder = _recording_wayfinder([], {"2026-10-02": _rows([0.0, 4.0, 8.0], [1.0] * 3)})
    with pytest.raises(TuningSkippedError, match="gaps"):
        tuning.tune_stale_record(TuningInputs(_SETUP, None, None, {"wayfinder": wayfinder}))
