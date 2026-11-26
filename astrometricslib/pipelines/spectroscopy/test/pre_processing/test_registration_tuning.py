"""Purpose: Unit tests for the star-registration tuner.

Description: Checks the pure functions of `registration_tuning` on synthetic
star positions with a known shift, that the offset-pair limit shares the
control-point declaration, and that the tuner stays silent with no data.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.foundation.tuning import TuningInputs, TuningSkippedError
from astrometricslib.pipelines.spectroscopy.pre_processing import spectral_star_registration
from astrometricslib.pipelines.spectroscopy.pre_processing.registration_tuning import (
    REGISTRATION_SEARCH_TUNER,
    TargetRegistration,
    chance_vote_peak,
    match_distance,
    measure_target_registration,
    required_vote_count,
    search_windows,
    tune_registration_search,
    vote_bin_width,
)

_POINTS = np.array([(0.0, 0.0), (130.0, 7.0), (51.0, 201.0), (222.0, 90.0), (310.0, 333.0)])


def _registration(shift: tuple[float, float], residuals: list[list[float]]) -> TargetRegistration:
    """Build a registration with a given shift and scatter.

    Returns
    -------
    registration : `TargetRegistration`
        The target, with five reference stars shifted by ``shift``.
    """
    return TargetRegistration("T", shift, np.array(residuals), _POINTS, _POINTS + np.array(shift))


def test_measure_target_registration_finds_the_shift() -> None:
    """Stars moved by a fixed shift give that shift and zero scatter."""
    placed = {"a": (0.0, 0.0), "b": (10.0, 0.0), "c": (0.0, 10.0), "d": (5.0, 5.0)}
    spectral = {name: (x + 100.0, y + 50.0) for name, (x, y) in placed.items()}
    registration = measure_target_registration("T", placed, spectral, 3)
    assert registration is not None
    assert registration.shift == pytest.approx((100.0, 50.0))
    assert np.allclose(registration.residuals, 0.0)


def test_measure_target_registration_needs_enough_stars() -> None:
    """Fewer shared stars than the minimum give no measurement."""
    assert measure_target_registration("T", {"a": (0.0, 0.0)}, {"a": (1.0, 1.0)}, 3) is None


def test_bin_and_match_distance_follow_the_scatter() -> None:
    """Bin and match distance follow the scatter.

    Twice the largest axis scatter is the bin; the largest radial scatter is
    the match distance.
    """
    registration = _registration((0.0, 0.0), [[3.0, 4.0], [0.0, 0.0], [-1.0, 0.0]])
    assert vote_bin_width([registration], 1.0) == pytest.approx(8.0)
    assert match_distance([registration], 1.0) == pytest.approx(5.0)


def test_search_windows_cover_the_shifts_with_a_margin() -> None:
    """Search windows cover the shifts with a margin.

    With full coverage both windows are the largest axis shift times the
    margin.
    """
    near = _registration((100.0, 0.0), [[0.0, 0.0]])
    far = _registration((0.0, -300.0), [[0.0, 0.0]])
    assert search_windows([near, far], 1.0, 1.5) == pytest.approx((450.0, 450.0))
    blind, solution = search_windows([near, far], 0.5, 1.0)
    assert blind == pytest.approx(200.0)
    assert solution == pytest.approx(300.0)


def test_chance_vote_peak_leaves_out_the_real_shift() -> None:
    """Chance vote peak leaves out the real shift.

    The real shift fills one bin with every star; leaving it out leaves at most
    a chance vote.
    """
    registration = _registration((40.0, 20.0), [[0.0, 0.0]])
    assert chance_vote_peak(registration, 500.0, 6.0, -1.0) == 5
    assert chance_vote_peak(registration, 500.0, 6.0, 12.0) <= 1


def test_required_vote_count_is_one_above_the_chance_peak() -> None:
    """The count exceeds the quantile of the chance peaks by one."""
    assert required_vote_count([1, 2, 3, 3], 1.0) == 4


def test_offset_pair_count_shares_the_control_point_declaration() -> None:
    """Offset pair count shares the control point declaration.

    The offset pair limit is the control point limit, so one tuner sets both.
    """
    assert (
        spectral_star_registration._MINIMUM_OFFSET_PAIR_COUNT
        is spectral_star_registration._MIN_CONTROL_POINTS
    )
    assert spectral_star_registration._MIN_CONTROL_POINTS in REGISTRATION_SEARCH_TUNER.parameters


def test_tune_skips_with_no_targets() -> None:
    """A library with no spectral target gives a skip, not a guess."""
    no_targets = SimpleNamespace(objects=[])
    astrometrics = SimpleNamespace(targets=SimpleNamespace(query=lambda **_kwargs: no_targets))
    setup = SetupConfig(name="s", camera_name="cam", optic_name="optic")
    inputs = TuningInputs(setup, None, None, {"astrometrics": astrometrics})
    with pytest.raises(TuningSkippedError):
        tune_registration_search(inputs)
