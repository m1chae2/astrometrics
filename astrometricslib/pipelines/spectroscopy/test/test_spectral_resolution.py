"""Purpose: Unit tests for the shared spectral resolution element.

Description: The classifier, the instrument-response fit and the feature
detector all blur reference spectra to "the instrument's resolution", and
that number is now estimated from each spectrum's own trail width. These
tests check the estimate, check its fallbacks, and check that the three
consumers really do share one default instead of each keeping a private
copy that could drift apart.
"""

import inspect
from types import SimpleNamespace

import numpy as np
import pytest

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.spectroscopy.instrument_response import (
    derive_instrument_response,
    load_instrument_response,
)
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
from astrometricslib.pipelines.spectroscopy.spectral_classifier import (
    _get_blurred_templates,
    _get_reference_templates,
    classify_spectral_type,
    unclassified_result,
)
from astrometricslib.pipelines.spectroscopy.spectral_feature_detector import (
    detect_named_features,
    expected_feature_depth,
)
from astrometricslib.pipelines.spectroscopy.spectral_resolution import (
    FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
    FWHM_PER_SIGMA,
    MINIMUM_FITTED_TRAIL_WIDTH_SAMPLES,
    MINIMUM_RESOLUTION_ELEMENT_PIXELS,
    blur_sigma_in_samples,
    estimate_resolution_element_angstrom,
    resolve_resolution_element_angstrom,
)
from astrometricslib.pipelines.spectroscopy.spectrum_analysis import analyze_spectrum
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig

ANGSTROM_PER_PIXEL = 11.0
SAMPLE_COUNT = 400


def _trail(sigma_px: float) -> tuple[np.ndarray, np.ndarray]:
    """Build wavelengths and a constant trail width for a synthetic spectrum.

    Returns
    -------
    wavelength_angstrom, trail_width_px : `tuple` [`np.ndarray`, `np.ndarray`]
        Wavelengths one pixel apart, and the same sigma at every step.
    """
    wavelength_angstrom = 4000.0 + ANGSTROM_PER_PIXEL * np.arange(SAMPLE_COUNT)
    return wavelength_angstrom, np.full(SAMPLE_COUNT, sigma_px)


def test_estimate_is_the_trail_fwhm_times_angstroms_per_pixel() -> None:
    """A 1.7 px sigma at 11 A per pixel is 2.355 * 1.7 * 11 Angstroms."""
    wavelength_angstrom, widths = _trail(1.7)

    estimate = estimate_resolution_element_angstrom(wavelength_angstrom, widths)

    assert estimate == pytest.approx(FWHM_PER_SIGMA * 1.7 * ANGSTROM_PER_PIXEL)


def test_failed_fits_marked_zero_do_not_drag_the_estimate_down() -> None:
    """A 0.0 width means a failed fit, so it is left out of the median."""
    wavelength_angstrom, widths = _trail(1.7)
    widths[::3] = 0.0  # a third of the fits failed

    estimate = estimate_resolution_element_angstrom(wavelength_angstrom, widths)

    assert estimate == pytest.approx(FWHM_PER_SIGMA * 1.7 * ANGSTROM_PER_PIXEL)


def test_a_few_wild_fits_at_the_ends_do_not_decide_the_estimate() -> None:
    """The wide, noisy fits at the ends of a trail must not move the median."""
    wavelength_angstrom, widths = _trail(1.7)
    widths[:20] = 9.0
    widths[-20:] = 9.0

    estimate = estimate_resolution_element_angstrom(wavelength_angstrom, widths)

    assert estimate == pytest.approx(FWHM_PER_SIGMA * 1.7 * ANGSTROM_PER_PIXEL)


def test_the_estimate_is_never_below_the_sampling_limit() -> None:
    """A very narrow fit cannot claim better than two pixels."""
    wavelength_angstrom, widths = _trail(0.6)  # 0.6 * 2.355 = 1.4 px, under the limit

    estimate = estimate_resolution_element_angstrom(wavelength_angstrom, widths)

    assert estimate == pytest.approx(MINIMUM_RESOLUTION_ELEMENT_PIXELS * ANGSTROM_PER_PIXEL)


def test_the_estimate_follows_the_wavelength_spacing() -> None:
    """The same trail on a coarser dispersion is blurrier in Angstroms."""
    wavelength_angstrom, widths = _trail(1.7)

    fine = estimate_resolution_element_angstrom(wavelength_angstrom, widths)
    coarse = estimate_resolution_element_angstrom(4000.0 + 2 * (wavelength_angstrom - 4000.0), widths)

    assert coarse == pytest.approx(2 * fine)


