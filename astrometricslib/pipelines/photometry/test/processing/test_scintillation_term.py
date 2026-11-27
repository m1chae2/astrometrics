"""Purpose: Unit tests for the scintillation term of the flux errors.

Description: Builds sessions of constant stars whose fluxes carry a known
amount of scintillation (Young's prediction times a chosen coefficient) and
checks that the coefficient is recovered, that a session with too few stars
falls back to the documented factor and says why, that the term is added to
every point's error in quadrature, that it is added only once, that the
comparison weights see it, and that the noise model's background term does
not.
"""

import math
from datetime import datetime, timedelta

import numpy as np
import pytest

from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.pipelines.photometry.pre_processing.detector_noise import DetectorNoise
from astrometricslib.pipelines.photometry.processing.noise_budget import (
    MAGNITUDES_PER_FRACTION,
    SessionNoiseInputs,
    remove_scintillation_in_quadrature,
    scintillation_relative_noise,
)
from astrometricslib.pipelines.photometry.processing.scintillation_term import (
    FALLBACK_YOUNG_FACTOR,
    SOURCE_FALLBACK,
    SOURCE_MEASURED,
    SOURCE_MEASURED_LIMITED,
    SOURCE_NONE,
    add_scintillation_in_quadrature,
    build_scintillation_term,
    fit_scintillation_coefficient,
)
from astrometricslib.pipelines.photometry.processing.variability_analyzer import VariabilityAnalyzer

_FRAME_COUNT = 60
_EXPOSURE_SECONDS = 30.0
_APERTURE_MM = 75.0
_ELEVATION_M = 1460.0
_AIRMASS = 1.3

# The CCD-only error of every point, as a fraction of its flux: 0.1 percent,
# well below the scintillation of about 0.4 percent, so the fit can see it.
_CCD_FRACTION = 0.001


def _young_fraction() -> float:
    """Find Young's prediction for the test frames.

    Returns
    -------
    fraction : `float`
        The fractional noise of one frame.
    """
    return scintillation_relative_noise(_APERTURE_MM, _AIRMASS, _EXPOSURE_SECONDS, _ELEVATION_M)


def _build_session(
    star_count: int, true_coefficient: float, seed: int = 7
) -> tuple[list[StellarObject], dict[datetime, float], list[datetime]]:
    """Build constant stars with a known scintillation.

    Parameters
    ----------
    star_count : `int`
        How many stars.
    true_coefficient : `float`
        The multiplier on Young's prediction that the flux scatter carries.
    seed : `int`, optional
        The random seed.

    Returns
    -------
    stars, exposures, timestamps : `tuple`
        The stars, the exposure of each frame and the frame times.
    """
    generator = np.random.default_rng(seed)
    start = datetime(2026, 10, 3, 3, 0, 0)
    timestamps = [start + timedelta(seconds=40 * index) for index in range(_FRAME_COUNT)]
    exposures = dict.fromkeys(timestamps, _EXPOSURE_SECONDS)
    scintillation = true_coefficient * _young_fraction()
    stars = []
    for index in range(star_count):
        base = 50000.0 - 400.0 * index
        noise = generator.normal(0.0, np.hypot(_CCD_FRACTION, scintillation), _FRAME_COUNT)
        fluxes = (base * (1.0 + noise)).tolist()
        star = StellarObject(id=f"Star_{index}")
        star.flux = base
        star.photometry = PhotometryResult(
            timestamps=list(timestamps),
            fluxes=fluxes,
            flux_errors=[_CCD_FRACTION * base] * _FRAME_COUNT,
            is_saturated=[False] * _FRAME_COUNT,
            airmasses=[_AIRMASS] * _FRAME_COUNT,
        )
        stars.append(star)
    return stars, exposures, timestamps


def test_the_fit_recovers_a_known_coefficient() -> None:
    """Scatter of 1.3 times Young's prediction gives a coefficient near 1.3."""
    stars, _exposures, _timestamps = _build_session(40, 1.3)
    fluxes = np.array([star.photometry.fluxes for star in stars])
    errors = np.array([star.photometry.flux_errors for star in stars])
    young = np.full(_FRAME_COUNT, _young_fraction())

    fit = fit_scintillation_coefficient(fluxes, errors, young)

    assert fit is not None
    assert fit.coefficient_squared > 0
    assert math.sqrt(fit.coefficient_squared) == pytest.approx(1.3, rel=0.1)
    assert fit.frame_count == _FRAME_COUNT


