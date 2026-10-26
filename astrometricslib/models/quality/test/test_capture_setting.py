"""Tests for `CaptureSetting`: header precision must not split a setting."""

import pytest

from astrometricslib.models.quality.capture_setting import CaptureSetting


def _setting(pixel_scale_arcsec: float | None) -> CaptureSetting:
    """Build an M 13-like setting with one pixel scale.

    Parameters
    ----------
    pixel_scale_arcsec : `float` or `None`
        The pixel scale to give.

    Returns
    -------
    setting : `CaptureSetting`
        The setting.
    """
    return CaptureSetting(
        camera="ZWO ASI 533MM Pro",
        telescope="Apertura 75Q",
        focal_length_mm=405.0,
        pixel_scale_arcsec=pixel_scale_arcsec,
        filter_name="Luminance",
        exposure_seconds=30.0,
        gain_setting=0,
        offset=0,
        binning=1,
    )


def test_two_header_precisions_of_one_scale_are_one_setting() -> None:
    """The two M 13 header values give equal settings with one hash."""
    short, long = _setting(1.915279), _setting(1.9152790075)
    assert short == long
    assert len({short, long}) == 1
    assert short.pixel_scale_arcsec == pytest.approx(1.915, abs=1e-12)


def test_the_two_optics_stay_apart() -> None:
    """The 300 mm and 405 mm scales of the same camera remain two settings."""
    assert _setting(2.586) != _setting(1.915)


def test_scales_of_other_sizes_keep_four_significant_figures() -> None:
    """Rounding is by significant figures, not decimal places."""
    assert _setting(12.34567).pixel_scale_arcsec == pytest.approx(12.35, abs=1e-12)
    assert _setting(0.4567891).pixel_scale_arcsec == pytest.approx(0.4568, abs=1e-12)


def test_a_missing_or_zero_scale_is_kept() -> None:
    """None and zero pass through unchanged."""
    assert _setting(None).pixel_scale_arcsec is None
    assert _setting(0.0).pixel_scale_arcsec == pytest.approx(0.0, abs=0.0)
