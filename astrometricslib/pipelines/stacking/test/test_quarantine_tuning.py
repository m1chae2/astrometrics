"""Tests for the tuners of the quarantine's star-elongation limits.

The pure functions get sessions with a known spread. The tuners get a library
service that is missing, so they must skip and never guess.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.foundation.tuning import Tuner, TuningInputs, TuningSkippedError
from astrometricslib.pipelines.stacking.pre_processing.quarantine_tuning import (
    COLOUR_DROP_TUNER,
    TRAILED_LIMITS_TUNER,
    fixed_elongation_limit,
    group_elongation_by_session,
    rise_above_session_median,
)

SETUP = SetupConfig(name="Setup", camera_name="Camera", optic_name="Optic")


def _row(value: float | None, night: str, setting: int = 1) -> SimpleNamespace:
    """Build a stand-in stored elongation row.

    Returns
    -------
    row : `SimpleNamespace`
        A row with a value and a subject.
    """
    subject = SimpleNamespace(target_id="M 1", observing_night_id=night, capture_setting_id=setting)
    return SimpleNamespace(value=value, subject=subject)


def test_sessions_below_the_minimum_batch_are_left_out() -> None:
    """A session too small for the quarantine to judge is left out."""
    big = [_row(1.1, "night-1") for _ in range(8)]
    small = [_row(1.1, "night-2") for _ in range(3)]
    sessions = group_elongation_by_session([*big, *small, _row(None, "night-1")])
    assert sessions == [[1.1] * 8]


def test_fixed_limit_is_the_median_plus_the_session_rise() -> None:
    """The limit is the median plus sigmas times the in-session spread."""
    session = [1.08, 1.09, 1.10, 1.11, 1.12] * 2
    limit = fixed_elongation_limit([session], sigmas=5.0)
    spread = 1.4826 * 0.01
    assert limit == pytest.approx(1.10 + 5.0 * spread)


def test_rise_stays_small_with_one_trailed_frame() -> None:
    """One trailed frame in a round session does not make the rise large."""
    round_session = [1.03, 1.02, 1.01, 1.02, 1.03, 1.02, 1.01, 1.02]
    with_trailed = [*round_session, 1.9]
    assert rise_above_session_median([with_trailed], sigmas=5.0) < 0.1


def test_a_noisier_session_gives_a_wider_rise() -> None:
    """A camera whose elongation scatters more is allowed a larger rise."""
    steady = [1.02, 1.03, 1.01, 1.02, 1.03, 1.01, 1.02, 1.02]
    scattered = [1.05, 1.20, 1.01, 1.12, 1.25, 1.03, 1.10, 1.15]
    assert rise_above_session_median([scattered], 5.0) > rise_above_session_median([steady], 5.0)


def test_the_spread_is_taken_inside_sessions() -> None:
    """Two steady sessions with different medians do not widen the rise."""
    low = [1.05, 1.06, 1.04, 1.05, 1.06, 1.04, 1.05, 1.05]
    high = [value + 0.3 for value in low]
    assert rise_above_session_median([low, high], 5.0) == pytest.approx(rise_above_session_median([low], 5.0))


@pytest.mark.parametrize("tuner", [TRAILED_LIMITS_TUNER, COLOUR_DROP_TUNER])
def test_tuners_skip_without_the_library(tuner: Tuner) -> None:
    """With no library service given, a tuner raises the skip error."""
    with pytest.raises(TuningSkippedError):
        tuner.tune(TuningInputs(SETUP, None, None, {}))
