"""Tests of the clipped-at-zero test that every pipeline shares.

The limit is `CLIPPED_FRAME_ZERO_FRACTION`. A frame is clipped when its share
of exact zeros is above it.
"""

import numpy as np

from astrometricslib.pipelines.shared.quality.clipped_frames import (
    CLIPPED_FRAME_ZERO_FRACTION,
    frame_is_clipped_at_zero,
)


def test_a_frame_with_no_zeros_is_not_clipped() -> None:
    """Sky well above the offset leaves no exact zeros."""
    assert not frame_is_clipped_at_zero(np.full((10, 10), 300.0))


def test_the_limit_decides_between_clipped_and_not() -> None:
    """A zero share just under the limit passes, just over it is clipped."""
    pixels = 100
    under = np.ones(pixels)
    under[: int(CLIPPED_FRAME_ZERO_FRACTION.default * pixels)] = 0.0
    over = under.copy()
    over[: int(CLIPPED_FRAME_ZERO_FRACTION.default * pixels) + 1] = 0.0

    assert not frame_is_clipped_at_zero(under)
    assert frame_is_clipped_at_zero(over)
