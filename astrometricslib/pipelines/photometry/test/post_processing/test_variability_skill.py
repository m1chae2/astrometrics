"""Tests for the check that a run's scatter statistic can see variables.

The arithmetic is checked on known answers, and the decision is checked on
simulated fields: one where catalogued variables really do vary more (it must
pass), and one where nothing differs (it must fail about 95% of the time, the
stated level), so the check is neither blind nor trigger-happy.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.photometry.post_processing import variability_skill as skill


def test_a_sinusoid_with_a_ten_percent_scatter_changes_by_about_three_tenths_of_a_magnitude() -> None:
    """Scatter 0.1 means peak-to-peak 0.283 of the mean, about 0.31 mag."""
    assert skill.minimum_detectable_amplitude_mag(0.1) == pytest.approx(0.307, abs=0.002)
    assert skill.minimum_detectable_amplitude_mag(0.0) == pytest.approx(0.0)


def test_the_required_auc_shrinks_as_the_groups_grow() -> None:
    """More stars means a smaller excess over one half is convincing."""
    assert skill.required_auc(10, 30) > skill.required_auc(100, 300) > 0.5
    assert skill.required_auc(10, 30) == pytest.approx(0.5 + 1.645 * np.sqrt(41 / (12 * 300)))


def test_groups_that_are_too_small_give_no_answer() -> None:
    """Too few catalogued variables or unlisted stars give None."""
    assert skill.discrimination([0.3] * 9, [0.1] * 100) is None
    assert skill.discrimination([0.3] * 20, [0.1] * 29) is None


def test_clearly_higher_scatter_passes() -> None:
    """Variables well above the others are seen."""
    result = skill.discrimination([0.4] * 20, list(np.linspace(0.05, 0.2, 60)))

    assert result.sees_known_variables
    assert result.auc == pytest.approx(1.0)


def test_identical_groups_do_not_pass() -> None:
    """The same values for both groups are chance, not skill."""
    values = list(np.linspace(0.05, 0.2, 80))

    assert not skill.discrimination(values[:20], values[20:]).sees_known_variables


def test_the_decision_is_neither_blind_nor_trigger_happy() -> None:
    """Simulate many fields: skill is found, and noise passes about 5%."""
    generator = np.random.default_rng(12)
    real_passes = 0
    noise_passes = 0
    fields = 400
    for _ in range(fields):
        unlisted = generator.lognormal(np.log(0.1), 0.4, size=80)
        real_passes += skill.discrimination(
            generator.lognormal(np.log(0.2), 0.4, size=15), unlisted
        ).sees_known_variables
        noise_passes += skill.discrimination(
            generator.lognormal(np.log(0.1), 0.4, size=15), unlisted
        ).sees_known_variables

    assert real_passes / fields > 0.9
    assert noise_passes / fields == pytest.approx(0.05, abs=0.03)