def test_a_session_without_scintillation_gives_no_excess() -> None:
    """Scatter as the CCD errors say gives a coefficient near zero."""
    stars, _exposures, _timestamps = _build_session(40, 0.0)
    fluxes = np.array([star.photometry.fluxes for star in stars])
    errors = np.array([star.photometry.flux_errors for star in stars])
    young = np.full(_FRAME_COUNT, _young_fraction())

    fit = fit_scintillation_coefficient(fluxes, errors, young)

    assert fit is not None
    assert math.sqrt(max(fit.coefficient_squared, 0.0)) < 0.35


def test_the_term_is_measured_from_the_comparison_stars() -> None:
    """With enough data the record says measured and holds k times Young."""
    stars, exposures, timestamps = _build_session(60, 1.3)

    term = build_scintillation_term(stars, exposures, _APERTURE_MM, None, _ELEVATION_M)

    assert term.record.source == SOURCE_MEASURED
    assert term.record.reason is None
    assert term.record.coefficient == pytest.approx(1.3, rel=0.15)
    assert term.record.measured_coefficient == pytest.approx(term.record.coefficient)
    expected_mmag = 1000.0 * MAGNITUDES_PER_FRACTION * _young_fraction()
    assert term.record.predicted_young_mmag == pytest.approx(expected_mmag, rel=0.01)
    assert term.record.used_mmag == pytest.approx(term.record.coefficient * expected_mmag, rel=1e-6)
    assert term.record.member_count >= 5
    assert term.fraction_by_timestamp[timestamps[0]] == pytest.approx(
        term.record.coefficient * _young_fraction()
    )


def test_a_coefficient_outside_the_range_is_limited_and_says_so() -> None:
    """A fit far above the range uses the range's top and says why."""
    stars, exposures, _timestamps = _build_session(60, 7.0)

    term = build_scintillation_term(stars, exposures, _APERTURE_MM, None, _ELEVATION_M)

    assert term.record.source == SOURCE_MEASURED_LIMITED
    assert term.record.coefficient == pytest.approx(3.0)
    assert term.record.measured_coefficient is not None and term.record.measured_coefficient > 3.0
    assert "outside" in (term.record.reason or "")


def test_too_few_comparison_stars_fall_back_with_the_reason() -> None:
    """Four stars cannot give five members, so the fallback factor is used."""
    stars, exposures, _timestamps = _build_session(4, 1.3)

    term = build_scintillation_term(stars, exposures, _APERTURE_MM, None, _ELEVATION_M)

    assert term.record.source == SOURCE_FALLBACK
    assert term.record.coefficient == pytest.approx(FALLBACK_YOUNG_FACTOR.default)
    assert term.record.measured_coefficient is None
    assert "needed" in (term.record.reason or "")
    assert term.fraction_by_timestamp


def test_too_few_frames_fall_back_with_the_reason() -> None:
    """A session of ten frames is too short to fit, so the factor is used."""
    stars, exposures, timestamps = _build_session(60, 1.3)
    for star in stars:
        photometry = star.photometry
        photometry.timestamps = photometry.timestamps[:10]
        photometry.fluxes = photometry.fluxes[:10]
        photometry.flux_errors = photometry.flux_errors[:10]
        photometry.is_saturated = photometry.is_saturated[:10]
        photometry.airmasses = photometry.airmasses[:10]

    term = build_scintillation_term(stars, exposures, _APERTURE_MM, None, _ELEVATION_M)

    assert term.record.source == SOURCE_FALLBACK
    assert "frame" in (term.record.reason or "")
    assert timestamps[0] in term.fraction_by_timestamp


def test_an_unknown_aperture_adds_no_term_and_says_why() -> None:
    """Without an aperture Young's prediction cannot be made."""
    stars, exposures, _timestamps = _build_session(60, 1.3)

    term = build_scintillation_term(stars, exposures, None, "the optic is not listed", _ELEVATION_M)

    assert term.record.source == SOURCE_NONE
    assert term.record.coefficient is None
    assert "not listed" in (term.record.reason or "")
    assert term.fraction_by_timestamp == {}


