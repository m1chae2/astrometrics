"""Purpose: Tests for the plate-solve grouping tuner.

Description: Builds three nights of synthetic plate solves of one target, ten
seconds apart, with a known settling slew and a known spread, and checks each
pure statistic and the whole tuner against the known answers. Also checks that
the tuner skips when no target has two solves on one night.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.parameters import TuningInputs, TuningSkippedError
from wayfindinglib.analytics import alignment_tuning as tuning
from wayfindinglib.analytics.alignment_sessions import (
    ASSUMED_CADENCE_SECONDS,
    RUN_GAP_SECONDS,
    SAME_TARGET_RADIUS_DEG,
    SLEW_ERROR_ARCSEC,
)

_SETUP = SetupConfig(name="test", camera_name="Camera", optic_name="Optic")
"""A setup the tuner does not look at."""

_NIGHT_SECONDS = 86_400.0
"""The time from one synthetic night to the next."""


def _row(night: int, index: int, error: float, dec_offset: float = 0.0) -> dict:
    """Build one stored solve of the target "A".

    Parameters
    ----------
    night : `int`
        Which night, from 0.
    index : `int`
        The solve's place in the night; solves are ten seconds apart.
    error : `float`
        The pointing error in arcseconds.
    dec_offset : `float`, optional
        How far the solve lies from the first one in declination, in degrees.

    Returns
    -------
    row : `dict`
        A stored alignment row.
    """
    return {
        "target_name": "A",
        "session_id": f"night-{night}",
        "timestamp": 1_790_000_000.0 + night * _NIGHT_SECONDS + index * 10.0,
        "pointing_error_arcsec": error,
        "mount_ra": 10.0,
        "mount_dec": 20.0 + dec_offset,
    }


def _three_nights() -> list[dict]:
    """Build three nights of 20 solves each.

    Returns
    -------
    rows : `list` [`dict`]
        The rows. The first solve of a night is a 500 arcsecond slew; every
        later solve is 5 arcseconds off and 0.01 degree from the first.
    """
    return [
        _row(night, index, 500.0 if index == 0 else 5.0, 0.0 if index == 0 else 0.01)
        for night in range(3)
        for index in range(20)
    ]


def test_pauses_within_nights_ignore_the_gap_between_nights() -> None:
    """Pauses inside a night are 10 s; between nights, over a day."""
    groups = tuning.group_by_target_and_night(_three_nights())
    within = tuning.pauses_within_nights(groups)
    assert len(within) == 57
    assert set(within) == {10.0}
    between = tuning.pauses_between_nights(groups)
    assert len(between) == 2
    assert min(between) > 80_000.0


def test_tracking_errors_leave_out_the_settling_solve() -> None:
    """The first solve of each run is dropped; the rest stay."""
    groups = tuning.group_by_target_and_night(_three_nights())
    errors = tuning.tracking_errors_arcsec(groups, 7200.0)
    assert len(errors) == 57
    assert set(errors) == {5.0}


def test_split_into_runs_breaks_at_a_long_pause() -> None:
    """A pause above the limit starts a second run."""
    solves = [_row(0, 0, 1.0), _row(0, 1, 1.0), _row(0, 500, 1.0)]
    assert [len(run) for run in tuning.split_into_runs(solves, 100.0)] == [2, 1]
    assert tuning.split_into_runs([], 100.0) == []


def test_radius_distances_measure_from_the_first_solve() -> None:
    """Every solve after the first lies 0.01 degree from it in declination."""
    groups = tuning.group_by_target_and_night(_three_nights())
    distances = tuning.radius_distances_deg(groups)
    assert len(distances) == 57
    assert distances == pytest.approx([0.01] * 57)


def _inputs(rows: list[dict]) -> TuningInputs:
    """Build tuning inputs whose wayfinder holds the given alignment rows.

    Parameters
    ----------
    rows : `list` [`dict`]
        The stored alignment rows.

    Returns
    -------
    inputs : `TuningInputs`
        Inputs with a fake ``"wayfinder"`` service.
    """
    records = SimpleNamespace(get_session_alignment_attempts=lambda _night, limit: rows)
    wayfinder = SimpleNamespace(control=SimpleNamespace(_context=SimpleNamespace(records=records)))
    return TuningInputs(_SETUP, None, None, {"wayfinder": wayfinder})


def test_the_tuner_returns_the_known_limits() -> None:
    """The limits match the synthetic solves."""
    result = tuning.tune_alignment_grouping(_inputs(_three_nights()))
    assert result.values[RUN_GAP_SECONDS.name] == pytest.approx(10.0)
    assert result.values[ASSUMED_CADENCE_SECONDS.name] == pytest.approx(10.0)
    assert result.values[SLEW_ERROR_ARCSEC.name] == pytest.approx(5.0)
    assert result.values[SAME_TARGET_RADIUS_DEG.name] == pytest.approx(0.01)
    assert result.sample == 57


def test_the_tuner_skips_without_two_solves_on_a_night() -> None:
    """No rows, or one solve per night, give nothing to tune."""
    with pytest.raises(TuningSkippedError):
        tuning.tune_alignment_grouping(_inputs([]))
    with pytest.raises(TuningSkippedError):
        tuning.tune_alignment_grouping(_inputs([_row(0, 0, 1.0), _row(1, 0, 1.0)]))


def test_the_tuner_skips_when_night_pauses_reach_the_gap_between_nights() -> None:
    """Pauses as long as the gap between nights cannot define a run."""
    rows = [_row(0, index * 9000, 1.0) for index in range(3)] + [_row(1, 0, 1.0)]
    # The pauses inside night 0 are 90,000 s, longer than the 86,400 s
    # that pass before night 1 begins.
    with pytest.raises(TuningSkippedError):
        tuning.tune_alignment_grouping(_inputs(rows))