def test_estimate_is_none_when_there_is_nothing_to_measure_from() -> None:
    """Missing, mismatched or too few widths all give `None`."""
    wavelength_angstrom, widths = _trail(1.7)

    assert estimate_resolution_element_angstrom(wavelength_angstrom, None) is None
    assert estimate_resolution_element_angstrom(wavelength_angstrom, widths[:-1]) is None
    assert estimate_resolution_element_angstrom(wavelength_angstrom, np.zeros(SAMPLE_COUNT)) is None
    too_few = np.zeros(SAMPLE_COUNT)
    too_few[: MINIMUM_FITTED_TRAIL_WIDTH_SAMPLES - 1] = 1.7
    assert estimate_resolution_element_angstrom(wavelength_angstrom, too_few) is None


def test_estimate_is_none_when_most_of_the_trail_failed_to_fit() -> None:
    """When most fits failed, the survivors are not a fair sample."""
    wavelength_angstrom, widths = _trail(1.7)
    widths[: int(0.6 * SAMPLE_COUNT)] = 0.0

    assert estimate_resolution_element_angstrom(wavelength_angstrom, widths) is None


def test_estimate_ignores_non_finite_widths() -> None:
    """A NaN width is a failed fit, not a number."""
    wavelength_angstrom, widths = _trail(1.7)
    widths[::4] = np.nan

    estimate = estimate_resolution_element_angstrom(wavelength_angstrom, widths)

    assert estimate == pytest.approx(FWHM_PER_SIGMA * 1.7 * ANGSTROM_PER_PIXEL)


def test_resolve_falls_back_and_says_so() -> None:
    """With no widths the fallback is used and flagged as not measured."""
    wavelength_angstrom, widths = _trail(1.7)

    fallback_value, fallback_measured = resolve_resolution_element_angstrom(wavelength_angstrom, None)
    measured_value, is_measured = resolve_resolution_element_angstrom(wavelength_angstrom, widths)

    assert fallback_value == pytest.approx(FALLBACK_RESOLUTION_ELEMENT_ANGSTROM)
    assert fallback_measured is False
    assert measured_value == pytest.approx(FWHM_PER_SIGMA * 1.7 * ANGSTROM_PER_PIXEL)
    assert is_measured is True


def test_blur_sigma_in_samples_converts_a_fwhm_to_a_sigma_in_samples() -> None:
    """45 A wide, on samples 5 A apart, is 45 / 2.355 / 5 samples of sigma."""
    assert blur_sigma_in_samples(45.0, 5.0) == pytest.approx(45.0 / FWHM_PER_SIGMA / 5.0)


def test_every_consumer_shares_the_one_default_resolution() -> None:
    """No module may keep its own private copy of the resolution default."""
    for function in (
        classify_spectral_type,
        detect_named_features,
        expected_feature_depth,
        derive_instrument_response,
    ):
        default = inspect.signature(function).parameters["resolution_element_angstrom"].default
        assert default == FALLBACK_RESOLUTION_ELEMENT_ANGSTROM, function.__name__


def test_blurred_reference_cache_is_kept_per_resolution() -> None:
    """A blurrier instrument gets blurrier references; repeats are reused."""
    sharp = _get_blurred_templates(30.0)
    blurry = _get_blurred_templates(90.0)

    assert _get_blurred_templates(30.0) is sharp
    assert _get_blurred_templates(30.4) is sharp  # same whole-Angstrom key
    assert sharp is not blurry
    # A wider blur flattens the Balmer lines, so the spectrum varies less.
    assert np.std(np.diff(blurry["A0V"][1])) < np.std(np.diff(sharp["A0V"][1]))


def test_a_blurrier_instrument_expects_shallower_lines() -> None:
    """The depth a line should show shrinks as the resolution gets coarser."""
    name = "Hydrogen Balmer series (H-beta)"

    sharp = expected_feature_depth("A0V", name, 30.0)
    blurry = expected_feature_depth("A0V", name, 90.0)

    assert sharp is not None
    assert blurry is not None
    assert blurry < sharp


