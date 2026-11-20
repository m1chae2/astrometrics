"""Tests for the tuners of the quarantine's roundness limits.

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
    drop_below_session_median,
    fixed_roundness_limit,
    group_roundness_by_session,
)

SETUP = SetupConfig(name="Setup", camera_name="Camera", optic_name="Optic")


def _row(value: float | None, night: str, setting: int = 1) -> SimpleNamespace:
    """Build a stand-in stored roundness row.

    Returns
    -------
    row : `SimpleNamespace`
        A row with a value and a subject.
    """
    subject = SimpleNamespace(target_id="M 1", observing_night_id=night, capture_setting_id=setting)
    return SimpleNamespace(value=value, subject=subject)


def test_sessions_below_the_minimum_batch_are_left_out() -> None:
    """A session too small for the quarantine to judge is left out."""
    big = [_row(0.9, "night-1") for _ in range(8)]
    small = [_row(0.9, "night-2") for _ in range(3)]
    sessions = group_roundness_by_session([*big, *small, _row(None, "night-1")])
    assert sessions == [[0.9] * 8]


def test_fixed_limit_is_the_median_minus_sigmas() -> None:
    """The limit is the median minus sigmas times the spread."""
    session = [0.88, 0.89, 0.90, 0.91, 0.92] * 2
    limit = fixed_roundness_limit([session], sigmas=5.0)
    spread = 1.4826 * 0.01
    assert limit == pytest.approx(0.90 - 5.0 * spread)


def test_drop_stays_small_with_one_trailed_frame() -> None:
    """One trailed frame in a round session does not make the drop large."""
    round_session = [0.97, 0.98, 0.99, 0.98, 0.97, 0.98, 0.99, 0.98]
    with_trailed = [*round_session, 0.45]
    assert drop_below_session_median([with_trailed], sigmas=5.0) < 0.1


def test_a_noisier_session_gives_a_wider_drop() -> None:
    """A camera whose roundness scatters more is allowed a larger drop."""
    steady = [0.98, 0.97, 0.99, 0.98, 0.97, 0.99, 0.98, 0.98]
    scattered = [0.95, 0.80, 0.99, 0.88, 0.75, 0.97, 0.90, 0.85]
    assert drop_below_session_median([scattered], 5.0) > drop_below_session_median([steady], 5.0)


@pytest.mark.parametrize("tuner", [TRAILED_LIMITS_TUNER, COLOUR_DROP_TUNER])
def test_tuners_skip_without_the_library(tuner: Tuner) -> None:
    """With no library service given, a tuner raises the skip error."""
    with pytest.raises(TuningSkippedError):
        tuner.tune(TuningInputs(SETUP, None, None, {}))
