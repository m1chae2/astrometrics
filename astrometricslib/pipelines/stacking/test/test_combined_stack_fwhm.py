"""Tests for measuring the star width of a stack combined from exposure groups.

Where a group's stack is saturated, the combine takes another group's data, so
a bright star's core is patched and the combined image has no saturated plateau
left for the usual width check to skip. On the M 57 stack of 2026-10-03 the
check then measured only those patched stars and read 3.40 px against 2.38 px
for the rest. These tests check that the runner hands the check a mask of the
replaced pixels, and that a failed measurement never costs the stack.
"""

from unittest.mock import patch

import numpy as np
import pytest

from astrometricslib.pipelines.stacking import stack_runner

MEASURE = "astrometricslib.pipelines.astrometry.pre_processing.fwhm.measure_fwhm_from_data"


def make_group_images() -> tuple[np.ndarray, list[np.ndarray]]:
    """Build a long group clipped at one star, a short group, and their mean.

    Returns
    -------
    combined, images : `numpy.ndarray`, `list` [`numpy.ndarray`]
        A stand-in combined image and the two group stacks.
    """
    long_group = np.full((80, 80), 0.2, dtype=np.float32)
    short_group = np.full((80, 80), 0.1, dtype=np.float32)
    long_group[30:36, 40:46] = 1.0
    return (long_group + short_group) / 2.0, [long_group, short_group]


def test_the_check_is_given_a_mask_of_the_clipped_cores() -> None:
    """The mask covers the clipped star and nothing else."""
    combined, images = make_group_images()
    seen: dict[str, object] = {}

    def fake_measure(data: np.ndarray, excluded_mask: np.ndarray | None = None) -> float:
        """Record the mask and give a fixed width.

        Returns
        -------
        width : `float`
            A width of 2.4 pixels.
        """
        seen["mask"] = excluded_mask
        return 2.4

    with patch(MEASURE, side_effect=fake_measure):
        width = stack_runner._measure_combined_fwhm(combined, images)

    assert width == pytest.approx(2.4)
    mask = seen["mask"]
    assert isinstance(mask, np.ndarray)
    assert mask[32, 42]
    assert not mask[5, 5]


def test_a_failed_measurement_gives_no_width_instead_of_an_error() -> None:
    """The measurement is advisory: a failure leaves the width unknown."""
    combined, images = make_group_images()

    with patch(MEASURE, side_effect=ValueError("no stars")):
        assert stack_runner._measure_combined_fwhm(combined, images) is None


def test_no_stars_gives_no_width() -> None:
    """When nothing can be measured the width is unknown, not zero."""
    combined, images = make_group_images()

    with patch(MEASURE, return_value=None):
        assert stack_runner._measure_combined_fwhm(combined, images) is None
