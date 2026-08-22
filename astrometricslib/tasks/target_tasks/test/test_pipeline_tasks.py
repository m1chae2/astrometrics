"""Purpose: Unit tests for tasks/target_tasks/pipeline_tasks.py.

Description: Verifies analyze_target, stack_frames's homogeneous
frame validation, add_frame, and analyze_frame_spectroscopy -- the
free functions that replaced Target's former orchestration methods.
"""

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from astropy.io import fits
from astropy.modeling.models import Gaussian2D

from astrometricslib import Astrometrics
from astrometricslib.data_access.butler import DiskButler
from astrometricslib.models.moving_object import CascadeStage
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.tasks.target_tasks import pipeline_tasks
from astrometricslib.utilities.config_loader import AppConfiguration


def _make_image_fits(path: str, shape: tuple = (100, 100)) -> None:
    """Write a dummy FITS image file for use as test fixture data."""
    arr = np.zeros(shape, dtype=np.float32)
    hdu = fits.PrimaryHDU(arr)
    hdu.header["OBJECT"] = "Vega"
    hdu.header["INSTRUME"] = "TestCam"
    hdu.writeto(path, overwrite=True)


@pytest.mark.filterwarnings("ignore:No sources were found")
def test_target_analyze_target(tmp_path: Any) -> None:
    """Test that analyzing a target frame runs the astrometry pipeline.

    Confirms the pipeline correctly identifies stars in the frame.
    """
    config = AppConfiguration()
    original_path = config.get_value("Image Library", "path", fallback="./libraryIndex")
    config.update_config({"Image Library": {"path": str(tmp_path)}})

    try:
        astrometrics = Astrometrics(app_config=config)

        image_path = str(tmp_path / "test_image.fits")
        _make_image_fits(image_path)

        target = astrometrics.targets.create("Vega")
        from astrometricslib import FrameRecord

        results = pipeline_tasks.analyze_target(target, frames=[FrameRecord(path=image_path)])
        assert "stellar_objects" in results
        assert "wcs" in results
    finally:
        config.update_config({"Image Library": {"path": original_path}})


def test_target_stack_and_solve_homogeneous_validation() -> None:
    """Verifies that stack_and_solve.

    validates that only homogeneous frame types are stacked,.
    and raises a ValueError if frames are mixed or missing.
    """
    # 1. Test empty frames
    target = Target(id="EmptyTarget")
    with pytest.raises(ValueError, match=r"Target has no frames available to stack\."):
        pipeline_tasks.stack_and_solve(target)

    # 2. Test mixed frames (SPEC + standard LIGHT)
    target_mixed = Target(
        id="MixedTarget",
        frames=[
            FrameRecord(path="/path/to/frame1.fits", type="light", filter="SPEC", exposure="10.0"),
            FrameRecord(path="/path/to/frame2.fits", type="light", filter="Ha", exposure="10.0"),
        ],
    )
    with pytest.raises(
        ValueError, match=r"Target contains a mixed set of spectral.*and standard imaging frames"
    ):
        pipeline_tasks.stack_and_solve(target_mixed)

    # 3. Test homogeneous frames (no mixed-frame error, though it might
    # fail later due to missing file/Siril execution)
    target_homog = Target(
        id="SpectralTarget",
        frames=[
            FrameRecord(path="/path/to/frame1.fits", type="light", filter="SPEC", exposure="10.0"),
            FrameRecord(path="/path/to/frame2.fits", type="light", filter="SPEC", exposure="10.0"),
        ],
    )

    # It should pass the homogeneous check and return None due to
    # dummy paths and unconfigured Siril driver
    result = pipeline_tasks.stack_and_solve(target_homog)
    assert result is None


