"""Tests for the emission-line detector used on glowing-gas targets."""

import numpy as np
import pytest

from astrometricslib.pipelines.spectroscopy.emission_line_detector import (
    MAXIMUM_FITTED_WAVELENGTH_ANGSTROM,
    VERDICT_DETECTED,
    VERDICT_NOT_COVERED,
    _box_profile,
    detect_emission_lines,
    is_emission_line_source,
    line_half_width_angstrom,
)
from astrometricslib.pipelines.spectroscopy.spectrum_analysis import analyze_spectrum

HALF_WIDTH = 300.0
WAVELENGTHS = np.arange(4200.0, 9500.0, 11.0)


def _nebula_spectrum(noise: float, seed: int, oiii: float = 40.0, halpha: float = 25.0) -> np.ndarray:
    """Build a faint continuum plus [O III] and H-alpha boxes with noise.

    Returns
    -------
    spectrum : `numpy.ndarray`
        The synthetic brightness on `WAVELENGTHS`.
    """
    generator = np.random.default_rng(seed)
    continuum = 10.0 + 0.0005 * (WAVELENGTHS - 4200.0)
    spectrum = continuum + oiii * _box_profile(WAVELENGTHS, 4990.0, HALF_WIDTH, 15.0)
    spectrum += halpha * _box_profile(WAVELENGTHS, 6570.0, HALF_WIDTH, 15.0)
    return spectrum + generator.normal(0.0, noise, WAVELENGTHS.size)


def _by_member(lines: list[dict[str, object]], member: str) -> dict[str, object]:
    """Find the entry that contains a named line.

    Returns
    -------
    entry : `dict`
        The result entry whose members include the name.
    """
    return next(entry for entry in lines if member in entry["members"])  # type: ignore[operator]


def test_finds_the_two_humps_of_a_nebula() -> None:
    """A nebula-like spectrum gives detected [O III] and H-alpha blends."""
    lines = detect_emission_lines(WAVELENGTHS, _nebula_spectrum(1.0, 1), HALF_WIDTH)
    assert _by_member(lines, "[O III] 4959+5007")["verdict"] == VERDICT_DETECTED
    assert _by_member(lines, "H-alpha + [N II]")["verdict"] == VERDICT_DETECTED
    assert is_emission_line_source(lines)


def test_overlapping_boxes_are_reported_as_a_blend() -> None:
    """H-beta and [O III] are too alike at this width and are joined."""
    lines = detect_emission_lines(WAVELENGTHS, _nebula_spectrum(1.0, 2), HALF_WIDTH)
    blend = _by_member(lines, "[O III] 4959+5007")
    assert blend["is_blend"]
    assert "H-beta" in blend["members"]  # type: ignore[operator]


def test_amplitude_is_recovered() -> None:
    """The blend amplitude is close to what was put in."""
    lines = detect_emission_lines(WAVELENGTHS, _nebula_spectrum(0.5, 3, oiii=40.0), HALF_WIDTH)
    blend = _by_member(lines, "[O III] 4959+5007")
    assert abs(float(blend["amplitude"]) - 40.0) < 6.0  # type: ignore[arg-type]


def test_noise_only_spectrum_finds_nothing() -> None:
    """A spectrum with no lines is never called an emission source."""
    false_alarms = 0
    for seed in range(40):
        generator = np.random.default_rng(seed)
        spectrum = 10.0 + generator.normal(0.0, 1.0, WAVELENGTHS.size)
        lines = detect_emission_lines(WAVELENGTHS, spectrum, HALF_WIDTH)
        false_alarms += sum(1 for entry in lines if entry["verdict"] == VERDICT_DETECTED)
    assert false_alarms == 0


def test_smooth_star_with_dips_is_not_an_emission_source() -> None:
    """A cooling continuum with absorption dips has no detected humps."""
    spectrum = 100.0 * np.exp(-((WAVELENGTHS - 4500.0) / 3500.0)) + 20.0
    for center in (4861.0, 6563.0):
        spectrum -= 8.0 * np.exp(-0.5 * ((WAVELENGTHS - center) / 15.0) ** 2)
    spectrum += np.random.default_rng(5).normal(0.0, 0.5, WAVELENGTHS.size)
    assert not is_emission_line_source(detect_emission_lines(WAVELENGTHS, spectrum, HALF_WIDTH))


