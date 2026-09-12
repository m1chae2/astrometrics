"""Purpose: Validate SpectroscopyInstrument's dispersion-geometry math.

Description: `dx_dlambda`/`expected_length_px` used to come from an
independently hand-derived textbook formula (``L / (d * cos(theta))``) that
only approximately matches this pipeline's own exact ``x = L*tan(theta)``,
``lambda = d*sin(theta)`` geometry -- and drifts further from it as theta
grows for higher-dispersion gratings. These tests check the computed values
against an independent numerical derivative of that exact geometry, and
check that the instrument's pixel<->wavelength helpers agree with
`optics_physics`'s canonical implementation, so a reintroduced small-angle
approximation or a re-diverged duplicate formula would fail loudly instead
of silently drifting.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.spectroscopy.optics_physics import (
    calculate_pixel_offset,
    calculate_wavelength,
)
from astrometricslib.pipelines.spectroscopy.spectroscopy_instrument import (
    SpectroscopyInstrument,
)
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig


def _build_config(  # ruff: ignore[missing-return-type-private-function]
    lines_per_mm: float, grating_distance_mm: float = 16.5, dispersion_start_px: float | None = None
):
    """Build a `SpectroscopyConfig` for a 350-900nm sensor.

    Returns
    -------
    config : `SpectroscopyConfig`
        The constructed configuration.
    """
    camera = CameraConfig(
        name="TestCam",
        pixel_size_um=3.76,
        sensor_width_px=3000,
        sensor_height_px=2000,
        sensor_min_wavelength=350.0,
        sensor_max_wavelength=900.0,
    )
    return SpectroscopyConfig(
        camera=camera,
        grating_lines_per_mm=lines_per_mm,
        grating_distance_mm=grating_distance_mm,
        dispersion_start_px=dispersion_start_px,
    )


def _numeric_dx_dlambda_mm_per_mm(  # ruff: ignore[missing-return-type-private-function]
    config: SpectroscopyConfig, lambda_c_nm: float
):
    """Independently derive dx/dlambda by numerical differentiation.

    Differentiates the pipeline's own exact x = L*tan(theta), lambda =
    d*sin(theta) geometry directly, without relying on any analytic
    simplification of that derivative.

    Returns
    -------
    dx_dlambda : `float`
        The numerically estimated dx/dlambda, in mm/mm.
    """
    d_mm = config.d_mm
    grating_distance_mm = config.grating_distance_mm

    def x_mm_at(wavelength_nm: float) -> float:
        wavelength_mm = wavelength_nm * 1e-6
        theta = np.arcsin(wavelength_mm / d_mm)
        return grating_distance_mm * np.tan(theta)

    h_nm = 1e-4
    return (x_mm_at(lambda_c_nm + h_nm) - x_mm_at(lambda_c_nm - h_nm)) / (2 * h_nm * 1e-6)


@pytest.mark.parametrize("lines_per_mm", [100.0, 300.0])
def test_dx_dlambda_matches_exact_geometry_derivative(lines_per_mm: float) -> None:
    """dx_dlambda must match the true derivative of the exact geometry.

    The small-angle approximation `L/(d*cos(theta))` under-predicts
    dx_dlambda increasingly as the dispersion angle grows, so this is
    checked at both a low-dispersion (100 lines/mm) and a
    higher-dispersion (300 lines/mm) grating.
    """
    config = _build_config(lines_per_mm)
    instrument = SpectroscopyInstrument(config)

    lambda_c_nm = (config.camera.sensor_min_wavelength + config.camera.sensor_max_wavelength) / 2.0
    expected = _numeric_dx_dlambda_mm_per_mm(config, lambda_c_nm)

    assert instrument.dx_dlambda == pytest.approx(expected, rel=1e-4)


def test_small_angle_dispersion_formula_is_measurably_wrong_at_high_dispersion() -> None:
    """Guard against reintroducing the small-angle L/(d*cos(theta)) formula.

    For a high-dispersion grating the two formulas disagree by several
    percent, so a regression would be caught here even if a low-dispersion
    default case happened to still look fine.
    """
    config = _build_config(lines_per_mm=300.0)
    instrument = SpectroscopyInstrument(config)

    small_angle_formula = config.grating_distance_mm / (config.d_mm * np.cos(instrument.theta))

    relative_difference = abs(instrument.dx_dlambda - small_angle_formula) / instrument.dx_dlambda
    assert relative_difference > 0.03


def test_wavelength_at_pixel_offset_matches_calculate_wavelength() -> None:
    """`wavelength_at_pixel_offset` must delegate to `optics_physics`.

    It must not re-derive its own copy of the grating-equation math.
    """
    config = _build_config(lines_per_mm=150.0)
    instrument = SpectroscopyInstrument(config)

    for px_offset in (0.0, 250.0, -100.0, 800.0):
        expected = calculate_wavelength(
            pixel_offset_px=px_offset,
            grating_distance_mm=config.grating_distance_mm,
            lines_per_mm=config.grating_lines_per_mm,
            pixel_size_um=config.camera.pixel_size_um,
        )
        assert instrument.wavelength_at_pixel_offset(px_offset) == pytest.approx(expected)


def test_zero_order_offset_matches_calculate_pixel_offset_at_min_wavelength() -> None:
    """The auto-computed zero_order_offset_px must match the shared helper.

    It should agree with `calculate_pixel_offset` at the sensor's minimum
    wavelength.
    """
    config = _build_config(lines_per_mm=100.0)
    instrument = SpectroscopyInstrument(config)

    expected = calculate_pixel_offset(
        wavelength_nm=config.camera.sensor_min_wavelength,
        grating_distance_mm=config.grating_distance_mm,
        lines_per_mm=config.grating_lines_per_mm,
        pixel_size_um=config.camera.pixel_size_um,
    )
    assert instrument.zero_order_offset_px == pytest.approx(expected)


def test_zero_order_offset_respects_manual_override() -> None:
    """A configured dispersion_start_px must be used as-is.

    It must bypass the physics calculation entirely.
    """
    config = _build_config(lines_per_mm=100.0, dispersion_start_px=123.4)
    instrument = SpectroscopyInstrument(config)
    assert instrument.zero_order_offset_px == pytest.approx(123.4)
