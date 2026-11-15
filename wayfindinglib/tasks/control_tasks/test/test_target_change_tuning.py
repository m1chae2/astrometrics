"""Purpose: Tests for the new-position distance tuner.

Description: Checks the distances between consecutive slew ends, the split of
small centering steps from large moves, and that the tuner skips with no
slews or with no gap between the two kinds.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.parameters import TuningInputs, TuningSkippedError
from wayfindinglib.tasks.control_tasks import target_change_tuning as tuning
from wayfindinglib.tasks.control_tasks.target_changes import NEW_POSITION_DEGREES

_SETUP = SetupConfig(name="test", camera_name="Camera", optic_name="Optic")
"""A setup the tuner does not look at."""


def _inputs(monkeypatch: pytest.MonkeyPatch, positions: list[tuple[float, float]]) -> TuningInputs:
    """Build inputs whose recorded slews end at the given positions.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the reader of slew ends.
    positions : `list` [`tuple` [`float`, `float`]]
        Right ascension and declination of each slew end, in degrees.

    Returns
    -------
    inputs : `TuningInputs`
        Inputs with a fake ``"wayfinder"`` service.
    """
    monkeypatch.setattr(tuning, "slew_end_positions", lambda _context: positions)
    wayfinder = SimpleNamespace(control=SimpleNamespace(_context=object()))
    return TuningInputs(_SETUP, None, None, {"wayfinder": wayfinder})


def test_consecutive_separations_measure_neighboring_slew_ends() -> None:
    """Two ends 0.1 degree apart, then a move of 30 degrees."""
    separations = tuning.consecutive_separations_deg([(10.0, 0.0), (10.0, 0.1), (10.0, 30.1)])
    assert separations == pytest.approx([0.1, 30.0], abs=1e-6)
    assert tuning.consecutive_separations_deg([(10.0, 0.0)]) == []


def test_the_tuner_puts_the_limit_between_centering_steps_and_new_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Small steps and moves of 20 degrees split between the two."""
    positions = [(10.0, 0.0), (10.0, 0.05), (10.0, 0.15), (10.0, 20.15), (10.0, 20.2), (10.0, 40.2)]
    result = tuning.tune_new_position(_inputs(monkeypatch, positions))
    assert 0.1 < result.values[NEW_POSITION_DEGREES.name] < 20.0
    assert result.sample == 5


def test_the_tuner_skips_without_slews(monkeypatch: pytest.MonkeyPatch) -> None:
    """No recorded slew gives no distance."""
    with pytest.raises(TuningSkippedError):
        tuning.tune_new_position(_inputs(monkeypatch, []))


def test_the_tuner_skips_when_the_distances_show_no_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evenly spread distances are one kind of slew, not two."""
    positions = [(10.0, 0.0), (10.0, 1.0), (10.0, 3.0), (10.0, 6.0), (10.0, 10.0)]
    with pytest.raises(TuningSkippedError, match="no gap"):
        tuning.tune_new_position(_inputs(monkeypatch, positions))
