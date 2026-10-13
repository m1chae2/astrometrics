"""Purpose: Tests for the comparison of a spectrum with its Gaia XP spectrum.

Description: The comparison builds a Gaia XP spectrum and a calibrated
spectrum of the same star from one Pickles template, puts a known tilt or a
known wavelength shift into the calibrated one, and checks that the comparison
finds it. It also covers the cases that cannot be compared (no Gaia id, no XP
spectrum, no response-corrected spectrum), the Gaia id lookup, the four
checkpoint metrics, the run-level medians and the `gaia_xp_agreement` gate.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from astrometricslib.models.gaia_xp_comparison import GaiaXpComparison
from astrometricslib.models.gate_result import GateStatus
from astrometricslib.models.spectroscopy_quality import CatalogComparison, OutputQualityAssessment
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.shared.catalog_star_identity import gaia_dr3_source_id_of
from astrometricslib.pipelines.spectroscopy.post_processing.assess_output_quality import (
    output_quality_checkpoint,
)
from astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp import (
    COMPARISON_BANDS_ANGSTROM,
    GAIA_XP_GATE_NAME,
    GAIA_XP_RESIDUAL_RMS_LIMIT,
    GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM,
    GAIA_XP_WAVELENGTH_SHIFT_LIMIT_ANGSTROM,
    REASON_NO_SOURCE_ID,
    REASON_NO_SPECTRUM,
    REASON_NO_XP,
    compare_spectrum_to_xp,
    compare_to_gaia_xp,
    gaia_xp_gate,
    gaia_xp_metrics,
    gaia_xp_rows,
    summarize_gaia_xp,
    xp_resolution_fwhm_angstrom,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    load_line_spread_profile,
)
from astrometricslib.test.synthetic.gaia_xp_spectra import (
    CAMERA_NAME,
    FakeGaiaXpDriver,
    make_calibrated_spectrum,
    make_xp_spectrum,
)

SOURCE_ID = 1328045433153485824


def compare(spectral_type: str = "G2V", **errors: float) -> GaiaXpComparison:
    """Compare a template star's XP spectrum with a calibrated one.

    Parameters
    ----------
    spectral_type : `str`, optional
        The Pickles template both spectra come from.
    **errors
        Passed to `make_calibrated_spectrum` (tilt, shift or noise).

    Returns
    -------
    comparison : `GaiaXpComparison`
        The comparison result.
    """
    wavelength, intensity = make_calibrated_spectrum(spectral_type, **errors)
    return compare_spectrum_to_xp(
        wavelength,
        intensity,
        make_xp_spectrum(spectral_type),
        gaia_source_id=SOURCE_ID,
        line_spread=load_line_spread_profile(CAMERA_NAME),
    )


def test_a_spectrum_equal_to_its_xp_spectrum_agrees_with_it() -> None:
    """With no tilt and no shift the residuals and the tilt are small."""
    comparison = compare(noise_fraction=0.01)

    assert comparison.status == "compared"
    assert comparison.residual_rms_fraction < 0.02
    assert comparison.slope_percent_per_1000_angstrom == pytest.approx(0.0, abs=0.3)
    assert abs(comparison.wavelength_shift_angstrom) < 3.0
    assert [band.median_ratio for band in comparison.bands] == pytest.approx([1.0] * 4, abs=0.01)


@pytest.mark.parametrize("spectral_type", ["A0V", "G2V", "K5V"])
def test_a_tilt_of_five_percent_per_1000_angstrom_is_recovered(spectral_type: str) -> None:
    """A known tilt of 5 percent per 1000 A comes back within 0.5."""
    comparison = compare(spectral_type, tilt_percent_per_1000_angstrom=5.0, noise_fraction=0.01)

    assert comparison.slope_percent_per_1000_angstrom == pytest.approx(5.0, abs=0.5)
    # The ratio is 1 at 5500 A, so the bands rise from below 1 to above 1.
    ratios = [band.median_ratio for band in comparison.bands]
    assert ratios == sorted(ratios)
    assert ratios[0] < 0.97
    assert ratios[-1] > 1.07


@pytest.mark.parametrize("spectral_type", ["A0V", "G2V", "K5V"])
def test_a_wavelength_shift_of_twenty_angstroms_is_recovered(spectral_type: str) -> None:
    """A known 20 A shift of the features comes back within 3 A."""
    comparison = compare(spectral_type, shift_angstrom=20.0, noise_fraction=0.01)

    assert comparison.wavelength_shift_angstrom == pytest.approx(20.0, abs=3.0)
    assert comparison.wavelength_shift_correlation > 0.8


def test_a_shift_in_the_other_direction_has_the_other_sign() -> None:
    """Features at shorter wavelengths than XP's give a negative shift."""
    comparison = compare("G2V", shift_angstrom=-20.0, noise_fraction=0.01)

    assert comparison.wavelength_shift_angstrom == pytest.approx(-20.0, abs=3.0)


