"""Tests for the model of a measurement's noise by source.

They check the pieces against hand calculations (Young's formula, the
geometry factor), against the CCD equation the photometry already uses (the
background term must come back exactly from the errors that equation made),
and the way a missing input is reported.
"""

import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from astrometricslib.models.stellar_source import PhotometryResult
from astrometricslib.pipelines.photometry.pre_processing.detector_noise import DetectorNoise
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import aperture_flux_error_adu
from astrometricslib.pipelines.photometry.processing.noise_budget import (
    MAGNITUDES_PER_FRACTION,
    NoiseBudgetInputs,
    SessionNoiseInputs,
    aperture_geometry_factor,
    background_variance_e2,
    magnitude_noise,
    median_header_airmass,
    noise_variances_at_magnitude,
    scintillation_relative_noise,
)

GAIN = 0.783
READ_NOISE = 3.8
CIRCLE_PIXELS = math.pi * 4.0**2


def test_the_geometry_factor_is_the_circle_plus_its_share_of_the_ring() -> None:
    """The factor is n_pix + n_pix**2 / n_sky for a ring of 298 pixels."""
    factor = aperture_geometry_factor()
    ring_pixels = CIRCLE_PIXELS**2 / (factor - CIRCLE_PIXELS)
    assert ring_pixels == pytest.approx(math.pi * (12.0**2 - 7.0**2), rel=0.03)


def test_young_formula_matches_a_hand_calculation() -> None:
    """75 mm at airmass 1.3 for 10 s gives 0.83 percent at sea level."""
    value = scintillation_relative_noise(75.0, 1.3, 10.0)
    assert value == pytest.approx(0.09 * 7.5 ** (-2 / 3) * 1.3**1.75 / math.sqrt(20.0), rel=1e-9)
    assert value == pytest.approx(0.00831, rel=0.01)


def test_the_site_height_lowers_the_scintillation_by_the_scale_height() -> None:
    """At 1,400 m the value is exp(-1400 / 8000) times the sea-level value."""
    sea_level = scintillation_relative_noise(75.0, 1.3, 10.0)
    high = scintillation_relative_noise(75.0, 1.3, 10.0, site_elevation_m=1400.0)
    assert high / sea_level == pytest.approx(math.exp(-1400.0 / 8000.0))


def test_scintillation_falls_with_aperture_and_exposure_and_rises_with_airmass() -> None:
    """The three dependences have the signs Young's formula gives."""
    base = scintillation_relative_noise(75.0, 1.3, 10.0)
    assert scintillation_relative_noise(150.0, 1.3, 10.0) < base
    assert scintillation_relative_noise(75.0, 1.3, 40.0) == pytest.approx(base / 2.0)
    assert scintillation_relative_noise(75.0, 2.0, 10.0) > base


@pytest.mark.parametrize("arguments", [(0.0, 1.3, 10.0), (75.0, 0.0, 10.0), (75.0, 1.3, 0.0)])
def test_scintillation_refuses_a_non_positive_input(arguments: tuple[float, float, float]) -> None:
    """A diameter, airmass or exposure of zero is an error, not a number."""
    with pytest.raises(ValueError, match="above zero"):
        scintillation_relative_noise(*arguments)


def test_the_background_term_comes_back_exactly_from_the_ccd_equation_errors() -> None:
    """Errors made by the CCD equation give back the background term."""
    noise = DetectorNoise(
        gain_e_per_adu=GAIN, read_noise_e=READ_NOISE, gain_is_assumed=False, read_noise_is_assumed=False
    )
    sky_adu_per_pixel = 300.0
    ring_pixels = 298.0
    exposure = 10.0
    net_adu = np.array([1.0e4, 5.0e4, 2.0e3, 0.0])
    errors_adu = np.array([
        aperture_flux_error_adu(flux, sky_adu_per_pixel, CIRCLE_PIXELS, ring_pixels, noise)
        for flux in net_adu
    ])
    background, count = background_variance_e2(
        net_adu / exposure, errors_adu / exposure, np.full(4, exposure), GAIN
    )
    expected = (CIRCLE_PIXELS + CIRCLE_PIXELS**2 / ring_pixels) * (sky_adu_per_pixel * GAIN + READ_NOISE**2)
    assert background == pytest.approx(expected, rel=1e-9)
    assert count == 4


