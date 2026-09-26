"""Purpose: Verify the second-order risk audit field.

Description: `compute_second_order_blue_to_red_ratio` reports how many times
brighter a star is at half each wavelength, and the pipeline stores it on the
star. These tests check the ratio's values at known wavelengths, the guards
against dim or negative red values, the risky-wavelength threshold, and that
the pipeline records the field without altering the spectrum.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from astrometricslib.models.stellar_source import SpectroscopyResult, StellarObject
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
from astrometricslib.pipelines.spectroscopy.second_order_risk import (
    MAXIMUM_STORED_RATIO,
    compute_second_order_blue_to_red_ratio,
    is_second_order_risky,
)
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig

# A spectrum sampled every 100 A from 3800 A to 9900 A.
WAVELENGTHS_ANGSTROM = np.arange(3800.0, 10000.0, 100.0)
# Index of the 9000 A sample, used by several tests below.
INDEX_AT_9000 = int(np.argmin(np.abs(WAVELENGTHS_ANGSTROM - 9000.0)))


def test_ratio_compares_the_brightness_at_half_the_wavelength() -> None:
    """At 9000 A the ratio is the brightness at 4500 A over that at 9000 A."""
    intensity = np.where(WAVELENGTHS_ANGSTROM <= 5000.0, 40.0, 2.0)

    ratio = compute_second_order_blue_to_red_ratio(WAVELENGTHS_ANGSTROM, intensity)

    assert ratio[INDEX_AT_9000] == pytest.approx(40.0 / 2.0)


def test_ratio_is_zero_where_half_the_wavelength_was_not_measured() -> None:
    """Below twice the first wavelength there is nothing to compare with."""
    ratio = compute_second_order_blue_to_red_ratio(
        WAVELENGTHS_ANGSTROM, np.full(WAVELENGTHS_ANGSTROM.size, 5.0)
    )

    assert np.allclose(ratio[WAVELENGTHS_ANGSTROM < 7600.0], 0.0)
    assert np.allclose(ratio[WAVELENGTHS_ANGSTROM >= 7600.0], 1.0)


def test_ratio_is_capped_when_the_red_brightness_is_zero_or_negative() -> None:
    """A dim red end must give the cap, not infinity or a negative number."""
    intensity = np.where(WAVELENGTHS_ANGSTROM <= 5000.0, 40.0, -1.0)

    ratio = compute_second_order_blue_to_red_ratio(WAVELENGTHS_ANGSTROM, intensity)

    assert np.all(np.isfinite(ratio))
    assert ratio[INDEX_AT_9000] == pytest.approx(MAXIMUM_STORED_RATIO)


def test_ratio_is_zero_when_the_blue_brightness_is_not_positive() -> None:
    """Sky-subtracted noise can be negative; that is not a risk."""
    ratio = compute_second_order_blue_to_red_ratio(
        WAVELENGTHS_ANGSTROM, np.full(WAVELENGTHS_ANGSTROM.size, -3.0)
    )

    assert np.allclose(ratio, 0.0)


def test_an_empty_spectrum_gives_an_empty_ratio() -> None:
    """No samples in means no samples out."""
    assert compute_second_order_blue_to_red_ratio(np.array([]), np.array([])).size == 0


def test_a_hot_stars_red_end_is_risky_and_a_cool_stars_is_not() -> None:
    """A blue-bright star is flagged in the red; a red-bright one is not."""
    hot = np.where(WAVELENGTHS_ANGSTROM <= 5000.0, 40.0, 1.0)
    cool = np.where(WAVELENGTHS_ANGSTROM <= 5000.0, 1.0, 5.0)

    hot_risky = is_second_order_risky(compute_second_order_blue_to_red_ratio(WAVELENGTHS_ANGSTROM, hot))
    cool_risky = is_second_order_risky(compute_second_order_blue_to_red_ratio(WAVELENGTHS_ANGSTROM, cool))

    assert hot_risky[INDEX_AT_9000]
    assert not cool_risky.any()


def _build_pipeline() -> SpectroscopyPipeline:
    """Build a small `SpectroscopyPipeline` for recording a result.

    Returns
    -------
    pipeline : `SpectroscopyPipeline`
        The constructed pipeline.
    """
    camera = CameraConfig(
        name="Some Other Camera",
        pixel_size_um=3.76,
        sensor_width_px=900,
        sensor_height_px=900,
        sensor_min_wavelength=300.0,
        sensor_max_wavelength=1000.0,
    )
    return SpectroscopyPipeline(config=SpectroscopyConfig(camera=camera, grating_distance_mm=16.5))


def test_pipeline_records_the_ratio_without_changing_the_spectrum() -> None:
    """The stored ratio lines up with the spectrum, which is left as it was."""
    pipeline = _build_pipeline()
    star = StellarObject(id="TestStar")
    wavelength_nm = np.arange(380.0, 1000.0, 10.0)
    intensities = np.where(wavelength_nm <= 500.0, 40.0, 2.0)
    result = {
        "detected_angle": 0.0,
        "wavelengths": wavelength_nm.tolist(),
        "intensities": intensities.tolist(),
        "target_pos": (100.0, 200.0),
        "trail_centerline_px": None,
        "trail_width_px": None,
    }

    pipeline._apply_result_to_stellar_object(star, result, SimpleNamespace(timestamp=1700000000.0))

    stored = star.spectroscopy
    assert len(stored.second_order_blue_to_red_ratio) == len(stored.wavelengths_angstrom)
    assert stored.intensities == intensities.tolist()
    at_9000 = stored.wavelengths_angstrom.index(9000.0)
    assert stored.second_order_blue_to_red_ratio[at_9000] == pytest.approx(20.0)


def test_a_spectrum_saved_before_the_field_existed_loads_with_none() -> None:
    """Old stored results have no ratio; that must read back as `None`."""
    assert SpectroscopyResult().second_order_blue_to_red_ratio is None
    assert SpectroscopyResult.model_validate({
        "secondOrderBlueToRedRatio": [0.0, 2.5]
    }).second_order_blue_to_red_ratio == [
        0.0,
        2.5,
    ]