def test_the_observed_spectrum_is_blurred_where_xp_is_broader_and_xp_where_it_is_not() -> None:
    """Each spectrum is blurred to the broader resolution in its bands."""
    comparison = compare("G2V")

    assert comparison.observed_was_blurred
    assert comparison.xp_was_blurred
    # The instrument line spread is 42 A at 4200 A and 148 A at 6563 A;
    # XP's is 95 A to 130 A in the blue and 70 A in the red.
    assert xp_resolution_fwhm_angstrom(np.array([4340.0]))[0] > 42.0
    assert xp_resolution_fwhm_angstrom(np.array([6563.0]))[0] < 148.0


def test_the_telluric_bands_and_the_edges_are_left_out() -> None:
    """A deep dip inside an atmospheric band does not change the result."""
    wavelength, intensity = make_calibrated_spectrum("G2V")
    clean = compare_spectrum_to_xp(wavelength, intensity, make_xp_spectrum("G2V"))
    dipped = intensity.copy()
    dipped[(wavelength > 7500.0) & (wavelength < 7700.0)] *= 0.3
    with_dip = compare_spectrum_to_xp(wavelength, dipped, make_xp_spectrum("G2V"))

    assert with_dip.slope_percent_per_1000_angstrom == pytest.approx(
        clean.slope_percent_per_1000_angstrom, abs=0.2
    )
    assert with_dip.sample_count == clean.sample_count
    assert clean.sample_count < np.isfinite(intensity).sum()


def test_the_spectrums_own_errors_are_used_when_given() -> None:
    """A spectrum with errors is weighted by them, and the record says so."""
    wavelength, intensity = make_calibrated_spectrum("G2V", tilt_percent_per_1000_angstrom=2.0)
    errors = 0.02 * np.abs(intensity)

    comparison = compare_spectrum_to_xp(
        wavelength, intensity, make_xp_spectrum("G2V"), intensity_errors=errors, gaia_source_id=SOURCE_ID
    )

    assert comparison.used_observed_errors
    assert comparison.slope_percent_per_1000_angstrom == pytest.approx(2.0, abs=0.3)
    assert not compare("G2V").used_observed_errors


def test_a_spectrum_with_too_few_usable_samples_is_not_checked() -> None:
    """A spectrum that is NaN almost everywhere cannot be compared."""
    wavelength = np.arange(4200.0, 8000.0, 11.2)
    intensity = np.full(wavelength.size, np.nan)
    intensity[:10] = 1.0

    comparison = compare_spectrum_to_xp(wavelength, intensity, make_xp_spectrum("G2V"))

    assert comparison.status == "not_checked"
    assert "fewer than" in comparison.not_checked_reason


def test_a_star_with_no_gaia_id_is_not_checked_and_asks_for_no_spectrum() -> None:
    """Without a source id the driver is never asked."""
    wavelength, intensity = make_calibrated_spectrum("G2V")
    driver = FakeGaiaXpDriver({SOURCE_ID: make_xp_spectrum("G2V")})

    comparison = compare_to_gaia_xp(driver, None, wavelength, intensity)

    assert comparison.status == "not_checked"
    assert comparison.not_checked_reason == REASON_NO_SOURCE_ID
    assert driver.requested_ids == []


def test_a_source_with_no_xp_spectrum_is_not_checked() -> None:
    """A source the driver has no spectrum for is not checked."""
    wavelength, intensity = make_calibrated_spectrum("G2V")
    driver = FakeGaiaXpDriver()

    comparison = compare_to_gaia_xp(driver, SOURCE_ID, wavelength, intensity)

    assert comparison.status == "not_checked"
    assert comparison.not_checked_reason == REASON_NO_XP
    assert comparison.gaia_source_id == SOURCE_ID
    assert driver.requested_ids == [SOURCE_ID]