def test_the_term_adds_in_quadrature_to_every_point() -> None:
    """Each error grows by the term, added in quadrature."""
    stars, _exposures, timestamps = _build_session(3, 1.0)
    before = list(stars[0].photometry.flux_errors)
    fractions = dict.fromkeys(timestamps, 0.004)
    fractions.pop(timestamps[1])

    add_scintillation_in_quadrature(stars, fractions)

    after = stars[0].photometry.flux_errors
    flux = stars[0].photometry.fluxes[0]
    assert after[0] == pytest.approx(math.hypot(before[0], 0.004 * flux))
    assert after[1] == before[1]
    assert all(value >= error for value, error in zip(after, before, strict=True))


def test_removing_the_term_gives_back_the_ccd_errors() -> None:
    """Removing the term is the inverse of adding it."""
    errors = np.array([10.0, 20.0, 30.0])
    fluxes = np.array([1000.0, 2000.0, 3000.0])
    fractions = np.array([0.004, math.nan, 0.01])
    combined = np.array([math.hypot(10.0, 4.0), 20.0, math.hypot(30.0, 30.0)])

    recovered = remove_scintillation_in_quadrature(combined, fluxes, fractions)

    assert recovered == pytest.approx(errors)


def _analyzer_for(
    stars: list[StellarObject], exposures: dict[datetime, float], timestamps: list[datetime]
) -> VariabilityAnalyzer:
    """Build an analyzer around prepared stars, as the runner does.

    Returns
    -------
    analyzer : `VariabilityAnalyzer`
        An analyzer that has not normalized yet.
    """
    analyzer = VariabilityAnalyzer()
    analyzer.stellar_objects = stars
    analyzer.frame_exposure_s = dict(exposures)
    analyzer.timestamp_to_path = {stamp: f"frame_{index:03d}.fits" for index, stamp in enumerate(timestamps)}
    analyzer.aperture_diameter_mm = _APERTURE_MM
    analyzer.aperture_reason = None
    analyzer.site_elevation_m = _ELEVATION_M
    return analyzer


def test_normalizing_adds_the_term_once_and_the_ensemble_weights_see_it() -> None:
    """The ensemble error grows with the term, which is added only once."""
    with_term = _build_session(60, 1.3)
    without_term = _build_session(60, 1.3)
    analyzer = _analyzer_for(*with_term)
    plain = _analyzer_for(*without_term)
    plain.aperture_diameter_mm = None

    analyzer.normalize_light_curves()
    plain.normalize_light_curves()
    errors_after_first = list(analyzer.stellar_objects[0].photometry.flux_errors)
    analyzer.normalize_light_curves()

    assert analyzer.scintillation is not None and analyzer.scintillation.source == SOURCE_MEASURED
    assert plain.scintillation is not None and plain.scintillation.source == SOURCE_NONE
    assert analyzer.stellar_objects[0].photometry.flux_errors == errors_after_first
    plain_errors = plain.stellar_objects[0].photometry.flux_errors
    assert all(new > old for new, old in zip(errors_after_first, plain_errors, strict=True))
    stamp = with_term[2][0]
    assert analyzer.frame_reference_flux_error[stamp] > plain.frame_reference_flux_error[stamp]


def test_the_noise_inputs_do_not_count_the_term_as_background() -> None:
    """The background term is the same with and without the term."""
    stars, exposures, timestamps = _build_session(6, 1.0)
    detector = DetectorNoise()
    plain = SessionNoiseInputs.from_analyzer_results("s", stars, exposures, detector)
    fractions = dict.fromkeys(timestamps, 0.004)
    add_scintillation_in_quadrature(stars, fractions)

    uncorrected = SessionNoiseInputs.from_analyzer_results("s", stars, exposures, detector)
    corrected = SessionNoiseInputs.from_analyzer_results("s", stars, exposures, detector, fractions)

    assert uncorrected.background_variance_e2 != pytest.approx(plain.background_variance_e2)
    assert corrected.background_variance_e2 == pytest.approx(plain.background_variance_e2, rel=1e-6)
