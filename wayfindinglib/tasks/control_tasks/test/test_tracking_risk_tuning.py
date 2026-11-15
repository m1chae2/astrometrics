"""Purpose: Tests for the tracking-risk blend tuner.

Description: Checks the leave-one-out error on targets with known jitter, that
the grid search returns numbers inside the allowed ranges and the best one,
and that the tuner skips when the plate scale, the location or the targets are
missing.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.parameters import TuningInputs, TuningSkippedError
from wayfindinglib.analytics.tracking_risk import (
    FULL_CONFIDENCE_SOLVES,
    INFLUENCE_RADIUS_DEG,
    MAX_MEASURED_SHARE,
    BlendSettings,
    TrackedTarget,
)
from wayfindinglib.tasks.control_tasks import tracking_risk_tuning as tuning

_SETUP = SetupConfig(name="test", camera_name="Camera", optic_name="Optic")
"""A setup the tuner reads only for its names."""


def _targets() -> list[TrackedTarget]:
    """Build four nearby targets that all track the same way.

    Returns
    -------
    targets : `list` [`TrackedTarget`]
        Targets within a few degrees of each other, each 1 arcsecond jitter.
    """
    return [
        TrackedTarget(ha_deg=-30.0 + 2.0 * i, dec_deg=40.0, rms_arcsec=1.0, solve_count=40) for i in range(4)
    ]


def test_nearby_targets_are_predicted_better_by_a_strong_pull_than_a_weak_one() -> None:
    """Agreeing neighbors predict better when they may decide the score."""
    strong = BlendSettings(25.0, 5.0, 0.95)
    weak = BlendSettings(25.0, 5.0, 0.1)
    assert tuning.leave_one_out_error(_targets(), strong, 45.0, 1.0) < tuning.leave_one_out_error(
        _targets(), weak, 45.0, 1.0
    )


def test_the_grid_search_stays_inside_the_allowed_ranges() -> None:
    """The best numbers lie between the limits of each parameter."""
    settings, error = tuning.best_blend_settings(_targets(), 45.0, 1.0, 3)
    assert (
        INFLUENCE_RADIUS_DEG.allowed_range[0]
        <= settings.influence_radius_deg
        <= INFLUENCE_RADIUS_DEG.allowed_range[1]
    )
    assert (
        FULL_CONFIDENCE_SOLVES.allowed_range[0]
        <= settings.full_confidence_solves
        <= FULL_CONFIDENCE_SOLVES.allowed_range[1]
    )
    assert (
        MAX_MEASURED_SHARE.allowed_range[0]
        <= settings.max_measured_share
        <= MAX_MEASURED_SHARE.allowed_range[1]
    )
    assert error == pytest.approx(tuning.leave_one_out_error(_targets(), settings, 45.0, 1.0))


def test_the_tuner_skips_without_a_plate_scale(monkeypatch: pytest.MonkeyPatch) -> None:
    """A setup whose camera or optic is not in the catalog cannot be scored."""
    monkeypatch.setattr(tuning, "_plate_scale", lambda _inputs, _config: None)
    wayfinder = SimpleNamespace(control=SimpleNamespace(_context=SimpleNamespace()), config=object())
    with pytest.raises(TuningSkippedError, match="catalog"):
        tuning.tune_tracking_blend(TuningInputs(_SETUP, None, None, {"wayfinder": wayfinder}))


def test_the_tuner_skips_without_an_observer_location(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hour angles need the observer's longitude."""
    monkeypatch.setattr(tuning, "_plate_scale", lambda _inputs, _config: 1.0)
    context = SimpleNamespace(observer_location=lambda: None)
    wayfinder = SimpleNamespace(control=SimpleNamespace(_context=context), config=object())
    with pytest.raises(TuningSkippedError, match="location"):
        tuning.tune_tracking_blend(TuningInputs(_SETUP, None, None, {"wayfinder": wayfinder}))


def test_the_tuner_skips_without_measured_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no stored solves there are fewer than two targets."""
    monkeypatch.setattr(tuning, "_plate_scale", lambda _inputs, _config: 1.0)
    context = SimpleNamespace(
        observer_location=lambda: {"latitude": 45.0, "longitude": -110.0, "elevation": 1500.0},
        records=SimpleNamespace(get_session_alignment_attempts=lambda _night, limit: []),
    )
    wayfinder = SimpleNamespace(control=SimpleNamespace(_context=context), config=object())
    with pytest.raises(TuningSkippedError, match="fewer than two"):
        tuning.tune_tracking_blend(TuningInputs(_SETUP, None, None, {"wayfinder": wayfinder}))
