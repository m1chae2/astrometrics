"""Tests for choosing a weighting mode when exposure lengths differ."""

import pytest

from astrometricslib.pipelines.stacking.pre_processing import exposure_weighting as ew


def test_equal_exposures_leave_no_penalty() -> None:
    """Frames of one length gain nothing from weighting."""
    assert ew.equal_weight_noise_penalty([120.0] * 10) == pytest.approx(1.0)
    assert ew.equal_weight_noise_penalty([]) == pytest.approx(1.0)


def test_the_penalty_matches_the_m_27_exposure_mix() -> None:
    """26 x 30 s, 33 x 60 s, 22 x 120 s and 1 x 300 s cost 17% noise."""
    exposures = [30.0] * 26 + [60.0] * 33 + [120.0] * 22 + [300.0]

    assert ew.equal_weight_noise_penalty(exposures) == pytest.approx(1.166, abs=0.005)


def test_mixed_exposures_choose_noise_weighting() -> None:
    """A stack of mixed exposures is weighted by noise when nothing is set."""
    assert ew.choose_stack_weight([30.0, 60.0, 120.0], None, is_spectral=False) == "noise"


def test_nearly_equal_exposures_are_left_alone() -> None:
    """A 2% difference in exposure is not worth a weighting."""
    assert ew.choose_stack_weight([120.0, 122.0, 120.0], None, is_spectral=False) is None


def test_a_configured_weight_always_wins() -> None:
    """The user's own choice is never replaced."""
    assert ew.choose_stack_weight([30.0, 120.0], "wfwhm", is_spectral=False) == "wfwhm"


def test_spectral_stacks_are_left_to_their_own_handling() -> None:
    """Spectral frames of different lengths are grouped, not weighted here."""
    assert ew.choose_stack_weight([0.1, 4.0], None, is_spectral=True) is None
