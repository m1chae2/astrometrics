"""Tests for reusing the stacking step's star width in astrometry."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrometricslib.pipelines.astrometry import runner

MEASURE = "astrometricslib.pipelines.astrometry.pre_processing.fwhm.measure_image_fwhm"


def make_target(stacked_image: str, stored_fwhm: float | None) -> SimpleNamespace:
    """Build a stand-in target with a stacked image and a stored width.

    Parameters
    ----------
    stacked_image : `str`
        Path recorded as the target's stacked image.
    stored_fwhm : `float` or `None`
        Star width held by the stacking quality summary.

    Returns
    -------
    target : `SimpleNamespace`
        An object shaped like `Target` for the fields the lookup reads.
    """
    metrics = SimpleNamespace(stacked_fwhm_px=stored_fwhm)
    summary = SimpleNamespace(stacking_metrics=metrics)
    return SimpleNamespace(stacking=SimpleNamespace(stacked_image=stacked_image, quality_summary=summary))


def test_the_stored_width_is_reused_for_the_stacked_image(tmp_path: Path) -> None:
    """The same file with a stored width is not measured again."""
    stack = tmp_path / "stack.fits"
    image = SimpleNamespace(path=str(tmp_path / "." / "stack.fits"))
    with patch(MEASURE, return_value=9.9) as measure:
        width = runner._measure_star_fwhm_px(image, make_target(str(stack), 2.5))
    assert width == pytest.approx(2.5)
    measure.assert_not_called()


def test_the_image_is_measured_when_nothing_is_stored_or_the_file_differs(tmp_path: Path) -> None:
    """No stored width, another file, or no target means measuring."""
    image = SimpleNamespace(path=str(tmp_path / "other.fits"))
    cases = [
        make_target(str(tmp_path / "stack.fits"), 2.5),
        make_target(str(tmp_path / "other.fits"), None),
        None,
    ]
    for target in cases:
        with patch(MEASURE, return_value=9.9) as measure:
            assert runner._measure_star_fwhm_px(image, target) == pytest.approx(9.9)
        measure.assert_called_once()