def test_the_background_term_ignores_points_with_no_error() -> None:
    """A point with an error of zero is left out; none left gives None."""
    background, count = background_variance_e2(
        np.array([1.0, 2.0]), np.array([0.0, 0.0]), np.array([10.0, 10.0]), GAIN
    )
    assert background is None
    assert count == 0


def _inputs(**changes: object) -> NoiseBudgetInputs:
    """Build inputs for a 10 s session with a known gain and read noise.

    Returns
    -------
    inputs : `NoiseBudgetInputs`
        Inputs with the given fields replaced.
    """
    geometry = aperture_geometry_factor()
    session = SessionNoiseInputs(
        session_id="s",
        exposure_seconds=10.0,
        gain_e_per_adu=GAIN,
        gain_is_assumed=False,
        read_noise_e=READ_NOISE,
        read_noise_is_assumed=False,
        background_variance_e2=geometry * (200.0 + READ_NOISE**2),
        point_count=1000,
    )
    values = {
        "session": session,
        "zero_point_mag": 21.5,
        "aperture_diameter_mm": 75.0,
        "airmass": 1.3,
        "airmass_frame_count": 5,
        "site_elevation_m": 1400.0,
    }
    values.update(changes)
    return NoiseBudgetInputs(**values)


def test_the_four_sources_of_a_star_add_up_as_the_ccd_equation_says() -> None:
    """Photon, sky and read variances sum to the equation's variance."""
    inputs = _inputs()
    variances = noise_variances_at_magnitude(12.0, inputs)
    assert variances is not None
    flux_adu_per_second = 10.0 ** (0.4 * (21.5 - 12.0))
    star_electrons = flux_adu_per_second * 10.0 * GAIN
    assert variances.star_electrons == pytest.approx(star_electrons)
    assert variances.read_e2 == pytest.approx(READ_NOISE**2 * aperture_geometry_factor())
    assert variances.sky_e2 == pytest.approx(200.0 * aperture_geometry_factor())
    total = variances.photon_e2 + variances.sky_e2 + variances.read_e2
    noise = DetectorNoise(
        gain_e_per_adu=GAIN, read_noise_e=READ_NOISE, gain_is_assumed=False, read_noise_is_assumed=False
    )
    ring_pixels = CIRCLE_PIXELS**2 / (aperture_geometry_factor() - CIRCLE_PIXELS)
    equation = (
        aperture_flux_error_adu(star_electrons / GAIN, 200.0 / GAIN, CIRCLE_PIXELS, ring_pixels, noise) * GAIN
    ) ** 2
    assert total == pytest.approx(equation, rel=1e-9)


def test_a_brighter_star_has_less_noise_in_magnitudes_and_the_floor_stays() -> None:
    """Photon noise falls with brightness; scintillation stays the same."""
    inputs = _inputs()
    bright = noise_variances_at_magnitude(10.0, inputs)
    faint = noise_variances_at_magnitude(14.0, inputs)
    assert bright is not None
    assert faint is not None
    assert magnitude_noise(bright.photon_e2, bright.star_electrons) < magnitude_noise(
        faint.photon_e2, faint.star_electrons
    )
    assert bright.scintillation_relative == faint.scintillation_relative
    assert MAGNITUDES_PER_FRACTION == pytest.approx(1.0857, abs=1e-4)


def test_an_assumed_read_noise_leaves_the_sky_and_read_terms_out() -> None:
    """With the read noise only assumed, the two cannot be told apart."""
    inputs = _inputs()
    assumed = SessionNoiseInputs(**{
        **inputs.session.__dict__,
        "read_noise_is_assumed": True,
        "read_noise_e": 0.0,
    })
    variances = noise_variances_at_magnitude(12.0, _inputs(session=assumed))
    assert variances is not None
    assert variances.sky_e2 is None
    assert variances.read_e2 is None
    assert variances.photon_e2 > 0


