"""A fixed astrometry run for the metric conformance test.

The conformance test (`pipelines/test/test_metric_conformance.py`) needs every
pipeline that records measurements to show that its record step emits each
metric the pipeline declares. This file runs the astrometry record step on one
solved run whose every value is filled, so no metric is left out for being
missing. The expected-residual rows come from their own step
(`expected_residual_measurements`), run on four stars. The target-clipping rows
come from their own step (`target_clipping_measurements`), which the fixture
runs on two small frames and a small stack written to a temporary folder.
"""

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib.models.quality.gate_result import GateResult, passed_gate
from astrometricslib.models.quality.measurement import QualityMeasurement
from astrometricslib.models.quality.quality_summary import AstrometryPipelineQualityMetrics
from astrometricslib.pipelines.astrometry.post_processing.expected_residual import (
    expected_residual,
    expected_residual_measurements,
)
from astrometricslib.pipelines.astrometry.post_processing.record_quality import (
    PLATE_SOLVE_GATE_NAME,
    astrometry_run_gates,
    astrometry_run_measurements,
)
from astrometricslib.pipelines.astrometry.post_processing.target_clipping import (
    target_clipping_measurements,
)
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.shared.registration_record import (
    RegistrationRecord,
    write_registration_record,
)


def astrometry_fixture_run() -> tuple[AstrometryPipelineQualityMetrics, list[GateResult], PipelineRunContext]:
    """Build a fixed, fully measured, solved astrometry run.

    Returns
    -------
    metrics : `AstrometryPipelineQualityMetrics`
        The run's numbers.
    gates : `list` of `GateResult`
        The plate-solve gate and the four gates of `astrometry_run_gates`.
    context : `PipelineRunContext`
        The run's shared facts, for target ``M 99``.
    """
    metrics = AstrometryPipelineQualityMetrics(
        sources_detected=80,
        solve_attempted=True,
        plate_solve_succeeded=True,
        simbad_matched_count=40,
        catalog_matched_star_count=60,
        position_only_star_count=15,
        unresolved_star_count=5,
        catalog_match_separation_rms_arcsec=1.2,
        plate_solve_fit_residual_rms_arcsec=0.8,
        plate_solve_matched_star_count=70,
        gaia_fit_rms_arcsec=0.4,
        gaia_fit_rms_before_arcsec=0.9,
        gaia_fit_star_count=55,
        wcs_refinement_shift_arcsec=1.5,
        plate_scale_arcsec_per_pixel=1.9,
        star_fwhm_px=3.0,
        remote_catalog_queries_attempted=10,
        remote_catalog_queries_failed=1,
        plate_solve_attempts=1,
    )
    gates = [passed_gate(PLATE_SOLVE_GATE_NAME, detail="the image was solved to sky coordinates")]
    gates.extend(astrometry_run_gates(metrics))
    context = PipelineRunContext(
        "M 99", "astrometry", "1.4.0", "job-1", "job-1", datetime(2026, 10, 22, tzinfo=UTC)
    )
    return metrics, gates, context


def fixture_target_clipping_rows(context: PipelineRunContext) -> list[QualityMeasurement]:
    """Run the target-clipping step on a small stack and two small frames.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        The two target-clipping rows of each of two frames. The stack holds a
        bright star at the target and the frames hold a clipped patch there.
    """
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crpix = [51.0, 51.0]
    wcs.wcs.crval = [250.0, 36.0]
    wcs.wcs.cdelt = [-2.0 / 3600.0, 2.0 / 3600.0]
    generator = np.random.default_rng(1)
    with tempfile.TemporaryDirectory() as folder:
        rows_grid, columns_grid = np.mgrid[0:100, 0:100]
        star = 500.0 * np.exp(-((rows_grid - 50.0) ** 2 + (columns_grid - 50.0) ** 2) / 8.0)
        stack_path = Path(folder) / "M99_L_Stacked.fits"
        fits.PrimaryHDU(
            data=(100.0 + generator.normal(0.0, 5.0, (100, 100)) + star).astype(np.float32)
        ).writeto(stack_path)
        frames = []
        for name in ("a.fits", "b.fits"):
            path = Path(folder) / "lights" / "M 99" / "Scope" / "Cam" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            pixels = np.full((100, 100), 500.0, dtype=np.float32)
            pixels[48:53, 48:53] = 65535.0
            fits.PrimaryHDU(data=pixels).writeto(path)
            frames.append(SimpleNamespace(path=str(path), camera="Cam", timestamp=1_792_000_000.0))
        identity = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
        write_registration_record(
            str(stack_path),
            RegistrationRecord(
                light_paths=tuple(frame.path for frame in frames),
                homographies=(identity, identity),
                included=(True, True),
                crop_offset=(0, 0),
                reference_frame=frames[0].path,
            ),
        )
        return target_clipping_measurements(
            str(stack_path), frames, context, solved_wcs=wcs, target_center_deg=(250.0, 36.0)
        )


def astrometry_emitted_measurements() -> list[QualityMeasurement]:
    """Run the astrometry record step on a fixed, fully measured run.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        What the record step emits for a solved run with a Gaia refit and
        a match table.
    """
    metrics, gates, context = astrometry_fixture_run()
    return [
        *astrometry_run_measurements(metrics, gates, context, "M99_Stacked.fits"),
        *fixture_target_clipping_rows(context),
        *expected_residual_measurements(
            expected_residual(
                [12.0, 20.0, 35.0, 60.0], "", metrics.star_fwhm_px, metrics.plate_scale_arcsec_per_pixel
            ),
            context,
            "M99_Stacked.fits",
        ),
    ]
