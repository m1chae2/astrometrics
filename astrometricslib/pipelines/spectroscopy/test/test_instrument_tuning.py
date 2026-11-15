"""Purpose: Test the instrument tuners and the statistics they share.

Description: Issue #175 Phase D. Each tuner in `instrument_tuning.py` keeps
its arithmetic in functions that take plain numbers. These tests give those
functions synthetic data with a known answer, and check that each tuner
refuses to run without data.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from astrometricslib.foundation.tuning import Tuner, TuningInputs, TuningSkippedError
from astrometricslib.pipelines.spectroscopy import instrument_tuning
from astrometricslib.pipelines.spectroscopy.spectrum_tuning_data import (
    reference_label,
    require_spectral_filter,
    robust_sigma,
    symmetric_limit_from_spread,
    upper_limit_from_spread,
)

ALL_TUNERS = (
    instrument_tuning.USABLE_RANGE_TUNER,
    instrument_tuning.LINE_SPREAD_WINDOWS_TUNER,
    instrument_tuning.RESPONSE_FIT_TUNER,
    instrument_tuning.SECOND_ORDER_SHARE_TUNER,
    instrument_tuning.GAIA_XP_LIMITS_TUNER,
    instrument_tuning.COLOUR_LIMIT_TUNER,
)


class _EmptyCatalog:
    """A library catalog that holds no stars."""

    def list_star_summaries(self) -> list:
        """List the stars.

        Returns
        -------
        summaries : `list`
            Always empty.
        """
        return []


def _inputs(filter_name: str | None) -> TuningInputs:
    """Build tuner inputs over a library with no stars.

    Parameters
    ----------
    filter_name : `str` or `None`
        The filter the tuner is run for.

    Returns
    -------
    inputs : `TuningInputs`
        The inputs.
    """
    astrometrics = SimpleNamespace(_catalog_access=_EmptyCatalog())
    setup = SimpleNamespace(camera_name="Test camera")
    return TuningInputs(setup, filter_name, None, {"astrometrics": astrometrics})


def test_signal_to_noise_by_band_gives_the_median_in_each_band() -> None:
    """A spectrum with brightness ten times its error reads ten in a band."""
    wavelength = np.arange(4000.0, 5000.0, 10.0)
    result = instrument_tuning.signal_to_noise_by_band(
        wavelength,
        np.full(wavelength.size, 100.0),
        np.full(wavelength.size, 10.0),
        np.array([4000.0, 4500.0, 5000.0, 6000.0]),
    )
    assert result[0] == pytest.approx(10.0)
    assert result[1] == pytest.approx(10.0)
    # The spectrum ends at 4990 Å, so the band from 5000 Å holds no sample.
    assert np.isnan(result[2])


def test_signal_to_noise_by_band_is_nan_for_an_empty_band() -> None:
    """A band with no sample has no value."""
    result = instrument_tuning.signal_to_noise_by_band(
        np.array([4100.0]), np.array([5.0]), np.array([1.0]), np.array([4000.0, 4200.0, 4400.0])
    )
    assert result[0] == pytest.approx(5.0)
    assert np.isnan(result[1])


def test_usable_range_follows_the_peak_down_to_the_share() -> None:
    """The range runs over the bands at or above half the peak."""
    edges = np.array([4000.0, 4100.0, 4200.0, 4300.0, 4400.0, 4500.0, 4600.0])
    curve = np.array([1.0, 5.0, 10.0, 9.0, 4.0, 1.0])
    assert instrument_tuning.usable_range_from_curve(edges, curve, 0.5) == (4100.0, 4400.0)


def test_usable_range_stops_at_a_gap() -> None:
    """A band with no value ends the run even if later bands are high."""
    edges = np.arange(4000.0, 4501.0, 100.0)
    curve = np.array([10.0, 9.0, np.nan, 9.0, 9.0])
    assert instrument_tuning.usable_range_from_curve(edges, curve, 0.5) == (4000.0, 4200.0)


def test_usable_range_is_none_without_a_value() -> None:
    """A curve of NaN gives no range."""
    assert (
        instrument_tuning.usable_range_from_curve(np.array([0.0, 1.0, 2.0]), np.full(2, np.nan), 0.5) is None
    )


def test_median_curve_needs_the_minimum_stars() -> None:
    """A band seen by fewer stars than the minimum is NaN."""
    curves = np.array([[1.0, 2.0], [3.0, np.nan], [5.0, np.nan]])
    result = instrument_tuning.median_curve_across_stars(curves, 3)
    assert result[0] == pytest.approx(3.0)
    assert np.isnan(result[1])


def test_defocus_windows_sit_inside_the_usable_range() -> None:
    """The blue window starts at the low end and the red one a share along."""
    blue, red = instrument_tuning.defocus_windows(4000.0, 8000.0, 800.0, 0.5)
    assert blue == (4000.0, 4800.0)
    assert red == (6000.0, 6800.0)


def test_window_median_width_reports_the_median_and_scatter() -> None:
    """Failed fits (zero width) are left out of the median."""
    wavelength = np.arange(4200.0, 4300.0, 10.0)
    widths = np.array([2.0, 2.0, 2.0, 2.0, 2.0, 0.0, 2.0, 2.0, 2.0, 2.0])
    median, scatter, count = instrument_tuning.window_median_width(wavelength, widths, (4200.0, 4300.0))
    assert median == pytest.approx(2.0)
    assert scatter == pytest.approx(0.0)
    assert count == 9


def test_window_median_width_is_none_for_an_empty_window() -> None:
    """A window with no working fit has no median."""
    assert instrument_tuning.window_median_width(np.array([5000.0]), np.array([2.0]), (4200.0, 4300.0)) == (
        None,
        None,
        0,
    )


def test_samples_for_median_error_grows_with_the_scatter() -> None:
    """A 10% scatter and a 5% target need seven fits."""
    assert instrument_tuning.samples_for_median_error(0.1, 0.05) == 7
    assert instrument_tuning.samples_for_median_error(0.0, 0.05) == 1


def test_cross_validated_error_prefers_the_true_degree() -> None:
    """A cubic is predicted exactly by degree 3, badly by degree 1."""
    x = np.linspace(-1.0, 1.0, 60)
    y = 0.5 * x**3 - x
    assert instrument_tuning.cross_validated_error(x, y, 3, 5) < 1e-6
    assert instrument_tuning.cross_validated_error(x, y, 1, 5) > 0.05


def test_cross_validated_error_is_none_with_too_few_samples() -> None:
    """Pieces that leave fewer samples than the degree need give no error."""
    x = np.linspace(0.0, 1.0, 5)
    assert instrument_tuning.cross_validated_error(x, x, 6, 5) is None


def test_choose_degree_takes_the_lowest_within_the_tolerance() -> None:
    """A higher degree that is only marginally better is not chosen."""
    errors = {2: 0.10, 3: 0.0500, 4: 0.0495, 5: 0.0490}
    assert instrument_tuning.choose_degree(errors, 0.05) == 3


def test_wing_radius_finds_the_end_of_a_wing() -> None:
    """A residual of 5% out to one line spread ends at that distance."""
    distance = np.arange(0.0, 4.0, 0.05)
    residual = np.where(distance < 1.0, 0.05, 0.0)
    radius = instrument_tuning.wing_radius(distance, residual, 0.0, 0.25, 4.0, 0.01)
    assert radius == pytest.approx(1.0)


def test_wing_radius_is_zero_without_a_wing_and_none_without_samples() -> None:
    """No residual above the baseline gives zero; no samples gives None."""
    distance = np.arange(0.0, 4.0, 0.05)
    assert instrument_tuning.wing_radius(
        distance, np.zeros(distance.size), 0.0, 0.25, 4.0, 0.01
    ) == pytest.approx(0.0)
    assert instrument_tuning.wing_radius(np.array([9.0]), np.array([0.0]), 0.0, 0.25, 4.0, 0.01) is None


def test_median_relative_noise_reads_the_range_only() -> None:
    """Only samples inside the range count."""
    wavelength = np.array([4000.0, 6000.0, 7000.0])
    intensity = np.array([10.0, 10.0, 10.0])
    error = np.array([5.0, 1.0, 1.0])
    assert instrument_tuning.median_relative_noise(
        wavelength, intensity, error, 5500.0, 8000.0
    ) == pytest.approx(0.1)
    assert instrument_tuning.median_relative_noise(wavelength, intensity, error, 9000.0, 9500.0) is None


def test_spread_limits() -> None:
    """The limits sit the stated number of robust deviations out."""
    values = np.array([-1.0, 0.0, 1.0, 2.0, 3.0])
    assert robust_sigma(values) == pytest.approx(1.4826 * 1.0)
    assert upper_limit_from_spread(values, 2.0) == pytest.approx(1.0 + 2.0 * 1.4826)
    assert symmetric_limit_from_spread(-values, 2.0) == pytest.approx(1.0 + 2.0 * 1.4826)


def test_reference_label_takes_the_longest_match_on_the_ladder() -> None:
    """A giant type finds the giant label, not the supergiant one."""
    labels = ("A0I", "A0III", "A0V")
    assert reference_label("A0III", labels) == "A0III"
    assert reference_label("A0Vn", labels) == "A0V"
    assert reference_label("C5", labels) is None


@pytest.mark.parametrize("tuner", ALL_TUNERS, ids=lambda tuner: tuner.name)
def test_every_tuner_skips_without_stars(tuner: Tuner) -> None:
    """With no stored spectra a tuner raises `TuningSkippedError`."""
    with pytest.raises(TuningSkippedError):
        tuner.tune(_inputs("SPEC"))


@pytest.mark.parametrize("tuner", ALL_TUNERS, ids=lambda tuner: tuner.name)
def test_every_tuner_skips_a_filter_that_is_not_the_grating(tuner: Tuner) -> None:
    """A luminance filter is not tuned by the spectroscopy tuners."""
    with pytest.raises(TuningSkippedError):
        tuner.tune(_inputs("L"))


def test_require_spectral_filter_accepts_the_grating_names() -> None:
    """The short and the catalog name of the grating both pass."""
    require_spectral_filter(_inputs("SPEC"))
    require_spectral_filter(_inputs("Star Analyzer 200"))


def test_no_tuned_parameter_of_these_tuners_is_pending() -> None:
    """Every parameter a tuner sets has its range and sample size."""
    for tuner in ALL_TUNERS:
        for parameter in tuner.parameters:
            assert not parameter.pending_conversion, parameter.name
            assert parameter.allowed_range is not None
            assert parameter.minimum_sample is not None