def test_a_star_with_no_response_corrected_spectrum_is_not_checked() -> None:
    """A camera with no stored instrument response gives nothing to compare."""
    wavelength, _intensity = make_calibrated_spectrum("G2V")
    driver = FakeGaiaXpDriver({SOURCE_ID: make_xp_spectrum("G2V")})

    comparison = compare_to_gaia_xp(driver, SOURCE_ID, wavelength, None)

    assert comparison.status == "not_checked"
    assert comparison.not_checked_reason == REASON_NO_SPECTRUM
    assert driver.requested_ids == []


def test_compare_to_gaia_xp_runs_the_comparison_with_the_drivers_spectrum() -> None:
    """With an id and a spectrum the driver's XP spectrum is compared."""
    wavelength, intensity = make_calibrated_spectrum("G2V", tilt_percent_per_1000_angstrom=4.0)
    driver = FakeGaiaXpDriver({SOURCE_ID: make_xp_spectrum("G2V")})

    comparison = compare_to_gaia_xp(driver, SOURCE_ID, wavelength, intensity)

    assert comparison.status == "compared"
    assert comparison.gaia_source_id == SOURCE_ID
    assert comparison.slope_percent_per_1000_angstrom == pytest.approx(4.0, abs=0.5)


def test_the_gaia_source_id_comes_from_the_stored_id_or_a_gaia_name() -> None:
    """The id is the stored number, else a Gaia name's number."""
    assert gaia_dr3_source_id_of(StellarObject(id="HD 1", gaia_dr3_source_id=42)) == 42
    assert gaia_dr3_source_id_of(StellarObject(id=f"Gaia DR3 {SOURCE_ID}")) == SOURCE_ID
    assert gaia_dr3_source_id_of(StellarObject(id="HD 1", name=f"Gaia DR3 {SOURCE_ID}")) == SOURCE_ID
    assert gaia_dr3_source_id_of(StellarObject(id="HD 151086")) is None
    # A made-up name for a Gaia star with no source number is not an id.
    assert gaia_dr3_source_id_of(StellarObject(id="Gaia DR3 J1234.5+12.3")) is None


def test_the_checkpoint_metrics_carry_the_designed_limits() -> None:
    """The metrics have the designed limits and pass on a good spectrum."""
    metrics = {entry.name: entry for entry in gaia_xp_metrics(compare(noise_fraction=0.01))}

    assert set(metrics) == {
        "gaia_xp_residual_rms_fraction",
        "gaia_xp_slope_percent_per_1000_angstrom",
        "gaia_xp_wavelength_shift_angstrom",
        "gaia_xp_available",
    }
    assert metrics["gaia_xp_residual_rms_fraction"].limit == pytest.approx(GAIA_XP_RESIDUAL_RMS_LIMIT)
    assert GAIA_XP_RESIDUAL_RMS_LIMIT == pytest.approx(0.05)
    assert metrics["gaia_xp_slope_percent_per_1000_angstrom"].limit == pytest.approx(3.0)
    assert metrics["gaia_xp_wavelength_shift_angstrom"].limit == pytest.approx(11.0)
    assert GAIA_XP_WAVELENGTH_SHIFT_LIMIT_ANGSTROM == pytest.approx(11.0)
    assert metrics["gaia_xp_available"].value == pytest.approx(1.0)
    assert metrics["gaia_xp_available"].limit is None
    assert all(entry.passed for entry in metrics.values() if entry.limit is not None), (
        "a spectrum equal to XP passes every limit"
    )


def test_a_tilted_and_shifted_spectrum_fails_its_checkpoint_metrics() -> None:
    """A 5 percent tilt and a 25 A shift fail the slope and shift limits."""
    comparison = compare(tilt_percent_per_1000_angstrom=5.0, shift_angstrom=25.0, noise_fraction=0.01)
    metrics = {entry.name: entry for entry in gaia_xp_metrics(comparison)}

    assert metrics["gaia_xp_slope_percent_per_1000_angstrom"].passed is False
    assert metrics["gaia_xp_wavelength_shift_angstrom"].passed is False
    assert metrics["gaia_xp_residual_rms_fraction"].passed is False


