"""Purpose: Verify the Gaia XP check is wired into pipeline and run summary.

Description: `compare_to_gaia_xp` has its own unit tests. These confirm the
pieces that carry its results: `_apply_result_to_stellar_object` runs the
check with the driver it was given and stores the comparison and the
checkpoint metrics, a star with no Gaia id is not checked and never touches
the driver, and both the single-image runner and the batch merge put the
run-level summary and the `gaia_xp_agreement` gate on the quality summary.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.models.gaia_xp_comparison import GaiaXpComparison
from astrometricslib.models.gate_result import GateStatus
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import Target
from astrometricslib.pipelines.pipeline_base import PipelineRequest, Result
from astrometricslib.pipelines.shared.star_recording import StarIdentificationBreakdown
from astrometricslib.pipelines.spectroscopy import batch, runner
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
from astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp import (
    GAIA_XP_GATE_NAME,
    REASON_NO_SOURCE_ID,
    REASON_NO_XP,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response import load_instrument_response
from astrometricslib.pipelines.spectroscopy.pre_processing.quantum_efficiency_correction import (
    curve_from_profile_record,
    interpolate_quantum_efficiency,
)
from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import _get_reference_templates
from astrometricslib.test.synthetic.gaia_xp_spectra import FakeGaiaXpDriver, make_xp_spectrum
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig
from astrometricslib.utilities.parallel_batch import BatchRunSummary

SOURCE_ID = 1328045433153485824

# A stand-in for AstrometricsImage: the pipeline only reads its header.
_FAKE_IMAGE = SimpleNamespace()


def build_pipeline(gaia_xp: FakeGaiaXpDriver) -> SpectroscopyPipeline:
    """Build a pipeline for the test camera with a fake Gaia XP driver.

    Parameters
    ----------
    gaia_xp : `FakeGaiaXpDriver`
        The driver the pipeline should use.

    Returns
    -------
    pipeline : `SpectroscopyPipeline`
        A pipeline whose camera has a quantum efficiency curve and an
        instrument response on file.
    """
    camera = CameraConfig(
        name="ZWO ASI 533MM Pro",
        pixel_size_um=3.76,
        sensor_width_px=900,
        sensor_height_px=900,
        sensor_min_wavelength=300.0,
        sensor_max_wavelength=1000.0,
    )
    config = SpectroscopyConfig(camera=camera, grating_distance_mm=16.5)
    return SpectroscopyPipeline(config=config, drivers=Drivers(gaia_xp=gaia_xp))


def extraction_result(spectral_type: str = "G2V") -> dict[str, object]:
    """Build the extraction result the instrument would give for a template.

    The raw counts are the template times the quantum efficiency times the
    instrument response, the exact inverse of what the pipeline divides out.

    Parameters
    ----------
    spectral_type : `str`, optional
        The Pickles template to use.

    Returns
    -------
    result : `dict`
        What `_apply_result_to_stellar_object` takes.
    """
    template_wavelength, template_flux = _get_reference_templates()[spectral_type]
    # One sample per pixel (11.2 Angstroms), as the extractor gives.
    wavelength_angstrom = np.arange(3800.0, 9000.0, 11.2)
    flux = np.interp(wavelength_angstrom, template_wavelength, template_flux)
    curve = curve_from_profile_record(resolve_camera_profile("ZWO ASI 533MM Pro").quantum_efficiency)
    response = load_instrument_response("ZWO ASI 533MM Pro")
    counts = (
        flux
        * interpolate_quantum_efficiency(wavelength_angstrom / 10.0, curve)
        * response.value_at(wavelength_angstrom)
    )
    return {
        "detected_angle": 0.0,
        "wavelengths": (wavelength_angstrom / 10.0).tolist(),
        "intensities": counts.tolist(),
        "target_pos": (100.0, 200.0),
        "trail_centerline_px": None,
        "trail_width_px": None,
    }


def checkpoint_metrics(star: StellarObject) -> dict[str, float | None]:
    """Read the post-processing checkpoint's metrics as name and value.

    Returns
    -------
    values : `dict` [`str`, `float` or `None`]
        Each metric's value.
    """
    checkpoint = next(entry for entry in star.spectroscopy.stage_quality if entry.stage == "post_processing")
    return {entry.name: entry.value for entry in checkpoint.metrics}


def test_the_pipeline_checks_a_gaia_star_against_the_drivers_xp_spectrum() -> None:
    """A star with a Gaia id is compared and the checkpoint has the metrics."""
    driver = FakeGaiaXpDriver({SOURCE_ID: make_xp_spectrum("G2V")})
    pipeline = build_pipeline(driver)
    star = StellarObject(id=f"Gaia DR3 {SOURCE_ID}")

    pipeline._apply_result_to_stellar_object(star, extraction_result("G2V"), _FAKE_IMAGE)

    comparison = star.spectroscopy.gaia_xp_comparison
    assert driver.requested_ids == [SOURCE_ID]
    assert comparison.status == "compared"
    assert comparison.gaia_source_id == SOURCE_ID
    # The instrument response was applied exactly, so the star only differs
    # from XP by the template's own sharpness.
    assert abs(comparison.slope_percent_per_1000_angstrom) < 1.0
    values = checkpoint_metrics(star)
    assert values["gaia_xp_available"] == pytest.approx(1.0)
    assert values["gaia_xp_residual_rms_fraction"] == pytest.approx(comparison.residual_rms_fraction)
    assert values["gaia_xp_slope_percent_per_1000_angstrom"] == pytest.approx(
        abs(comparison.slope_percent_per_1000_angstrom)
    )


def test_a_star_found_through_simbad_uses_its_stored_gaia_id() -> None:
    """A star named by SIMBAD is compared through its stored Gaia source id."""
    driver = FakeGaiaXpDriver({SOURCE_ID: make_xp_spectrum("G2V")})
    pipeline = build_pipeline(driver)
    star = StellarObject(id="HD 151086", gaia_dr3_source_id=SOURCE_ID)

    pipeline._apply_result_to_stellar_object(star, extraction_result("G2V"), _FAKE_IMAGE)

    assert driver.requested_ids == [SOURCE_ID]
    assert star.spectroscopy.gaia_xp_comparison.status == "compared"


def test_a_star_with_no_gaia_id_is_not_checked_and_the_driver_is_not_asked() -> None:
    """The reason is stored and the checkpoint metrics are empty."""
    driver = FakeGaiaXpDriver({SOURCE_ID: make_xp_spectrum("G2V")})
    pipeline = build_pipeline(driver)
    star = StellarObject(id="HD 151086")

    pipeline._apply_result_to_stellar_object(star, extraction_result("G2V"), _FAKE_IMAGE)

    comparison = star.spectroscopy.gaia_xp_comparison
    assert comparison.status == "not_checked"
    assert comparison.not_checked_reason == REASON_NO_SOURCE_ID
    assert driver.requested_ids == []
    values = checkpoint_metrics(star)
    assert values["gaia_xp_available"] == pytest.approx(0.0)
    assert values["gaia_xp_residual_rms_fraction"] is None


def test_a_gaia_star_with_no_xp_spectrum_is_not_checked_with_that_reason() -> None:
    """A source the driver has no spectrum for is stored as not checked."""
    driver = FakeGaiaXpDriver()
    pipeline = build_pipeline(driver)
    star = StellarObject(id=f"Gaia DR3 {SOURCE_ID}")

    pipeline._apply_result_to_stellar_object(star, extraction_result("G2V"), _FAKE_IMAGE)

    assert star.spectroscopy.gaia_xp_comparison.not_checked_reason == REASON_NO_XP
    assert driver.requested_ids == [SOURCE_ID]


def test_a_failing_comparison_never_stops_the_spectrum(monkeypatch: pytest.MonkeyPatch) -> None:
    """An error inside the comparison is stored as a not-checked reason."""

    def broken(*_arguments: object, **_keywords: object) -> None:
        """Fail the way a bad numeric step does.

        Raises
        ------
        ValueError
            Always.
        """
        raise ValueError("singular matrix")

    monkeypatch.setattr(
        "astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp.compare_spectrum_to_xp",
        broken,
    )
    driver = FakeGaiaXpDriver({SOURCE_ID: make_xp_spectrum("G2V")})
    pipeline = build_pipeline(driver)
    star = StellarObject(id=f"Gaia DR3 {SOURCE_ID}")

    pipeline._apply_result_to_stellar_object(star, extraction_result("G2V"), _FAKE_IMAGE)

    assert star.spectroscopy.gaia_xp_comparison.status == "not_checked"
    assert "singular matrix" in star.spectroscopy.gaia_xp_comparison.not_checked_reason
    assert star.spectroscopy.wavelengths_angstrom


def compared_star(star_id: str, slope: float) -> SimpleNamespace:
    """Build a stand-in star whose spectrum was compared with Gaia XP.

    Parameters
    ----------
    star_id : `str`
        The star's id.
    slope : `float`
        The comparison's slope, in percent per 1000 A.

    Returns
    -------
    star : `SimpleNamespace`
        A star with the fields the run summary reads.
    """
    comparison = GaiaXpComparison(
        status="compared",
        slope_percent_per_1000_angstrom=slope,
        bands=[
            {
                "start_angstrom": low,
                "end_angstrom": high,
                "median_ratio": 1.0 + 0.01 * slope,
                "sample_count": 40,
            }
            for low, high in ((4200.0, 5000.0), (5000.0, 6000.0), (6000.0, 7000.0), (7000.0, 8000.0))
        ],
    )
    spectroscopy = SimpleNamespace(
        self_determined_spectral_type="Unknown",
        catalog_comparison=None,
        probable_spectral_features=[],
        resolution_element_angstrom=None,
        dispersion_angle=0.0,
        trail_width_px=[],
        stage_quality=[],
        gaia_xp_comparison=comparison,
    )
    return SimpleNamespace(id=star_id, spectroscopy=spectroscopy)


def test_the_runner_records_the_gaia_summary_and_fails_the_gate_for_tilted_stars() -> None:
    """Three stars tilted by 5 percent per 1000 A fail `gaia_xp_agreement`."""
    stars = [compared_star(f"star {index}", 5.0) for index in range(3)]
    result = Result(
        stellar_objects=stars,
        payload={
            "star_id_breakdown": StarIdentificationBreakdown(
                catalog_matched=3, position_only=0, unresolved=0
            ),
            "spectroscopy": SimpleNamespace(
                last_run_zero_order_saturation_fractions=[0.0],
                config=SimpleNamespace(camera=SimpleNamespace(name="ZWO ASI 533MM Pro")),
            ),
            "flagged_spectral_classifications": [],
        },
    )
    request = PipelineRequest(target=Target(id="Test Target"), catalog_access=None)

    summary = runner.SpectroscopyPipelineAdapter().validate_output(request, result)

    assert summary.gate(GAIA_XP_GATE_NAME).status is GateStatus.FAILED
    gaia_summary = summary.spectroscopy_metrics.gaia_xp_summary
    assert gaia_summary.compared_star_count == 3
    assert gaia_summary.median_slope_percent_per_1000_angstrom == pytest.approx(5.0)
    assert [band.median_ratio for band in gaia_summary.bands] == pytest.approx([1.05] * 4)


def test_the_runner_leaves_the_gate_unchecked_when_no_star_was_compared() -> None:
    """With no compared star the gate is not checked; there is no summary."""
    star = compared_star("star", 0.0)
    star.spectroscopy.gaia_xp_comparison = GaiaXpComparison(
        status="not_checked", not_checked_reason=REASON_NO_SOURCE_ID
    )
    result = Result(
        stellar_objects=[star],
        payload={
            "star_id_breakdown": StarIdentificationBreakdown(
                catalog_matched=1, position_only=0, unresolved=0
            ),
            "spectroscopy": SimpleNamespace(
                last_run_zero_order_saturation_fractions=[0.0],
                config=SimpleNamespace(camera=SimpleNamespace(name="ZWO ASI 533MM Pro")),
            ),
            "flagged_spectral_classifications": [],
        },
    )
    request = PipelineRequest(target=Target(id="Test Target"), catalog_access=None)

    summary = runner.SpectroscopyPipelineAdapter().validate_output(request, result)

    assert summary.gate(GAIA_XP_GATE_NAME).status is GateStatus.NOT_CHECKED
    assert summary.spectroscopy_metrics.gaia_xp_summary is None


def test_the_batch_merge_carries_every_frames_gaia_rows_into_the_summary() -> None:
    """Rows from the workers become the run summary and the gate verdict."""
    target = Target(id="GaiaXpBatchTarget")

    def frame(star_ids: list[str], slope: float) -> dict[str, object]:
        """Build one worker result with compared spectra for some stars.

        Returns
        -------
        result : `dict`
            The worker's result dictionary.
        """
        stars = [compared_star(star_id, slope) for star_id in star_ids]
        return {
            "status": "success",
            "stars_processed": len(stars),
            "dispersion_angles": [],
            "trail_widths": [],
            "zero_order_saturation_fractions": [0.0],
            "gaia_xp": [
                {"star_id": star.id, **star.spectroscopy.gaia_xp_comparison.model_dump()} for star in stars
            ],
        }

    summary = BatchRunSummary(
        succeeded=["a.fits", "b.fits"],
        failed=[],
        results={"a.fits": frame(["s1", "s2"], 4.0), "b.fits": frame(["s2", "s3"], 4.0)},
    )
    session = SimpleNamespace(id="Target:2026-01-01:800:0", frame_paths=["a.fits", "b.fits"])

    batch._attach_spectroscopy_quality_summary(target, summary, [(session, SimpleNamespace())])

    quality = target.quality.spectroscopy
    assert quality.spectroscopy_metrics.gaia_xp_summary.compared_star_count == 3
    assert quality.gate(GAIA_XP_GATE_NAME).status is GateStatus.FAILED
    assert np.isclose(quality.gate(GAIA_XP_GATE_NAME).measured_value, 4.0)