def test_target_add_frame_validation(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Tests that add_frame.

    correctly extracts FITS metadata, prevents duplicate paths,. and
    proactively enforces filter homogeneity.
    """
    # Create test FITS files
    standard_fit = tmp_path / "standard.fits"
    spectral_fit = tmp_path / "spectral.fits"

    # Write standard FITS
    arr = np.zeros((10, 10), dtype=np.float32)
    hdu1 = fits.PrimaryHDU(arr)
    hdu1.header["OBJECT"] = "Vega"
    hdu1.header["FILTER"] = "LUMINANCE"
    hdu1.header["EXPTIME"] = 5.0
    hdu1.header["GAIN"] = "800"
    hdu1.writeto(standard_fit, overwrite=True)

    # Write spectral FITS
    hdu2 = fits.PrimaryHDU(arr)
    hdu2.header["OBJECT"] = "Vega"
    hdu2.header["FILTER"] = "SPECTROSCOPY"
    hdu2.header["EXPTIME"] = 10.0
    hdu2.header["GAIN"] = "1600"
    hdu2.writeto(spectral_fit, overwrite=True)

    target = Target(id="Vega")

    # 1. Add standard frame
    rec1 = pipeline_tasks.add_frame(target, str(standard_fit))
    assert rec1.filter.name == "L"
    assert rec1.exposure == "5.0"
    assert rec1.iso == "800"
    assert target.exposure_sec == pytest.approx(5.0)
    assert len(target.frames) == 1

    # 2. Add duplicate frame
    rec2 = pipeline_tasks.add_frame(target, str(standard_fit))
    assert rec2 == rec1
    assert len(target.frames) == 1

    # 3. Add incompatible spectral frame (proactive boundary validation)
    with pytest.raises(
        ValueError, match=r"Target contains a mixed set of spectral.*and standard imaging frames"
    ):
        pipeline_tasks.add_frame(target, str(spectral_fit))


def test_target_analyze_frame_spectroscopy(tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Tests analyze_frame_spectroscopy.

    coordinates execution, target ID mapping, and database index saving.
    """
    from astrometricslib.utilities import config_loader

    config = AppConfiguration()
    original_instance = config_loader._instance
    config_loader._instance = config

    original_path = config.get_value("Image Library", "path", fallback="./libraryIndex")
    config.update_config({"Image Library": {"path": str(tmp_path)}})

    try:
        # Create a mock fits file
        fit_path = tmp_path / "vega_spec.fits"
        arr = np.zeros((10, 10), dtype=np.float32)
        hdu = fits.PrimaryHDU(arr)
        hdu.header["OBJECT"] = "Vega"
        hdu.header["FILTER"] = "SPECTROSCOPY"
        hdu.header["EXPTIME"] = 5.0
        hdu.writeto(fit_path, overwrite=True)

        # Mock Pipelines
        from astrometricslib.analysis_context import AnalysisContext
        from astrometricslib.utilities.image import AstrometricsImage

        img = AstrometricsImage(str(fit_path))
        mock_context = AnalysisContext(image=img, stellar_objects=[StellarObject(id="Vega_Star")], wcs=None)

        mocker.patch(
            "astrometricslib.tasks.stellar_tasks.astrometry_tasks.astrometry_pipeline.AstrometryPipeline.prepare_image",
            return_value=mock_context,
        )
        mocker.patch(
            "astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.spectroscopy_pipeline.SpectroscopyPipeline.process",
            return_value=[StellarObject(id="Vega_Star")],
        )

        target = Target(id="Vega")
        _context, objs = pipeline_tasks.analyze_frame_spectroscopy(target, str(fit_path))

        assert len(objs) == 1
        assert objs[0].id == "Vega_Star"
        assert "Vega" in objs[0].target_ids
        assert len(target.frames) == 1

        # Verify persistence
        from astrometricslib.drivers import disk_interface

        loaded = disk_interface.load_stellar_objects(config)
        assert len(loaded) == 1
        assert loaded[0].id == "Vega_Star"
        assert "Vega" in loaded[0].target_ids
    finally:
        config.update_config({"Image Library": {"path": original_path}})
        config_loader._instance = original_instance


def _write_asteroid_recovery_frame_fits(path, star_pixel_xy, extra_source_pixel_xy_list=()):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Write a synthetic light frame FITS file with Gaussian source(s).

    `extra_source_pixel_xy_list` adds further one-off point sources to
    this single frame only, e.g. to simulate a cosmic ray that a real
    moving object's persistence chain must not absorb.
    """
    rng = np.random.default_rng(0)
    data = rng.normal(100.0, 5.0, (64, 64)).astype(np.float32)
    yy, xx = np.mgrid[0:64, 0:64]
    data += Gaussian2D(5000.0, star_pixel_xy[0], star_pixel_xy[1], 2.0, 2.0)(xx, yy)
    for extra_pixel_x, extra_pixel_y in extra_source_pixel_xy_list:
        data += Gaussian2D(5000.0, extra_pixel_x, extra_pixel_y, 2.0, 2.0)(xx, yy)
    header = fits.Header()
    header["RA"] = 150.0
    header["DEC"] = 0.0
    fits.PrimaryHDU(data.astype(np.float32), header=header).writeto(path, overwrite=True)


def _write_asteroid_recovery_stack_fits(path):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Write a synthetic stack FITS file with a real TAN WCS header."""
    header = fits.Header()
    header["NAXIS1"] = 64
    header["NAXIS2"] = 64
    header["CTYPE1"] = "RA---TAN"
    header["CTYPE2"] = "DEC--TAN"
    header["CRVAL1"] = 150.0
    header["CRVAL2"] = 0.0
    header["CRPIX1"] = 32.0
    header["CRPIX2"] = 32.0
    header["CD1_1"] = -0.0005
    header["CD1_2"] = 0.0
    header["CD2_1"] = 0.0
    header["CD2_2"] = 0.0005
    header["CUNIT1"] = "deg"
    header["CUNIT2"] = "deg"
    fits.PrimaryHDU(np.zeros((64, 64), dtype=np.float32), header=header).writeto(path, overwrite=True)


def test_target_analyze_target_asteroid_recovery(tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify analyze_target wires the asteroid recovery pipeline.

    Candidates are populated on the Target, and the quality summary
    reflects the pipeline's own metrics and session provenance.
    """
    mocker.patch("astroquery.imcce.Skybot.cone_search", return_value=None)

    star_pixel_positions = [(20.0, 20.0), (25.0, 23.0), (30.0, 26.0), (35.0, 29.0)]
    timestamps = [0.0, 600.0, 1200.0, 1800.0]
    frames = []
    for index, (star_pixel, timestamp) in enumerate(zip(star_pixel_positions, timestamps, strict=False)):
        frame_path = tmp_path / f"frame{index}.fits"
        _write_asteroid_recovery_frame_fits(frame_path, star_pixel)
        frames.append(FrameRecord(path=str(frame_path), timestamp=timestamp))

    stack_path = tmp_path / "stack.fits"
    _write_asteroid_recovery_stack_fits(stack_path)

    target = Target(id="AsteroidRecoveryTestTarget", frames=frames)
    target.stacked_image = str(stack_path)

    result = pipeline_tasks.analyze_target(target, pipeline_type="asteroid_recovery")

    assert result["status"] == "completed"
    assert len(target.asteroid_candidates) == 1
    assert target.asteroid_candidates[0].cascade_stage == CascadeStage.RATE_LINEARITY_CONFIRMED

    summary = target.asteroid_recovery_quality_summary
    assert summary is not None
    assert summary.upstream_quality_summary_reference == "astrometry"
    assert summary.asteroid_recovery_metrics.frames_with_wcs_estimate == 4
    assert summary.asteroid_recovery_metrics.candidates_rate_linearity_confirmed == 1
    assert len(summary.target_session_breakdown) == 1
    assert summary.target_session_breakdown[0].frames_contributed == 4
    assert summary.flagged is True
    assert "not matched to a known body" in summary.flag_reasons[0]


def test_target_analyze_target_asteroid_recovery_drops_rejected_candidates(tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify only surviving candidates are persisted onto the target.

    A cosmic-ray-like point source appearing in a single frame only
    chains into its own single-detection candidate alongside the
    genuine moving-object track. `analyze_target` should still report
    it in the run's metrics (the full discrimination-cascade audit
    trail), but must not persist it onto `target.asteroid_candidates`
    -- otherwise a dense field's rejected noise chains bloat the
    target's persisted record without limit.
    """
    mocker.patch("astroquery.imcce.Skybot.cone_search", return_value=None)

    star_pixel_positions = [(20.0, 20.0), (25.0, 23.0), (30.0, 26.0), (35.0, 29.0)]
    timestamps = [0.0, 600.0, 1200.0, 1800.0]
    frames = []
    for index, (star_pixel, timestamp) in enumerate(zip(star_pixel_positions, timestamps, strict=False)):
        frame_path = tmp_path / f"frame{index}.fits"
        extra_sources = [(50.0, 10.0)] if index == 0 else []
        _write_asteroid_recovery_frame_fits(frame_path, star_pixel, extra_sources)
        frames.append(FrameRecord(path=str(frame_path), timestamp=timestamp))

    stack_path = tmp_path / "stack.fits"
    _write_asteroid_recovery_stack_fits(stack_path)

    target = Target(id="AsteroidRecoveryMixedTestTarget", frames=frames)
    target.stacked_image = str(stack_path)

    result = pipeline_tasks.analyze_target(target, pipeline_type="asteroid_recovery")

    assert result["status"] == "completed"
    # Only the confirmed track is persisted -- the single-frame cosmic
    # ray is dropped, not carried onto the target.
    assert len(target.asteroid_candidates) == 1
    assert target.asteroid_candidates[0].cascade_stage == CascadeStage.RATE_LINEARITY_CONFIRMED

    # But the pipeline's own metrics still account for both candidates
    # it evaluated, proving the drop happens at persistence time, not
    # inside the discrimination cascade itself.
    summary = target.asteroid_recovery_quality_summary
    assert summary.asteroid_recovery_metrics.candidates_detected == 2
    assert summary.asteroid_recovery_metrics.candidates_rate_linearity_confirmed == 1
    assert result["candidates"] == target.asteroid_candidates


def _grid_star_positions(count, spacing=30.0, margin=30.0, per_row=6):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Well-separated star pixel positions on a grid, generous clearance.

    Returns
    -------
    positions : `list` [`tuple` [`float`, `float`]]
        Each star's `(x, y)` pixel position.
    """
    return [
        (margin + (index % per_row) * spacing, margin + (index // per_row) * spacing)
        for index in range(count)
    ]


def _write_photometry_frame_fits(path, star_pixel_positions, date_obs):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Write a synthetic light frame FITS file with many Gaussian sources."""
    rng = np.random.default_rng(0)
    shape = (256, 256)
    data = rng.normal(100.0, 5.0, shape).astype(np.float32)
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    for star_x, star_y in star_pixel_positions:
        data += Gaussian2D(5000.0, star_x, star_y, 2.0, 2.0)(xx, yy)
    header = fits.Header()
    header["DATE-OBS"] = date_obs
    fits.PrimaryHDU(data.astype(np.float32), header=header).writeto(path, overwrite=True)


def test_target_analyze_target_photometry_runs_each_session_independently(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify photometry splits multi-session frames, not cross-tracking.

    Pixel-position re-centroiding against a single reference frame only
    holds within one observing session. Session A's frames place 12
    stars at one set of pixel positions; session B's frames (a
    different calendar night) place 18 stars at a different set of
    positions.

    Rather than asserting a literal star count (DAOStarFinder can pick
    up a handful of noise peaks alongside the injected sources, making
    an exact number fragile), this compares against ground truth
    obtained by running each session's frames through a standalone
    `VariabilityAnalyzer` directly -- the same detection logic
    `analyze_target` itself uses -- and also against what a single
    combined reference-frame run across all 4 frames would find. If
    sessions were incorrectly merged into one analysis (the pre-fix
    bug), `analyze_target`'s result would match the combined-run count,
    not the sum of the two independent per-session counts.
    """
    monkeypatch.setenv("ASTROMETRICS_CONFIG_PATH", str(tmp_path / "astrometrics.config"))
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(tmp_path)}})
    butler = DiskButler(config=config)

    session_a_positions = _grid_star_positions(12)
    session_b_positions = _grid_star_positions(18)

    import datetime as datetime_module

    session_a_base = datetime_module.datetime(2026, 1, 1, 22, 0, 0)
    session_b_base = datetime_module.datetime(2026, 1, 5, 22, 0, 0)

    session_paths = [[], []]
    frames = []
    for session_index, (base_datetime, positions) in enumerate([
        (session_a_base, session_a_positions),
        (session_b_base, session_b_positions),
    ]):
        for frame_index in range(2):
            frame_datetime = base_datetime + datetime_module.timedelta(minutes=5 * frame_index)
            frame_path = tmp_path / f"session{session_index}_frame{frame_index}.fits"
            _write_photometry_frame_fits(frame_path, positions, frame_datetime.isoformat())
            session_paths[session_index].append(str(frame_path))
            frames.append(
                FrameRecord(
                    path=str(frame_path),
                    role="LIGHT",
                    timestamp=frame_datetime.timestamp(),
                )
            )

    from astrometricslib.tasks.stellar_tasks.photometry_tasks.variability_analyzer import (
        VariabilityAnalyzer,
    )

    expected_independent_total = 0
    for paths in session_paths:
        standalone_analyzer = VariabilityAnalyzer()
        standalone_analyzer.process(list(paths))
        expected_independent_total += len(standalone_analyzer.stellar_objects)

    combined_analyzer = VariabilityAnalyzer()
    combined_analyzer.process(session_paths[0] + session_paths[1])
    combined_reference_frame_total = len(combined_analyzer.stellar_objects)

    target = Target(id="PhotometrySessionSplitTestTarget", frames=frames)

    result = pipeline_tasks.analyze_target(
        target, pipeline_type="photometry", butler=butler, use_astrometry_seed=False
    )

    assert result["status"] == "completed"
    assert result["starsProcessed"] == expected_independent_total
    assert result["starsFound"] == expected_independent_total
    # The load-bearing contrast: a single shared reference frame across
    # both sessions finds a different total than two independent
    # per-session reference frames do, so matching the latter (not the
    # former) proves genuine per-session splitting happened.
    assert result["starsProcessed"] != combined_reference_frame_total

    summary = target.photometry_quality_summary
    assert summary is not None
    assert len(summary.target_session_ids) == 2
    frames_contributed = sorted(c.frames_contributed for c in summary.target_session_breakdown)
    assert frames_contributed == [2, 2]

    # Ids are session-prefixed once there's more than one session, so
    # stars from the two sessions can never collide in the persisted
    # stellar_catalog.
    persisted_stars = butler.get("stellar_catalog", {}) or []
    session_scoped_ids = {
        star.id for star in persisted_stars if star.id.startswith("PhotometrySessionSplitTestTarget:")
    }
    assert len(session_scoped_ids) == expected_independent_total
    assert all(":Star_" in star_id for star_id in session_scoped_ids)


def test_target_analyze_target_photometry_with_astrometry_seed_uses_identified_stars(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify use_astrometry_seed=True threads identify_session_stars output.

    Bypasses real plate-solving/SIMBAD by monkeypatching
    identify_session_stars to return a canned identification result,
    so this test exercises the wiring (seeded stars flow through
    VariabilityAnalyzer, quality metrics get populated, the persisted
    star carries its real astrometry-derived id) without depending on
    solve-field or a network SIMBAD query.
    """
    monkeypatch.setenv("ASTROMETRICS_CONFIG_PATH", str(tmp_path / "astrometrics.config"))
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(tmp_path)}})
    butler = DiskButler(config=config)

    positions = _grid_star_positions(5)

    import datetime as datetime_module

    frame_datetime = datetime_module.datetime(2026, 1, 1, 22, 0, 0)
    frame_path = tmp_path / "frame0.fits"
    _write_photometry_frame_fits(frame_path, positions, frame_datetime.isoformat())
    frames = [FrameRecord(path=str(frame_path), role="LIGHT", timestamp=frame_datetime.timestamp())]

    seed_star = StellarObject(id="* alf Lyr", name="Vega")
    seed_star.star_data = {"xcentroid": positions[0][0], "ycentroid": positions[0][1]}
    seed_star.right_ascension = 279.23473479
    seed_star.declination = 38.78368896
    seed_star.spectral_type = "A0Va"

    from astrometricslib.tasks.stellar_tasks.astrometry_tasks import session_identification

    fake_wcs = object()

    def _fake_identify_session_stars(  # ruff: ignore[missing-return-type-private-function]
        reference_image,  # ruff: ignore[missing-type-function-argument]
        star_identifier,  # ruff: ignore[missing-type-function-argument]
        center_ra=None,  # ruff: ignore[missing-type-function-argument]
        center_dec=None,  # ruff: ignore[missing-type-function-argument]
        **_kw,  # ruff: ignore[missing-type-kwargs]
    ):
        return session_identification.SessionIdentificationResult(
            wcs=fake_wcs,
            stellar_objects=[seed_star],
            reused_existing_header_wcs=True,
            solve_attempted=False,
            plate_solve_succeeded=True,
            simbad_matched_count=1,
            sources_detected=1,
        )

    monkeypatch.setattr(session_identification, "identify_session_stars", _fake_identify_session_stars)

    target = Target(id="PhotometryAstrometrySeedTestTarget", frames=frames)

    result = pipeline_tasks.analyze_target(
        target, pipeline_type="photometry", butler=butler, use_astrometry_seed=True
    )

    assert result["status"] == "completed"
    assert result["starsProcessed"] == 1

    persisted_stars = butler.get("stellar_catalog", {}) or []
    matching = [star for star in persisted_stars if star.id == "* alf Lyr"]
    assert len(matching) == 1
    assert matching[0].name == "Vega"

    summary = target.photometry_quality_summary
    assert summary is not None
    assert summary.photometry_metrics.astrometry_identified_star_count == 1
    assert summary.photometry_metrics.sessions_with_reused_header_wcs == summary.target_session_ids


