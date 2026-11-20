"""Purpose: Tests for the Moon extinction tuner.

Description: Checks the search for the best coefficient on a made-up error
curve with a known minimum, that a flat error curve gives no value, the
frame-weighted mean of the held-out errors, and that the tuner skips when the
camera has no frames.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.parameters import TuningInputs, TuningSkippedError
from wayfindinglib.analytics.result_quality import moon_extinction_tuning as tuning


def test_best_extinction_finds_the_minimum_of_the_error_curve() -> None:
    """An error that is smallest at k = 0.3 gives 0.3."""
    best = tuning.best_extinction(lambda k: (k - 0.3) ** 2 + 1.0, 0.0, 0.6, 0.001)
    assert best == pytest.approx(0.3, abs=1e-3)


def test_best_extinction_is_none_for_a_flat_error_curve() -> None:
    """An error that does not depend on k gives no value."""
    assert tuning.best_extinction(lambda _k: 1.0, 0.0, 0.6, 0.01) is None


def test_weighted_mean_error_weights_by_frames() -> None:
    """A setting with three times the frames counts three times."""
    assert tuning.weighted_mean_error([0.1, 0.3], [30, 10]) == pytest.approx(0.15)


def test_the_tuner_skips_when_the_camera_has_no_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no frame to fit, there is no setting with a Moon term."""

    class FakeLibraryInputs:
        """A library that holds no frames."""

        def __init__(self, *_arguments: object) -> None:
            """Take the same arguments as the real class and keep none."""
            self.site = SimpleNamespace(latitude_deg=45.0, longitude_deg=-110.0, elevation_m=1500.0)

        def camera_observations(self, _camera: str) -> list:
            """Return no frames.

            Returns
            -------
            observations : `list`
                An empty list.
            """
            return []

    monkeypatch.setattr(tuning, "LibraryInputs", FakeLibraryInputs)
    monkeypatch.setattr(tuning, "butler_of", lambda _wayfinder: object())
    wayfinder = SimpleNamespace(astrometrics=object(), config=object())
    setup = SetupConfig(name="test", camera_name="Camera", optic_name="Optic")
    with pytest.raises(TuningSkippedError):
        tuning.tune_moon_extinction(TuningInputs(setup, None, None, {"wayfinder": wayfinder}))
