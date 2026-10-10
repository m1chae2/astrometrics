"""Purpose: Unit tests for the airmass extinction correction.

Description: The instrument response is fitted to a standard star at one
airmass, so it also removes the air's dimming at that airmass. A target
observed at another airmass keeps a leftover blue-red tilt. These tests
check the stored extinction curve, the size and sign of the correction, that
a flat spectrum observed at a higher airmass comes back flat, that a skipped
correction is recorded with its reason, that the stored response records its
airmass, and that the pipeline reads the airmass from the frame header.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.spectroscopy import pipeline as pipeline_module
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
from astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_extinction import (
    EXTINCTION_CURVE_FILE,
    REASON_INVALID_AIRMASS,
    REASON_NO_REFERENCE_AIRMASS,
    REASON_NO_TARGET_AIRMASS,
    apply_extinction_correction,
    extinction_correction_factor,
    load_extinction_curve,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response import load_instrument_response
from astrometricslib.pipelines.spectroscopy.pre_processing.quantum_efficiency_correction import (
    curve_from_profile_record,
    interpolate_quantum_efficiency,
)
from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import _get_reference_templates
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig

CAMERA_NAME = "ZWO ASI 533MM Pro"
TARGET_AIRMASS = 1.5
REFERENCE_AIRMASS = 1.15
WAVELENGTH_ANGSTROM = np.arange(4200.0, 8000.0 + 1.0, 10.0)
FLATNESS_TOLERANCE = 0.01


def _observed_at(
    flux: np.ndarray, wavelength: np.ndarray, airmass: float, reference_airmass: float
) -> np.ndarray:
    """Dim a spectrum the way the air does, relative to the reference airmass.

    Parameters
    ----------
    flux : `numpy.ndarray`
        The spectrum above the atmosphere (or at the reference airmass), in
        any units.
    wavelength : `numpy.ndarray`
        Wavelengths, in Angstroms.
    airmass : `float`
        The airmass of the observation.
    reference_airmass : `float`
        The airmass that the instrument response already removes.

    Returns
    -------
    observed : `numpy.ndarray`
        The flux after it passes `airmass - reference_airmass` more airmasses.
    """
    k = load_extinction_curve().value_at(wavelength)
    return flux * 10.0 ** (-0.4 * k * (airmass - reference_airmass))


def test_the_stored_curve_covers_the_response_range_and_falls_toward_the_red() -> None:
    """The table spans 4200-8000 A and falls toward the red."""
    curve = load_extinction_curve()

    assert curve.wavelength_angstrom.min() <= 4200.0
    assert curve.wavelength_angstrom.max() >= 8000.0
    assert np.all(np.diff(curve.wavelength_angstrom) > 0)
    k = curve.value_at(np.array([4200.0, 5500.0, 8000.0]))
    assert k[0] > k[1] > k[2] > 0
    assert EXTINCTION_CURVE_FILE.name == "atmospheric_extinction_kpno.txt"


def test_a_change_of_0_35_airmass_tilts_the_spectrum_by_about_a_tenth_of_a_magnitude() -> None:
    """0.35 airmass tilts 4200-8000 A by about 0.09 mag."""
    factor = extinction_correction_factor(np.array([4200.0, 8000.0]), TARGET_AIRMASS, REFERENCE_AIRMASS)

    tilt_mag = 2.5 * np.log10(factor[0] / factor[1])
    assert 0.08 < tilt_mag < 0.11


def test_equal_airmasses_change_nothing() -> None:
    """With the target at the reference airmass the factor is exactly 1."""
    factor = extinction_correction_factor(WAVELENGTH_ANGSTROM, 1.3, 1.3)

    np.testing.assert_array_equal(factor, 1.0)


def test_a_higher_target_airmass_brightens_the_blue_more() -> None:
    """The correction puts back more light in the blue than in the red."""
    factor = extinction_correction_factor(WAVELENGTH_ANGSTROM, TARGET_AIRMASS, REFERENCE_AIRMASS)

    assert np.all(factor > 1.0)
    assert factor[0] > factor[-1]
    # Going to a lower airmass than the reference does the reverse.
    lower = extinction_correction_factor(WAVELENGTH_ANGSTROM, 1.05, REFERENCE_AIRMASS)
    assert np.all(lower < 1.0)


def test_a_flat_spectrum_at_airmass_1_5_is_flat_again_after_the_correction() -> None:
    """A flat spectrum seen at airmass 1.5 is flat to 1 percent again."""
    flat = np.full(WAVELENGTH_ANGSTROM.size, 1000.0)
    observed = _observed_at(flat, WAVELENGTH_ANGSTROM, TARGET_AIRMASS, REFERENCE_AIRMASS)
    assert observed[0] / observed[-1] < 0.93  # the tilt is there before the correction

    corrected, record = apply_extinction_correction(
        WAVELENGTH_ANGSTROM, observed, TARGET_AIRMASS, REFERENCE_AIRMASS
    )

    assert np.abs(corrected / corrected.mean() - 1.0).max() < FLATNESS_TOLERANCE
    assert record.is_applied
    assert record.target_airmass == TARGET_AIRMASS
    assert record.reference_airmass == REFERENCE_AIRMASS
    assert record.reason is None


def test_nan_samples_stay_nan() -> None:
    """A sample marked NaN (outside the response range) is not made finite."""
    flux = np.full(WAVELENGTH_ANGSTROM.size, 1.0)
    flux[:5] = np.nan

    corrected, _ = apply_extinction_correction(WAVELENGTH_ANGSTROM, flux, TARGET_AIRMASS, REFERENCE_AIRMASS)

    assert np.isnan(corrected[:5]).all()
    assert np.isfinite(corrected[5:]).all()


@pytest.mark.parametrize(
    ("target", "reference", "reason"),
    [
        (None, REFERENCE_AIRMASS, REASON_NO_TARGET_AIRMASS),
        (TARGET_AIRMASS, None, REASON_NO_REFERENCE_AIRMASS),
        (0.5, REFERENCE_AIRMASS, REASON_INVALID_AIRMASS),
        (float("nan"), REFERENCE_AIRMASS, REASON_INVALID_AIRMASS),
        (25.0, REFERENCE_AIRMASS, REASON_INVALID_AIRMASS),
    ],
)
def test_a_correction_without_usable_airmasses_is_skipped_and_says_why(
    target: float | None, reference: float | None, reason: str
) -> None:
    """The spectrum comes back unchanged and the record names the reason."""
    flux = np.linspace(1.0, 2.0, WAVELENGTH_ANGSTROM.size)

    corrected, record = apply_extinction_correction(WAVELENGTH_ANGSTROM, flux, target, reference)

    np.testing.assert_array_equal(corrected, flux)
    assert corrected is not flux
    assert not record.is_applied
    assert record.reason == reason
    assert record.as_dict()["is_applied"] is False


def test_the_stored_response_records_the_airmass_of_its_standard_star() -> None:
    """The stored ASI533 response records a reference airmass of 1.15."""
    response = load_instrument_response(CAMERA_NAME)

    assert response is not None
    assert response.reference_airmass == pytest.approx(REFERENCE_AIRMASS)


def _build_pipeline() -> SpectroscopyPipeline:
    """Build a `SpectroscopyPipeline` for the ASI533.

    Returns
    -------
    pipeline : `SpectroscopyPipeline`
        The constructed pipeline.
    """
    camera = CameraConfig(
        name=CAMERA_NAME,
        pixel_size_um=3.76,
        sensor_width_px=900,
        sensor_height_px=900,
        sensor_min_wavelength=300.0,
        sensor_max_wavelength=1000.0,
    )
    return SpectroscopyPipeline(config=SpectroscopyConfig(camera=camera, grating_distance_mm=16.5))


def _run_pipeline_on_a_star_seen_at(
    airmass: float | None, monkeypatch: pytest.MonkeyPatch
) -> tuple[np.ndarray, np.ndarray, dict[str, object] | None]:
    """Run the pipeline on a G0V star observed at a given airmass.

    Parameters
    ----------
    airmass : `float` or `None`
        The ``AIRMASS`` card of the frame header; `None` leaves it out.
    monkeypatch : `pytest.MonkeyPatch`
        Used to capture the extinction record the pipeline passes on.

    Returns
    -------
    reference_flux, corrected_flux : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        The G0V reference flux the star was built from, and the pipeline's
        response-corrected flux, on the same wavelengths.
    record : `dict` or `None`
        The extinction record handed to the analysis.
    """
    wavelength, flux = _get_reference_templates()["G0V"]
    curve = curve_from_profile_record(resolve_camera_profile(CAMERA_NAME).quantum_efficiency)
    response = load_instrument_response(CAMERA_NAME)
    seen_at = airmass if airmass is not None else REFERENCE_AIRMASS
    counts = (
        _observed_at(flux, wavelength, seen_at, REFERENCE_AIRMASS)
        * interpolate_quantum_efficiency(wavelength / 10.0, curve)
        * response.value_at(wavelength)
    )
    captured: dict[str, object] = {}
    real_analyze = pipeline_module.analyze_spectrum

    def spy(*args: object, **kwargs: object) -> object:
        """Call the real analysis and keep the extinction record it was given.

        Returns
        -------
        analysis : `SpectrumAnalysis`
            The real analysis result.
        """
        analysis = real_analyze(*args, **kwargs)
        captured["record"] = analysis.extinction_correction
        return analysis

    monkeypatch.setattr(pipeline_module, "analyze_spectrum", spy)
    header = {} if airmass is None else {"AIRMASS": airmass}
    star = StellarObject(id="TestStar")
    result = {
        "detected_angle": 0.0,
        "wavelengths": (wavelength / 10.0).tolist(),
        "intensities": counts.tolist(),
        "target_pos": (100.0, 200.0),
        "trail_centerline_px": None,
        "trail_width_px": None,
    }
    _build_pipeline()._apply_result_to_stellar_object(star, result, SimpleNamespace(header=header))
    return flux, np.array(star.spectroscopy.response_corrected_intensities, dtype=float), captured["record"]


def test_the_pipeline_reads_the_header_airmass_and_flattens_the_tilt(monkeypatch: pytest.MonkeyPatch) -> None:
    """A G0V star seen at airmass 1.5 comes out as its reference spectrum."""
    flux, corrected, record = _run_pipeline_on_a_star_seen_at(TARGET_AIRMASS, monkeypatch)

    wavelength = _get_reference_templates()["G0V"][0]
    inside = np.isfinite(corrected) & (wavelength >= 4200.0) & (wavelength <= 8000.0)
    ratio = corrected[inside] / flux[inside]
    np.testing.assert_allclose(ratio, ratio[0], rtol=1e-6)
    assert record is not None
    assert record["is_applied"] is True
    assert record["target_airmass"] == TARGET_AIRMASS
    assert record["reference_airmass"] == pytest.approx(REFERENCE_AIRMASS)


def test_the_pipeline_without_a_header_airmass_skips_the_correction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an ``AIRMASS`` card nothing is scaled and the record says so."""
    _, _, record = _run_pipeline_on_a_star_seen_at(None, monkeypatch)

    assert record is not None
    assert record["is_applied"] is False
    assert record["reason"] == REASON_NO_TARGET_AIRMASS
