"""Purpose: Tests for the slow-frame ratio tuner.

Description: Checks the ratios of frame errors to their group's median, and
that the tuner skips when no capture is stored.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.parameters import TuningInputs, TuningSkippedError
from wayfindinglib.tasks.control_tasks import frame_guiding_tuning as tuning


def test_group_guide_ratios_divide_by_the_group_median() -> None:
    """Errors 1, 2 and 3 have the median 2, so the ratios are 0.5, 1, 1.5."""
    assert tuning.group_guide_ratios([1.0, 2.0, 3.0], 3) == pytest.approx([0.5, 1.0, 1.5])


def test_group_guide_ratios_need_enough_frames_and_a_positive_median() -> None:
    """A group with too few frames, or a median of zero, gives no ratios."""
    assert tuning.group_guide_ratios([1.0, 2.0], 3) == []
    assert tuning.group_guide_ratios([0.0, 0.0, 0.0], 3) == []


def test_the_tuner_skips_without_stored_captures() -> None:
    """With no Ekos session record there is no frame to compare."""
    records = SimpleNamespace(get_guiding_samples=lambda **_keywords: [])
    butler = SimpleNamespace(get_all=lambda _dataset: [])
    wayfinder = SimpleNamespace(
        control=SimpleNamespace(_context=SimpleNamespace(records=records, butler=butler))
    )
    setup = SetupConfig(name="test", camera_name="Camera", optic_name="Optic")
    with pytest.raises(TuningSkippedError):
        tuning.tune_slow_frame_ratio(TuningInputs(setup, None, None, {"wayfinder": wayfinder}))
