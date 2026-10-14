"""Tests for the emission-line detector validation.

Artificial spectra with a known answer check the harness: a clean, quiet
spectrum gives no false line, a strong injected line is found, an injected
line far below the noise is not, spectra with gaps (NaN) are handled, and the
spectra of stars that may truly emit are left out of the false-line count.
"""

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from astrometricslib.scripts import validate_emission_line_detector as validation


def quiet_spectrum(noise: float = 0.01, seed: int = 0, gap: bool = False) -> validation.StoredSpectrum:
    """Build a smooth spectrum with a little white noise.

    Parameters
    ----------
    noise : `float`
        The noise as a fraction of the continuum.
    seed : `int`
        Seed for the noise.
    gap : `bool`
        Mark the first 20 samples NaN, as an off-image trail is stored.

    Returns
    -------
    spectrum : `validation.StoredSpectrum`
        A spectrum from 3800 to 8100 Angstroms.
    """
    wavelength = np.linspace(3800.0, 8100.0, 400)
    intensity = 1.0 + 0.3 * (wavelength - 3800.0) / 4300.0
    intensity = intensity + np.random.default_rng(seed).normal(0.0, noise, wavelength.size)
    if gap:
        intensity[:20] = np.nan
    return validation.StoredSpectrum("star", wavelength, intensity, resolution=40.0, half_width=100.0)


def test_a_quiet_spectrum_has_no_false_line() -> None:
    """Smooth light with a little noise gives no detected line."""
    summary = validation.false_line_summary([quiet_spectrum(seed=seed) for seed in range(5)])

    assert summary["with_a_detected_line"] == 0
    assert summary["called_emission_source"] == 0


def test_a_strong_injected_line_is_found_and_a_buried_one_is_not() -> None:
    """A line as tall as the continuum is found; one at 1% is lost."""
    spectrum = quiet_spectrum()

    strong = validation.recovery_table([spectrum], strengths=(1.0,), groups=("H-alpha + [N II]",))
    weak = validation.recovery_table(
        [quiet_spectrum(noise=0.05)], strengths=(0.01,), groups=("H-alpha + [N II]",)
    )

    assert strong["H-alpha + [N II]", 1.0] == (1, 1)
    assert weak["H-alpha + [N II]", 0.01] == (0, 1)


def test_an_injected_line_is_a_box_centred_on_the_line_and_as_tall_as_asked() -> None:
    """The added light peaks at the requested share of the median."""
    spectrum = quiet_spectrum(noise=0.0)

    injected = validation.inject_line(spectrum, "H-alpha + [N II]", 0.5)

    added = injected - spectrum.intensity
    assert added.max() == pytest.approx(0.5 * np.nanmedian(spectrum.intensity), rel=0.02)
    assert spectrum.wavelength[np.argmax(added)] == pytest.approx(6573.0, abs=150.0)
    assert added[0] == pytest.approx(0.0, abs=1e-6)


def test_a_line_outside_the_covered_wavelengths_is_not_injected() -> None:
    """A spectrum that stops before the line cannot receive it."""
    spectrum = validation.StoredSpectrum(
        "short", np.linspace(3800.0, 5200.0, 200), np.ones(200), resolution=40.0, half_width=100.0
    )

    assert validation.inject_line(spectrum, "H-alpha + [N II]", 1.0) is None


def test_gaps_in_a_stored_spectrum_do_not_break_the_injection() -> None:
    """NaN samples (an off-image trail) are tolerated, not propagated."""
    spectrum = quiet_spectrum(gap=True)

    injected = validation.inject_line(spectrum, "[O III] 4959+5007", 1.0)

    assert injected is not None
    assert np.isfinite(np.nanmax(injected))
    assert np.isfinite(injected[100:]).all()


def test_spectra_of_stars_that_may_emit_are_left_out_of_the_null(tmp_path: Path) -> None:
    """A Be star and a gas target are excluded; an ordinary star stays."""
    connection = sqlite3.connect(tmp_path / "catalog.db")
    connection.execute("CREATE TABLE stellar_objects (id TEXT, data_json TEXT, has_spectra INTEGER)")

    def add(star_id: str, object_types: str = "", stellar_type: str = "") -> None:
        spectrum = quiet_spectrum()
        document = {
            "spectroscopy": {
                "wavelengthsAngstrom": spectrum.wavelength.tolist(),
                "intensities": spectrum.intensity.tolist(),
                "rectangle": [0, 0, 600, 21],
            },
            "simbadObjectTypes": object_types,
            "stellarSpectralType": stellar_type,
        }
        connection.execute("INSERT INTO stellar_objects VALUES (?, ?, 1)", (star_id, json.dumps(document)))

    add("Ordinary", "*|IR")
    add("BeStar", "*|Be*|V*")
    add("M_57_Cluster", stellar_type="Cluster")
    add("Copy::spectroscopy")
    connection.commit()

    kept = [spectrum.star_id for spectrum in validation.load_spectra(connection)]
    connection.close()

    assert kept == ["Ordinary"]
