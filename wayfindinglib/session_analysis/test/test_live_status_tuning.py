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