def test_target_analyze_target_photometry_without_astrometry_seed_uses_synthetic_ids(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify passing use_astrometry_seed=False uses synthetic IDs."""
    monkeypatch.setenv("ASTROMETRICS_CONFIG_PATH", str(tmp_path / "astrometrics.config"))
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(tmp_path)}})
    butler = DiskButler(config=config)

    positions = _grid_star_positions(5)

    import datetime as datetime_module

    frame_datetime = datetime_module.datetime(2026, 1, 1, 22, 0, 0)
    frame_path = tmp_path / "frame0.fits"
    _write_photometry_frame_fits(frame_path, positions, frame_datetime.isoformat())
    frames = [FrameRecord(path=str(frame_path), role="LIGHT", timestamp=frame_datetime.timestamp())]

    target = Target(id="PhotometryNoSeedTestTarget", frames=frames)

    result = pipeline_tasks.analyze_target(
        target, pipeline_type="photometry", butler=butler, use_astrometry_seed=False
    )

    assert result["status"] == "completed"
    summary = target.photometry_quality_summary
    assert summary.photometry_metrics.astrometry_identified_star_count == 0
    assert summary.photometry_metrics.sessions_with_reused_header_wcs == []

    persisted_stars = butler.get("stellar_catalog", {}) or []
    assert all(star.id.startswith("Star_") for star in persisted_stars)


class _FakeLinearWcs:
    """A trivial linear pixel->world stand-in for a real plate solve.

    ra = ra_offset + x*scale_deg_per_px; dec = dec_offset + y*scale_deg_per_px
    Precise enough for unit tests that only need controllable, known sky
    positions -- not a real WCS, just duck-types `wcs_pix2world` the way
    `pipeline_tasks._stars_to_sky` calls it.
    """

    def __init__(self, ra_offset: float, dec_offset: float, scale_deg_per_px: float = 0.0001):  # ruff: ignore[missing-return-type-special-method]
        self.ra_offset = ra_offset
        self.dec_offset = dec_offset
        self.scale_deg_per_px = scale_deg_per_px

    def wcs_pix2world(self, x_array, y_array, _origin):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        ra = self.ra_offset + np.array(x_array) * self.scale_deg_per_px
        dec = self.dec_offset + np.array(y_array) * self.scale_deg_per_px
        return ra, dec


def _make_matching_test_star(star_id: str, pixel_x: float, pixel_y: float) -> StellarObject:
    """Build a minimal StellarObject with a pixel position and light curve.

    Enough for `_match_and_merge_across_sessions`/
    `_rescale_and_merge_light_curve` to operate on without needing a
    real VariabilityAnalyzer run.

    Returns
    -------
    star : `StellarObject`
        The constructed test star.
    """
    from datetime import datetime, timedelta

    from astrometricslib.models.stellar_source import LightCurve

    t0 = datetime(2026, 1, 1)
    timestamps = [t0 + timedelta(minutes=5 * i) for i in range(5)]
    fluxes = [100.0, 101.0, 99.0, 100.0, 102.0]
    star = StellarObject(id=star_id)
    star.star_data = {"xcentroid": pixel_x, "ycentroid": pixel_y}
    star.light_curve = LightCurve(
        timestamps=timestamps,
        fluxes=fluxes,
        fluxes_normalized=fluxes,
        fluxes_detrended=fluxes,
        airmasses=[1.1] * 5,
        is_saturated=[False] * 5,
    )
    return star


def _make_test_session(session_index: int) -> object:
    from datetime import date as date_cls

    from astrometricslib.tasks.target_tasks.target_session_tasks import TargetSession

    return TargetSession(
        id=f"MatchTestTarget:session{session_index}",
        target_id="MatchTestTarget",
        night_date=date_cls(2026, 1, 1 + session_index),
        gain="800",
        offset="0",
        frame_paths=[f"session{session_index}_frame0.fits"],
    )


def test_match_and_merge_across_sessions_merges_matching_stars(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a star detected in two sessions merges into one canonical entry.

    Session A's star at pixel (10, 10) and session B's star at pixel
    (30, 30) are given WCS solutions that place them at the identical
    sky position -- they must merge into one canonical StellarObject
    (keeping session A's id, since it's chronologically first),
    recording a `StellarSessionMatch` for the contributing session.
    Session A's second star and session B's second star sit far from
    everything and must remain standalone.
    """
    session_a = _make_test_session(0)
    session_b = _make_test_session(1)

    star_a1 = _make_matching_test_star(f"{session_a.id}:Star_1", 10.0, 10.0)
    star_a2 = _make_matching_test_star(f"{session_a.id}:Star_2", 500.0, 500.0)
    star_b1 = _make_matching_test_star(f"{session_b.id}:Star_1", 30.0, 30.0)
    star_b2 = _make_matching_test_star(f"{session_b.id}:Star_2", 900.0, 900.0)

    # session B's WCS is chosen so its star at (30, 30) lands exactly on
    # session A's star-1 sky position, while both sessions' second stars
    # land far away from anything.
    wcs_a = _FakeLinearWcs(ra_offset=100.0, dec_offset=20.0)
    wcs_b = _FakeLinearWcs(
        ra_offset=100.0 + 10 * 0.0001 - 30 * 0.0001, dec_offset=20.0 + 10 * 0.0001 - 30 * 0.0001
    )

    mocker.patch(
        "astrometricslib.tasks.target_tasks.pipeline_tasks._solve_session_wcs",
        side_effect=[wcs_a, wcs_b],
    )

    from astrometricslib.tasks.target_tasks.pipeline_tasks import _match_and_merge_across_sessions

    target = Target(id="MatchTestTarget")
    per_session_results = [
        (SimpleNamespace(stellar_objects=[star_a1, star_a2]), []),
        (SimpleNamespace(stellar_objects=[star_b1, star_b2]), []),
    ]

    merged, sessions_missing_wcs, match_count = _match_and_merge_across_sessions(
        [session_a, session_b], per_session_results, target
    )

    assert sessions_missing_wcs == []
    assert match_count == 1
    assert len(merged) == 3

    merged_by_id = {star.id: star for star in merged}
    assert star_a1.id in merged_by_id
    assert star_b1.id not in merged_by_id  # folded into star_a1, not persisted separately
    assert star_a2.id in merged_by_id
    assert star_b2.id in merged_by_id

    merged_star = merged_by_id[star_a1.id]
    assert len(merged_star.session_matches) == 1
    assert merged_star.session_matches[0].session_id == session_b.id
    assert len(merged_star.light_curve.timestamps) == 10  # 5 from each session


def test_match_and_merge_across_sessions_reuses_pre_resolved_wcs_without_re_solving(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify session_wcs_map entry is used as-is.

    identify_session_stars (called upstream, per-session, by
    _run_variability_analysis_for_session when use_astrometry_seed is
    True) already resolves each session's WCS -- re-solving it again
    here would be a redundant, wasted plate-solve of the same
    reference frame.
    """
    session_a = _make_test_session(0)
    session_b = _make_test_session(1)

    star_a1 = _make_matching_test_star(f"{session_a.id}:Star_1", 10.0, 10.0)
    star_b1 = _make_matching_test_star(f"{session_b.id}:Star_1", 30.0, 30.0)

    wcs_a = _FakeLinearWcs(ra_offset=100.0, dec_offset=20.0)
    wcs_b = _FakeLinearWcs(
        ra_offset=100.0 + 10 * 0.0001 - 30 * 0.0001, dec_offset=20.0 + 10 * 0.0001 - 30 * 0.0001
    )

    solve_spy = mocker.patch("astrometricslib.tasks.target_tasks.pipeline_tasks._solve_session_wcs")

    from astrometricslib.tasks.target_tasks.pipeline_tasks import _match_and_merge_across_sessions

    target = Target(id="MatchTestTarget")
    per_session_results = [
        (SimpleNamespace(stellar_objects=[star_a1]), []),
        (SimpleNamespace(stellar_objects=[star_b1]), []),
    ]

    merged, sessions_missing_wcs, match_count = _match_and_merge_across_sessions(
        [session_a, session_b],
        per_session_results,
        target,
        session_wcs_map={session_a.id: wcs_a, session_b.id: wcs_b},
    )

    solve_spy.assert_not_called()
    assert sessions_missing_wcs == []
    assert match_count == 1
    assert len(merged) == 1


def test_match_and_merge_across_sessions_falls_back_to_solving_when_session_absent_from_map(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A session missing from session_wcs_map still solves the old way."""
    session_a = _make_test_session(0)
    star_a1 = _make_matching_test_star(f"{session_a.id}:Star_1", 10.0, 10.0)
    wcs_a = _FakeLinearWcs(ra_offset=100.0, dec_offset=20.0)

    solve_spy = mocker.patch(
        "astrometricslib.tasks.target_tasks.pipeline_tasks._solve_session_wcs", return_value=wcs_a
    )

    from astrometricslib.tasks.target_tasks.pipeline_tasks import _match_and_merge_across_sessions

    target = Target(id="MatchTestTarget")
    per_session_results = [(SimpleNamespace(stellar_objects=[star_a1]), [])]

    _match_and_merge_across_sessions([session_a], per_session_results, target, session_wcs_map={})

    solve_spy.assert_called_once_with(session_a, target)


def test_match_and_merge_across_sessions_avoids_double_assignment_when_ambiguous(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a canonical star only absorbs its nearest session-star match.

    One canonical star (from session A) and two session-B stars both
    fall within the matching tolerance of it -- a naive "first match
    within tolerance wins" per-star loop could merge both B stars into
    the same canonical entry, corrupting its light curve with two
    unrelated stars' data. The nearer B star must win; the farther one
    must become its own standalone canonical entry instead.
    """
    session_a = _make_test_session(0)
    session_b = _make_test_session(1)

    star_a1 = _make_matching_test_star(f"{session_a.id}:Star_1", 0.0, 0.0)
    # Both B stars project to within 5" of star_a1 at dec=20 with this
    # scale, but star_b1 (closer) must win over star_b2 (farther).
    star_b1 = _make_matching_test_star(f"{session_b.id}:Star_1", 1.0, 0.0)
    star_b2 = _make_matching_test_star(f"{session_b.id}:Star_2", 2.0, 0.0)

    wcs_a = _FakeLinearWcs(ra_offset=100.0, dec_offset=20.0, scale_deg_per_px=0.0000003)
    wcs_b = _FakeLinearWcs(ra_offset=100.0, dec_offset=20.0, scale_deg_per_px=0.0000003)

    mocker.patch(
        "astrometricslib.tasks.target_tasks.pipeline_tasks._solve_session_wcs",
        side_effect=[wcs_a, wcs_b],
    )

    from astrometricslib.tasks.target_tasks.pipeline_tasks import _match_and_merge_across_sessions

    target = Target(id="MatchTestTarget")
    per_session_results = [
        (SimpleNamespace(stellar_objects=[star_a1]), []),
        (SimpleNamespace(stellar_objects=[star_b1, star_b2]), []),
    ]

    merged, sessions_missing_wcs, match_count = _match_and_merge_across_sessions(
        [session_a, session_b], per_session_results, target, tolerance_arcsec=5.0
    )

    assert sessions_missing_wcs == []
    assert match_count == 1
    assert len(merged) == 2  # star_a1 (absorbed star_b1) + star_b2 standalone

    merged_star_a1 = next(star for star in merged if star.id == star_a1.id)
    assert len(merged_star_a1.session_matches) == 1
    assert len(merged_star_a1.light_curve.timestamps) == 10  # merged with star_b1, not star_b2

    assert any(star.id == star_b2.id for star in merged)


def test_solve_session_wcs_failure_does_not_abort_other_sessions(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify one session's plate-solve failure only excludes that session.

    `PlateSolver.solve()` returns `None` on an ordinary solve failure
    (no matching field), but a genuinely unexpected exception (a config
    error, a malformed header, etc.) must also be caught rather than
    propagating and taking down every other, otherwise-independent
    session's photometry results too.
    """
    session_a = _make_test_session(0)
    session_b = _make_test_session(1)

    from astrometricslib.tasks.target_tasks.pipeline_tasks import _solve_session_wcs

    target = Target(id="MatchTestTarget")

    mocker.patch(
        "astrometricslib.tasks.stellar_tasks.astrometry_tasks.plate_solver.PlateSolver.solve",
        return_value=None,
    )
    result_a = _solve_session_wcs(session_a, target)
    assert result_a is None  # ordinary solve failure -- no exception at all

    mocker.patch(
        "astrometricslib.tasks.stellar_tasks.astrometry_tasks.plate_solver.PlateSolver.solve",
        side_effect=RuntimeError("unexpected local solver failure"),
    )
    result_b = _solve_session_wcs(session_b, target)
    assert result_b is None  # caught, not raised -- the caller stays alive


def test_rescale_and_merge_light_curve_removes_inter_session_step_change():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify merging rescales each session to a shared baseline first.

    Two sessions normalized against different local comparison-star
    ensembles can carry a different absolute flux scale even for a
    genuinely non-variable star. Naively concatenating them would
    inject a step-change at the session boundary that reads as
    variability; rescaling the incoming segment to the canonical
    segment's own median removes it.
    """
    from datetime import datetime, timedelta

    from astrometricslib.models.stellar_source import LightCurve
    from astrometricslib.tasks.target_tasks.pipeline_tasks import _rescale_and_merge_light_curve

    t0 = datetime(2026, 1, 1)
    canonical = LightCurve(
        timestamps=[t0 + timedelta(minutes=i) for i in range(5)],
        fluxes=[100.0] * 5,
        fluxes_normalized=[1.0, 1.01, 0.99, 1.0, 1.02],
        fluxes_detrended=[1.0, 1.01, 0.99, 1.0, 1.02],
        airmasses=[1.1] * 5,
        is_saturated=[False] * 5,
    )
    # A different session, same star, but its own ensemble normalization
    # scales it to roughly 5x the canonical segment's level.
    new_segment = LightCurve(
        timestamps=[t0 + timedelta(days=10, minutes=i) for i in range(5)],
        fluxes=[500.0] * 5,
        fluxes_normalized=[5.0, 5.05, 4.95, 5.0, 5.1],
        fluxes_detrended=[5.0, 5.05, 4.95, 5.0, 5.1],
        airmasses=[1.2] * 5,
        is_saturated=[False] * 5,
    )

    merged = _rescale_and_merge_light_curve(canonical, new_segment)

    assert len(merged.timestamps) == 10
    assert merged.timestamps == sorted(merged.timestamps)

    merged_flux_array = np.array(merged.fluxes_normalized)
    merged_cv = float(np.std(merged_flux_array) / np.mean(merged_flux_array))
    # Un-rescaled, concatenating a ~1.0-level and ~5.0-level segment
    # would give a coefficient of variation approaching 0.5-1.0 (a huge,
    # spurious "variability" signal); after rescaling it should read
    # close to each segment's own genuine ~1-2% scatter.
    assert merged_cv < 0.05
