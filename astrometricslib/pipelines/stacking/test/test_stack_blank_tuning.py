"""Tests for the tuner of the blank and over-subtracted stack limits.

The gap rule gets values shaped like the library's stacks: a clean group near
zero and a broken group near one.
"""

import pytest

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.foundation.tuning import TuningInputs, TuningSkippedError
from astrometricslib.pipelines.stacking.post_processing.stack_blank_tuning import (
    BLANK_STACK_TUNER,
    limit_in_widest_gap,
    negative_pixel_fractions,
)

SETUP = SetupConfig(name="Setup", camera_name="Camera", optic_name="Optic")


def test_limit_sits_in_the_middle_of_the_gap() -> None:
    """Clean stacks up to 0.06 and broken ones from 0.98 split there."""
    values = [0.0, 0.0, 0.01, 0.06, 0.98, 1.0]
    assert limit_in_widest_gap(values, minimum_gap=0.1) == pytest.approx((0.06 + 0.98) / 2)


def test_no_limit_when_the_stacks_are_all_clean() -> None:
    """Scatter among clean stacks is narrower than the minimum gap."""
    assert limit_in_widest_gap([0.0, 0.01, 0.02, 0.04], minimum_gap=0.1) is None


def test_no_limit_for_one_stack() -> None:
    """One stack has no neighbor to make a gap."""
    assert limit_in_widest_gap([0.3], minimum_gap=0.1) is None


def test_a_stack_without_a_warning_counts_as_zero_negative_pixels() -> None:
    """A stack with no stored warning counts as clean."""
    stacks = [("a", None), ("b", None)]
    assert negative_pixel_fractions(stacks, {("b", None): 0.99}) == [0.0, 0.99]


def test_tuner_skips_without_the_library() -> None:
    """With no library service given, the tuner raises the skip error."""
    with pytest.raises(TuningSkippedError):
        BLANK_STACK_TUNER.tune(TuningInputs(SETUP, None, None, {}))