def test_a_background_below_the_read_noise_is_reported_as_not_found() -> None:
    """A negative sky term means the inputs disagree; neither is given."""
    inputs = _inputs()
    low = SessionNoiseInputs(**{**inputs.session.__dict__, "background_variance_e2": 1.0})
    variances = noise_variances_at_magnitude(12.0, _inputs(session=low))
    assert variances is not None
    assert variances.sky_e2 is None
    assert variances.read_e2 is None


@pytest.mark.parametrize(
    "changes",
    [{"zero_point_mag": None}, {"session": None}],
)
def test_without_a_zero_point_or_a_session_no_term_is_found(changes: dict) -> None:
    """The terms need a flux for the magnitude, so a zero point."""
    assert noise_variances_at_magnitude(12.0, _inputs(**changes)) is None


def test_scintillation_needs_the_aperture_and_the_airmass() -> None:
    """A missing aperture or airmass drops scintillation only."""
    for changes in ({"aperture_diameter_mm": None}, {"airmass": None}):
        variances = noise_variances_at_magnitude(12.0, _inputs(**changes))
        assert variances is not None
        assert variances.scintillation_relative is None
        assert variances.scintillation_e2 is None
        assert variances.photon_e2 > 0


def test_the_header_airmass_takes_the_median_of_the_frames_that_gave_one() -> None:
    """A frame with no airmass (None) is not counted and no default is used."""
    assert median_header_airmass([1.2, None, 1.4, 1.3]) == (pytest.approx(1.3), 3)
    assert median_header_airmass([None, None]) == (None, 0)
    assert median_header_airmass([0.5, float("nan")]) == (None, 0)


def test_session_inputs_are_gathered_from_light_curves_and_exposures() -> None:
    """Saved fluxes, errors and exposures give the session's background."""
    noise = DetectorNoise(
        gain_e_per_adu=GAIN, read_noise_e=READ_NOISE, gain_is_assumed=False, read_noise_is_assumed=False
    )
    start = datetime(2026, 10, 3, 4, 0, tzinfo=UTC).replace(tzinfo=None)
    stamps = [start + timedelta(seconds=20 * index) for index in range(6)]
    exposures = dict.fromkeys(stamps, 10.0)
    ring_pixels = 298.0
    stars = []
    for net_adu in (2.0e3, 3.0e4):
        errors = [aperture_flux_error_adu(net_adu, 250.0, CIRCLE_PIXELS, ring_pixels, noise) / 10.0] * 6
        stars.append(
            SimpleNamespace(
                photometry=PhotometryResult(
                    timestamps=stamps, fluxes=[net_adu / 10.0] * 6, flux_errors=errors
                )
            )
        )
    stars.append(SimpleNamespace(photometry=None))
    inputs = SessionNoiseInputs.from_analyzer_results("night", stars, exposures, noise)
    expected = (CIRCLE_PIXELS + CIRCLE_PIXELS**2 / ring_pixels) * (250.0 * GAIN + READ_NOISE**2)
    assert inputs.background_variance_e2 == pytest.approx(expected, rel=1e-9)
    assert inputs.point_count == 12
    assert inputs.exposure_seconds == pytest.approx(10.0)
    assert inputs.gain_e_per_adu == GAIN
    assert not inputs.gain_is_assumed


def test_session_inputs_skip_a_star_whose_arrays_disagree_in_length() -> None:
    """A light curve with mismatched errors and times is left out."""
    noise = DetectorNoise(
        gain_e_per_adu=GAIN, read_noise_e=READ_NOISE, gain_is_assumed=False, read_noise_is_assumed=False
    )
    start = datetime(2026, 10, 3, 4, 0)
    stamps = [start + timedelta(seconds=20 * index) for index in range(3)]
    star = SimpleNamespace(
        photometry=PhotometryResult(timestamps=stamps, fluxes=[1.0, 2.0, 3.0], flux_errors=[0.1])
    )
    inputs = SessionNoiseInputs.from_analyzer_results("night", [star], dict.fromkeys(stamps, 10.0), noise)
    assert inputs.background_variance_e2 is None
    assert inputs.point_count == 0
