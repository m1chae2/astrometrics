"""Purpose: Unit tests for the Balmer-line mask in the response fit.

Description: The instrument response is fitted by comparing an observation
of a reference star (Vega) with a bundled reference spectrum. Near the
Balmer lines the two disagree, because the instrument blurs the observed
lines to 100-150 Angstroms and the bundled spectrum is blurred only to one
fixed width. The fit must skip a band around each line wide enough to cover
that mismatch, or the line wings end up in the response and get divided out
of every target. These tests build a synthetic reference with a flat
continuum and Balmer dips, observe it through the stored line-spread profile,
and check that the fitted response is flat.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.spectroscopy.pre_processing import instrument_response
from astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response import (
    InstrumentResponse,
    derive_instrument_response,
    line_skip_half_widths_angstrom,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    ResolutionProfile,
    blur_to_resolution_profile,
    load_line_spread_profile,
)
from astrometricslib.pipelines.spectroscopy.processing import spectral_classifier

CAMERA_NAME = "ZWO ASI 533MM Pro"
# The Balmer lines inside the response's fitting range, in Angstroms.
BALMER_LINES_ANGSTROM = (4340.0, 4861.0, 6563.0)
# The wavelength grid of the bundled reference spectra, in Angstroms.
GRID_ANGSTROM = np.arange(3000.0, 10000.0 + 1.0, 5.0)
# The intrinsic width and depth of each reference line, before the
# instrument blurs it. The width is below the instrument's blur but wider
# than the single width the fit blurs the reference to. The lines are
# stronger than Vega's real ones (equivalent width about 38 Angstroms here
# against about 13), so the wings they leave in an unmasked fit move the
# response by more than the 1 percent the test allows. Vega's own lines move
# it by about half a percent.
INTRINSIC_LINE_FWHM_ANGSTROM = 60.0
LINE_DEPTH = 0.6
# The response must be flat to this fraction across the fitted range.
FLATNESS_TOLERANCE = 0.01
# The wings checked: this many line-spread widths either side of each line.
WING_HALF_WIDTH_PER_FWHM = 2.5


def _synthetic_reference() -> np.ndarray:
    """Build the A0V-like reference: a flat continuum with Balmer dips.

    Returns
    -------
    flux : `numpy.ndarray`
        The reference flux on `GRID_ANGSTROM`.
    """
    sigma = INTRINSIC_LINE_FWHM_ANGSTROM / 2.355
    flux = np.ones_like(GRID_ANGSTROM)
    for line in (4102.0, *BALMER_LINES_ANGSTROM):
        flux -= LINE_DEPTH * np.exp(-0.5 * ((GRID_ANGSTROM - line) / sigma) ** 2)
    return flux


def _fitted_response(monkeypatch: pytest.MonkeyPatch) -> InstrumentResponse:
    """Fit a response to a star observed through the stored line spread.

    The synthetic reference replaces the bundled A0V spectrum. The star is
    that reference blurred by the stored line-spread profile, with no
    instrument tilt, so the true response is flat.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to swap in the synthetic reference.

    Returns
    -------
    response : `InstrumentResponse`
        The fitted response.
    """
    reference = _synthetic_reference()
    monkeypatch.setattr(
        spectral_classifier, "_get_reference_templates", lambda: {"A0V": (GRID_ANGSTROM, reference)}
    )
    profile = load_line_spread_profile(CAMERA_NAME)
    assert profile is not None
    observed = blur_to_resolution_profile(GRID_ANGSTROM, reference, profile)
    return derive_instrument_response(GRID_ANGSTROM, observed, "A0V", CAMERA_NAME, "synthetic Vega")


def test_the_response_is_flat_across_the_balmer_wings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A flat true response fits flat to 1 percent where the line wings are."""
    response = _fitted_response(monkeypatch)
    profile = load_line_spread_profile(CAMERA_NAME)

    values = response.value_at(GRID_ANGSTROM)
    in_range = (GRID_ANGSTROM >= response.minimum_wavelength_angstrom) & (
        GRID_ANGSTROM <= response.maximum_wavelength_angstrom
    )
    near_a_line = np.zeros_like(in_range)
    for line in BALMER_LINES_ANGSTROM:
        near_a_line |= (
            np.abs(GRID_ANGSTROM - line) <= WING_HALF_WIDTH_PER_FWHM * profile.at(np.array([line]))[0]
        )
    checked = in_range & near_a_line
    relative = values[checked] / np.median(values[in_range])

    assert checked.sum() > 100
    assert np.abs(relative - 1.0).max() < FLATNESS_TOLERANCE


def test_the_response_is_flat_over_the_whole_fitted_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fitted response is flat to 1 percent over its whole range."""
    response = _fitted_response(monkeypatch)

    values = response.value_at(GRID_ANGSTROM)
    in_range = (GRID_ANGSTROM >= response.minimum_wavelength_angstrom) & (
        GRID_ANGSTROM <= response.maximum_wavelength_angstrom
    )
    relative = values[in_range] / np.median(values[in_range])

    assert np.abs(relative - 1.0).max() < FLATNESS_TOLERANCE


def test_the_skipped_band_is_one_and_a_half_line_spreads_wide() -> None:
    """The half-width is 1.5 times the stored line spread at each line."""
    profile = load_line_spread_profile(CAMERA_NAME)
    assert profile is not None

    half_widths = dict(
        zip(
            instrument_response._LINES_TO_SKIP_ANGSTROM,
            line_skip_half_widths_angstrom(profile, 45.0),
            strict=True,
        )
    )

    assert half_widths[6563.0] == pytest.approx(1.5 * 148.0)
    assert half_widths[4861.0] == pytest.approx(1.5 * 102.0)
    # In the blue, 1.5 line spreads is only 63-66 A, so the 60 A floor holds.
    assert half_widths[4340.0] >= 60.0


def test_without_a_profile_the_scalar_resolution_sets_the_band() -> None:
    """With no stored profile, one resolution element serves every line."""
    half_widths = line_skip_half_widths_angstrom(None, 100.0)

    assert half_widths.tolist() == [150.0] * len(instrument_response._LINES_TO_SKIP_ANGSTROM)


def test_a_given_profile_overrides_the_stored_one() -> None:
    """A caller can pass its own line-spread profile."""
    profile = ResolutionProfile(np.array([4000.0, 7000.0]), np.array([80.0, 80.0]))

    assert line_skip_half_widths_angstrom(profile, 45.0).tolist() == [120.0] * len(
        instrument_response._LINES_TO_SKIP_ANGSTROM
    )