def test_analysis_measures_the_resolution_from_the_trail_width() -> None:
    """`analyze_spectrum` hands each spectrum's own resolution to its tests."""
    wavelength_angstrom, widths = _trail(2.5)
    flux = np.ones(SAMPLE_COUNT)

    measured = analyze_spectrum(wavelength_angstrom, flux, None, True, trail_width_px=widths.tolist())
    fallback = analyze_spectrum(wavelength_angstrom, flux, None, True)

    assert measured.is_resolution_measured is True
    assert measured.resolution_element_angstrom == pytest.approx(FWHM_PER_SIGMA * 2.5 * ANGSTROM_PER_PIXEL)
    assert fallback.is_resolution_measured is False
    assert fallback.resolution_element_angstrom == pytest.approx(FALLBACK_RESOLUTION_ELEMENT_ANGSTROM)


def test_analysis_expects_shallower_lines_for_a_blurrier_spectrum() -> None:
    """The feature detector really receives the measured resolution."""
    wavelength_angstrom, flux = _get_reference_templates()["A0V"]
    keep = (wavelength_angstrom > 3800.0) & (wavelength_angstrom < 8400.0)
    wavelength_angstrom, flux = wavelength_angstrom[keep], flux[keep]
    spacing = float(np.median(np.diff(wavelength_angstrom)))

    def expected_h_beta(sigma_px: float) -> float:
        widths = np.full(wavelength_angstrom.size, sigma_px)
        analysis = analyze_spectrum(
            wavelength_angstrom, flux, None, True, "A0V", trail_width_px=widths.tolist()
        )
        entry = next(feature for feature in analysis.features if "H-beta" in str(feature["feature"]))
        return float(entry["expected_depth"])

    # Trail sigmas that give roughly 30 A and roughly 90 A resolution.
    sharp_sigma = 30.0 / FWHM_PER_SIGMA / spacing
    blurry_sigma = 90.0 / FWHM_PER_SIGMA / spacing

    assert expected_h_beta(blurry_sigma) < expected_h_beta(sharp_sigma)


def test_analysis_gives_the_classifier_the_measured_resolution(monkeypatch) -> None:  # ruff: ignore[missing-type-function-argument]
    """The classifier must be told the spectrum's own resolution."""
    received = {}

    def fake_classify(wavelength, intensity, resolution_element_angstrom):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        received["resolution_element_angstrom"] = resolution_element_angstrom
        return unclassified_result("stand-in classifier")

    monkeypatch.setattr(
        "astrometricslib.pipelines.spectroscopy.spectrum_analysis.classify_spectral_type", fake_classify
    )
    wavelength_angstrom, widths = _trail(2.5)

    analyze_spectrum(
        wavelength_angstrom,
        np.ones(SAMPLE_COUNT),
        load_instrument_response("ZWO ASI 533MM Pro"),
        True,
        trail_width_px=widths.tolist(),
    )

    assert received["resolution_element_angstrom"] == pytest.approx(FWHM_PER_SIGMA * 2.5 * ANGSTROM_PER_PIXEL)


def _build_pipeline() -> SpectroscopyPipeline:
    """Build a pipeline for a test camera with no response on file.

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


def _extraction_result(trail_width_px: list[float] | None) -> dict[str, object]:
    """Build an extraction result like `_process_single_star` returns.

    Returns
    -------
    result : `dict`
        The result, with wavelengths in nanometers 1.1 nm apart.
    """
    wavelength_nm = 400.0 + 1.1 * np.arange(SAMPLE_COUNT)
    return {
        "detected_angle": 0.0,
        "wavelengths": wavelength_nm.tolist(),
        "intensities": np.ones(SAMPLE_COUNT).tolist(),
        "target_pos": (100.0, 200.0),
        "trail_centerline_px": None,
        "trail_width_px": trail_width_px,
    }


def test_pipeline_stores_the_measured_resolution_on_the_star() -> None:
    """The measured resolution is recorded with the spectrum it was used on."""
    pipeline = _build_pipeline()
    star = StellarObject(id="TestStar")

    pipeline._apply_result_to_stellar_object(
        star, _extraction_result([1.7] * SAMPLE_COUNT), SimpleNamespace(timestamp=1700000000.0)
    )

    assert star.spectroscopy.resolution_element_angstrom == pytest.approx(
        FWHM_PER_SIGMA * 1.7 * ANGSTROM_PER_PIXEL
    )


def test_pipeline_records_no_resolution_when_the_fallback_was_used() -> None:
    """`None` on the star means the fixed fallback resolution was used."""
    pipeline = _build_pipeline()
    star = StellarObject(id="TestStar")

    pipeline._apply_result_to_stellar_object(
        star, _extraction_result(None), SimpleNamespace(timestamp=1700000000.0)
    )

    assert star.spectroscopy.resolution_element_angstrom is None
