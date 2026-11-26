"""Purpose: Unit tests for the standard-star offset tuner.

Description: Checks `offset_from_center` and `separating_limit` on known
numbers, and that the tuner stays silent with no data.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.foundation.tuning import TuningInputs, TuningSkippedError
from astrometricslib.pipelines.spectroscopy.pre_processing.standard_star_tuning import (
    offset_from_center,
    separating_limit,
    tune_standard_star_offset,
)


def test_offset_from_center_is_the_distance() -> None:
    """Offset from center is the distance.

    A star 3 px right and 4 px below the center of a 100 by 100 frame is 5 px
    off.
    """
    assert offset_from_center((50.0, 50.0), (100, 100)) == pytest.approx(0.0)
    assert offset_from_center((53.0, 54.0), (100, 100)) == pytest.approx(5.0)


def test_separating_limit_is_between_the_two_groups() -> None:
    """Separating limit is between the two groups.

    The limit lies midway between the real offsets' high end and the wrong
    sources' low end.
    """
    limit = separating_limit([10.0, 12.0, 14.0], [30.0, 40.0, 50.0], 0.95)
    assert limit == pytest.approx(0.5 * (13.8 + 31.0))


def test_separating_limit_is_none_when_the_groups_overlap() -> None:
    """A wrong source as close as a real star leaves no separating limit."""
    assert separating_limit([10.0, 30.0], [20.0, 40.0], 0.95) is None


def test_tune_skips_with_no_targets() -> None:
    """A library with no spectral target gives a skip, not a guess."""
    no_targets = SimpleNamespace(objects=[])
    astrometrics = SimpleNamespace(targets=SimpleNamespace(query=lambda **_kwargs: no_targets))
    setup = SetupConfig(name="s", camera_name="cam", optic_name="optic")
    inputs = TuningInputs(setup, None, None, {"astrometrics": astrometrics})
    with pytest.raises(TuningSkippedError):
        tune_standard_star_offset(inputs)