def test_light_above_the_limit_is_ignored() -> None:
    """A hump beyond 8000 A changes nothing, since that range is not fitted."""
    base = _nebula_spectrum(1.0, 6)
    boosted = base + 60.0 * _box_profile(WAVELENGTHS, 9000.0, HALF_WIDTH, 15.0)
    first = detect_emission_lines(WAVELENGTHS, base, HALF_WIDTH)
    second = detect_emission_lines(WAVELENGTHS, boosted, HALF_WIDTH)
    assert first == second
    assert MAXIMUM_FITTED_WAVELENGTH_ANGSTROM == pytest.approx(8000.0)


def test_lines_outside_the_spectrum_are_not_covered() -> None:
    """A blue-only spectrum reports the red lines as not covered."""
    blue = WAVELENGTHS < 5200.0
    lines = detect_emission_lines(WAVELENGTHS[blue], _nebula_spectrum(1.0, 7)[blue], HALF_WIDTH)
    assert _by_member(lines, "[S II] 6717+6731")["verdict"] == VERDICT_NOT_COVERED


def test_short_spectrum_returns_nothing() -> None:
    """Too few samples means no fit is attempted."""
    assert detect_emission_lines(WAVELENGTHS[:10], np.ones(10), HALF_WIDTH) == []


def test_second_order_ghost_is_listed() -> None:
    """Each entry says where its second-order copy would land."""
    lines = detect_emission_lines(WAVELENGTHS, _nebula_spectrum(1.0, 8), HALF_WIDTH)
    ghost = float(_by_member(lines, "He II 4686")["second_order_ghost_angstrom"])  # type: ignore[arg-type]
    assert ghost == pytest.approx(2 * 4686.0)


def test_half_width_comes_from_box_width_and_sampling() -> None:
    """The half-width is half the box width times Angstroms per pixel."""
    assert line_half_width_angstrom(WAVELENGTHS, 37.0) == pytest.approx(0.5 * 37.0 * 11.0)


def test_analysis_replaces_classification_for_glowing_extended_target() -> None:
    """A "Cluster" target with emission lines gets no stellar type."""
    analysis = analyze_spectrum(
        WAVELENGTHS,
        _nebula_spectrum(0.3, 9),
        None,
        is_quantum_efficiency_corrected=False,
        catalog_spectral_type="PN",
        is_extended_target=True,
        extraction_box_width_px=2.0 * HALF_WIDTH / 11.0,
    )
    assert analysis.is_emission_line_source
    assert analysis.classification["spectral_type"] == "Unknown"
    assert "emission" in str(analysis.classification["reason"])
    assert analysis.features == []


def test_analysis_gives_no_stellar_type_to_an_extended_target_without_confirmed_lines() -> None:
    """A featureless "Cluster" spectrum (M 27, M 13) gets no stellar type."""
    rng = np.random.default_rng(11)
    analysis = analyze_spectrum(
        WAVELENGTHS,
        1.0 + 0.05 * rng.normal(size=WAVELENGTHS.size),
        None,
        is_quantum_efficiency_corrected=False,
        catalog_spectral_type="GlC",
        is_extended_target=True,
        extraction_box_width_px=2.0 * HALF_WIDTH / 11.0,
    )
    assert not analysis.is_emission_line_source
    assert analysis.classification["spectral_type"] == "Unknown"
    assert "extended object" in str(analysis.classification["reason"])
    assert analysis.features == []


def test_analysis_never_replaces_classification_of_an_ordinary_star() -> None:
    """The same spectrum on a typed star is flagged but keeps its verdicts."""
    analysis = analyze_spectrum(
        WAVELENGTHS,
        _nebula_spectrum(0.3, 9),
        None,
        is_quantum_efficiency_corrected=False,
        catalog_spectral_type="G2V",
        extraction_box_width_px=2.0 * HALF_WIDTH / 11.0,
    )
    assert analysis.is_emission_line_source
    assert analysis.features != []
    assert "glowing gas" not in str(analysis.classification["reason"])