def test_metrics_of_a_spectrum_that_was_not_compared_have_no_value_and_say_why() -> None:
    """A not-checked spectrum reports availability 0 and no verdicts."""
    comparison = compare_to_gaia_xp(FakeGaiaXpDriver(), None, [5000.0], None)
    metrics = {entry.name: entry for entry in gaia_xp_metrics(comparison)}

    assert metrics["gaia_xp_available"].value == pytest.approx(0.0)
    assert metrics["gaia_xp_available"].note == REASON_NO_SOURCE_ID
    assert metrics["gaia_xp_residual_rms_fraction"].value is None
    assert metrics["gaia_xp_residual_rms_fraction"].passed is None
    assert metrics["gaia_xp_slope_percent_per_1000_angstrom"].value is None
    assert metrics["gaia_xp_wavelength_shift_angstrom"].value is None
    assert {e.name: e.value for e in gaia_xp_metrics(None)}["gaia_xp_available"] == pytest.approx(0.0)


def test_checkpoint_three_carries_the_gaia_metrics_and_a_flag() -> None:
    """Checkpoint 3 gets the Gaia metrics, classified or not."""
    assessment = OutputQualityAssessment(
        is_poor_match=False,
        is_ambiguous=False,
        is_class_ambiguous=False,
        catalog_agrees=True,
        is_trustworthy=True,
    )
    bad = compare(tilt_percent_per_1000_angstrom=6.0)

    classified = output_quality_checkpoint(
        assessment,
        own_spectral_type="G2V",
        catalog_spectral_type="G2V",
        catalog_comparison=CatalogComparison(),
        gaia_xp_comparison=bad,
    )
    unclassified = output_quality_checkpoint(
        assessment,
        own_spectral_type="Unknown",
        catalog_spectral_type=None,
        catalog_comparison=None,
        gaia_xp_comparison=bad,
    )
    without = output_quality_checkpoint(
        assessment, own_spectral_type="G2V", catalog_spectral_type="G2V", catalog_comparison=None
    )

    for checkpoint in (classified, unclassified):
        names = [entry.name for entry in checkpoint.metrics]
        assert "gaia_xp_residual_rms_fraction" in names
        assert "gaia_xp_disagrees" in checkpoint.flags
    assert "unclassified" in unclassified.flags
    assert "unclassified" not in classified.flags
    assert not any(entry.name.startswith("gaia_xp") for entry in without.metrics)
    assert "gaia_xp_disagrees" not in without.flags


def make_row(
    star_id: str, slope: float, ratios: tuple[float | None, ...] = (0.95, 1.0, 1.05, 1.1)
) -> dict[str, object]:
    """Build one run-level record like `gaia_xp_rows` gives.

    Parameters
    ----------
    star_id : `str`
        The star's id.
    slope : `float`
        The star's slope, in percent per 1000 A.
    ratios : `tuple`, optional
        The median ratio in each of the four bands.

    Returns
    -------
    row : `dict`
        The record.
    """
    comparison = GaiaXpComparison(
        status="compared",
        slope_percent_per_1000_angstrom=slope,
        bands=[
            {"start_angstrom": low, "end_angstrom": high, "median_ratio": ratio, "sample_count": 50}
            for (low, high), ratio in zip(COMPARISON_BANDS_ANGSTROM, ratios, strict=True)
        ],
    )
    return {"star_id": star_id, **comparison.model_dump()}


def test_the_run_summary_gives_the_median_ratio_and_scatter_in_each_band() -> None:
    """Medians are over stars; the scatter is the robust spread."""
    rows = [
        make_row("a", 4.0, (0.90, 1.00, 1.04, 1.10)),
        make_row("b", 5.0, (0.92, 1.01, 1.05, 1.12)),
        make_row("c", 6.0, (0.94, 1.02, 1.06, 1.14)),
    ]

    summary = summarize_gaia_xp(rows)

    assert summary.compared_star_count == 3
    assert [band.median_ratio for band in summary.bands] == pytest.approx([0.92, 1.01, 1.05, 1.12])
    # 1.4826 * MAD of (0.90, 0.92, 0.94) is 1.4826 * 0.02.
    assert summary.bands[0].scatter == pytest.approx(1.4826 * 0.02)
    assert [band.star_count for band in summary.bands] == [3, 3, 3, 3]
    assert summary.median_slope_percent_per_1000_angstrom == pytest.approx(5.0)
    assert summary.median_absolute_slope_percent_per_1000_angstrom == pytest.approx(5.0)


def test_a_star_with_several_spectra_counts_once() -> None:
    """Each star's numbers are the median over its own spectra."""
    rows = [
        make_row("a", 1.0, (1.0, 1.0, 1.0, 1.0)),
        make_row("a", 3.0, (1.0, 1.0, 1.0, 1.0)),
        make_row("a", 5.0, (1.0, 1.0, 1.0, 1.0)),
        make_row("b", -2.0, (1.0, 1.0, 1.0, 1.0)),
    ]

    summary = summarize_gaia_xp(rows)

    assert summary.compared_star_count == 2
    assert summary.median_slope_percent_per_1000_angstrom == pytest.approx(0.5)
    assert summary.median_absolute_slope_percent_per_1000_angstrom == pytest.approx(2.5)
    assert summarize_gaia_xp([]) is None


def test_the_run_summary_uses_the_median_absolute_slope_not_the_signed_one() -> None:
    """Tilts of opposite sign do not cancel in the absolute median."""
    rows = [make_row("a", 4.0), make_row("b", -4.0), make_row("c", 4.5)]

    summary = summarize_gaia_xp(rows)

    assert summary.median_slope_percent_per_1000_angstrom == pytest.approx(4.0)
    assert summary.median_absolute_slope_percent_per_1000_angstrom == pytest.approx(4.0)


def test_gaia_xp_rows_keeps_only_compared_spectra_with_the_star_id() -> None:
    """The worker records hold compared spectra only, tagged by star."""
    compared = StellarObject(id="Gaia DR3 1")
    compared.spectroscopy.gaia_xp_comparison = compare()
    unchecked = StellarObject(id="Gaia DR3 2")
    unchecked.spectroscopy.gaia_xp_comparison = compare_to_gaia_xp(FakeGaiaXpDriver(), None, [5000.0], None)
    unrecorded = StellarObject(id="Gaia DR3 3")
    no_spectroscopy = SimpleNamespace(spectroscopy=None, id="x")

    rows = gaia_xp_rows([compared, unchecked, unrecorded, no_spectroscopy])

    assert [row["star_id"] for row in rows] == ["Gaia DR3 1"]
    assert GaiaXpComparison.model_validate(rows[0]).status == "compared"


def test_the_gate_is_not_checked_with_fewer_than_three_compared_stars() -> None:
    """Two stars, however bad, are too few for a run verdict."""
    rows = [make_row("a", 9.0), make_row("b", 9.0)]

    gate = gaia_xp_gate(rows)

    assert gate.name == GAIA_XP_GATE_NAME
    assert gate.status is GateStatus.NOT_CHECKED
    assert gaia_xp_gate([]).status is GateStatus.NOT_CHECKED
    assert GAIA_XP_GATE_NAME == "gaia_xp_agreement"


def test_the_gate_fails_when_the_median_absolute_slope_is_above_the_limit() -> None:
    """Three stars tilted by 4 percent per 1000 A fail the gate."""
    rows = [make_row("a", 4.0), make_row("b", -4.5), make_row("c", 5.0)]

    gate = gaia_xp_gate(rows)

    assert gate.name == GAIA_XP_GATE_NAME
    assert gate.status is GateStatus.FAILED
    assert gate.measured_value == pytest.approx(4.5)
    assert gate.limit == GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM


def test_the_gate_passes_when_the_stars_agree_with_gaia() -> None:
    """Three stars within 3 percent per 1000 A pass the gate."""
    rows = [make_row("a", 0.5), make_row("b", -1.0), make_row("c", 2.0)]

    gate = gaia_xp_gate(rows)

    assert gate.name == GAIA_XP_GATE_NAME
    assert gate.status is GateStatus.PASSED
    assert gate.measured_value == pytest.approx(1.0)


def test_the_gate_judges_stars_not_spectra() -> None:
    """Many frames of one star do not make three stars."""
    rows = [make_row("a", 9.0) for _ in range(10)]

    assert gaia_xp_gate(rows).status is GateStatus.NOT_CHECKED
