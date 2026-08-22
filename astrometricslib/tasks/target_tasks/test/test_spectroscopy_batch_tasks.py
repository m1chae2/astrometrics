"""Purpose: Unit tests for tasks/target_tasks/spectroscopy_batch_tasks.py.

Description: Verifies the session-grouped spectroscopy processing path
(process_spectroscopy_frames_by_session and its worker/helpers) --
frame-pixel projection, per-frame WCS resolution priority (own header
over session-level over fallback), persistence, and session-level
orchestration -- without depending on solve-field or real
multiprocessing dispatch.
"""

from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits
from astropy.modeling.models import Gaussian2D
from astropy.wcs import WCS

from astrometricslib.data_access.butler import DiskButler
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.tasks.target_tasks import spectroscopy_batch_tasks
from astrometricslib.utilities import config_loader, parallel_batch
from astrometricslib.utilities.config_loader import AppConfiguration


@pytest.fixture
def isolated_config(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Swap the process-wide config singleton for a fresh, tmp_path-scoped one.

    `_process_single_spectroscopy_frame_worker_v2` constructs its own
    `Astrometrics()` with no arguments (mirroring the real subprocess
    worker's self-contained-process design), which resolves config via
    `get_configuration()`'s process-wide singleton -- fine for a real,
    separate OS process, but means calling the worker function
    in-process (as these unit tests do, to avoid real multiprocessing
    dispatch) would otherwise silently reuse whatever config singleton
    an earlier test in the same pytest run already cached, pointing at
    a different (possibly non-existent, possibly some other test's)
    library path.

    Yields
    ------
    config : `AppConfiguration`
        The fresh, tmp_path-scoped configuration now installed as the
        process-wide singleton for the duration of the test.
    """
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(tmp_path)}})
    original_instance = config_loader._instance
    config_loader._instance = config
    yield config
    config_loader._instance = original_instance


def _make_wcs_header(ra_center=279.0, dec_center=38.0, scale_deg_per_px=0.0001):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    w = WCS(naxis=2)
    w.wcs.crpix = [128, 128]
    w.wcs.cdelt = [-scale_deg_per_px, scale_deg_per_px]
    w.wcs.crval = [ra_center, dec_center]
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    return w.to_header()


def _write_frame_fits(path, date_obs, with_own_wcs=False):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    rng = np.random.default_rng(0)
    shape = (256, 256)
    data = rng.normal(100.0, 5.0, shape).astype(np.float32)
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    data += Gaussian2D(5000.0, 128.0, 128.0, 2.0, 2.0)(xx, yy)
    header = fits.Header()
    header["DATE-OBS"] = date_obs
    if with_own_wcs:
        header.update(_make_wcs_header())
    fits.PrimaryHDU(data.astype(np.float32), header=header).writeto(path, overwrite=True)


def _make_seed_star(star_id="* alf Lyr", name="Vega", ra=279.0, dec=38.0):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    star = StellarObject(id=star_id, name=name)
    star.right_ascension = ra
    star.declination = dec
    star.spectral_type = "A0Va"
    star.star_data = {"xcentroid": 128.0, "ycentroid": 128.0}
    return star


class TestProjectSessionStarsToFramePixels:
    """Unit tests for _project_session_stars_to_frame_pixels."""

    def test_projects_stars_with_known_sky_position(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify a star with a sky position gets a pixel centroid."""
        wcs = WCS(_make_wcs_header())
        star = _make_seed_star()

        projected = spectroscopy_batch_tasks._project_session_stars_to_frame_pixels([star], wcs)

        assert len(projected) == 1
        assert projected[0] is not star  # deep copy, not the original
        assert projected[0].id == star.id
        assert "xcentroid" in projected[0].star_data
        assert "ycentroid" in projected[0].star_data

    def test_skips_stars_with_no_sky_position(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify a star with no ra/dec is silently skipped."""
        wcs = WCS(_make_wcs_header())
        star = StellarObject(id="Unidentified")
        star.star_data = {}

        projected = spectroscopy_batch_tasks._project_session_stars_to_frame_pixels([star], wcs)

        assert projected == []

    def test_mutation_of_projected_star_does_not_leak_back(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify mutating a projected copy never mutates the session star."""
        wcs = WCS(_make_wcs_header())
        star = _make_seed_star()

        projected = spectroscopy_batch_tasks._project_session_stars_to_frame_pixels([star], wcs)
        projected[0].star_data["xcentroid"] = 999.0

        assert star.star_data["xcentroid"] == pytest.approx(128.0)


class TestMergeBatchSummaries:
    """Unit tests for _merge_batch_summaries."""

    def test_merges_succeeded_failed_and_results(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify multiple sessions' summaries concatenate into one."""
        from astrometricslib.utilities import parallel_batch

        summary_a = parallel_batch.BatchRunSummary(
            succeeded=["a.fits"], failed=[], results={"a.fits": {"status": "success"}}
        )
        summary_b = parallel_batch.BatchRunSummary(
            succeeded=[], failed=[("b.fits", "boom")], results={"b.fits": {"status": "failed"}}
        )

        merged = spectroscopy_batch_tasks._merge_batch_summaries([summary_a, summary_b])

        assert merged.succeeded == ["a.fits"]
        assert merged.failed == [("b.fits", "boom")]
        assert merged.results == {"a.fits": {"status": "success"}, "b.fits": {"status": "failed"}}


class TestProcessSingleSpectroscopyFrameWorkerV2:
    """Unit tests for _process_single_spectroscopy_frame_worker_v2."""

    def test_prefers_frame_own_header_wcs_over_session_wcs(self, tmp_path, isolated_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify a frame's own header WCS wins over the session's WCS."""
        config = isolated_config
        frame_path = tmp_path / "frame.fits"
        _write_frame_fits(frame_path, "2026-01-01T00:00:00", with_own_wcs=True)

        seed_star = _make_seed_star()
        # A deliberately different session WCS -- if the frame's own
        # header WCS wasn't actually preferred, projection would use
        # this wildly different center instead.
        wrong_session_header = _make_wcs_header(ra_center=10.0, dec_center=-10.0)

        result = spectroscopy_batch_tasks._process_single_spectroscopy_frame_worker_v2(
            str(frame_path), "WorkerTestTarget", [seed_star], wrong_session_header
        )

        assert result["status"] == "success"
        assert result["stars_processed"] == 1

        butler = DiskButler(config=config)
        persisted = butler.get("stellar_catalog", {}) or []
        assert any(star.id == "* alf Lyr" for star in persisted)

    def test_uses_session_wcs_when_frame_has_no_own_header_wcs(self, tmp_path, isolated_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify the session WCS is used when a frame has none of its own."""
        frame_path = tmp_path / "frame.fits"
        _write_frame_fits(frame_path, "2026-01-01T00:00:00", with_own_wcs=False)

        seed_star = _make_seed_star()
        session_header = _make_wcs_header()

        result = spectroscopy_batch_tasks._process_single_spectroscopy_frame_worker_v2(
            str(frame_path), "WorkerTestTarget2", [seed_star], session_header
        )

        assert result["status"] == "success"
        assert result["stars_processed"] == 1

    def test_falls_back_to_independent_analysis_when_no_seed_stars(self, tmp_path, isolated_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify an empty seed list falls back to the independent flow."""
        frame_path = tmp_path / "frame.fits"
        _write_frame_fits(frame_path, "2026-01-01T00:00:00")

        from astrometricslib import Astrometrics

        astrometrics = Astrometrics()
        astrometrics.targets.create("WorkerFallbackTestTarget")
        astrometrics.targets.save()

        result = spectroscopy_batch_tasks._process_single_spectroscopy_frame_worker_v2(
            str(frame_path), "WorkerFallbackTestTarget", [], None
        )

        assert result["status"] == "success"
        assert result["dispersion_angles"] == []

    def test_falls_back_when_no_wcs_available_at_all(self, tmp_path, isolated_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify no usable WCS anywhere falls back to the independent flow."""
        frame_path = tmp_path / "frame.fits"
        _write_frame_fits(frame_path, "2026-01-01T00:00:00", with_own_wcs=False)

        from astrometricslib import Astrometrics

        astrometrics = Astrometrics()
        astrometrics.targets.create("WorkerNoWcsFallbackTarget")
        astrometrics.targets.save()

        seed_star = _make_seed_star()

        result = spectroscopy_batch_tasks._process_single_spectroscopy_frame_worker_v2(
            str(frame_path), "WorkerNoWcsFallbackTarget", [seed_star], None
        )

        assert result["status"] == "success"
        # Fallback path doesn't track per-star quality inputs.
        assert result["dispersion_angles"] == []


class TestProcessSpectroscopyFramesBySession:
    """Unit tests for process_spectroscopy_frames_by_session."""

    def test_identifies_once_per_session_and_dispatches_that_sessions_frames(self, tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify each session identifies once, dispatches its own frames."""
        monkeypatch.setenv("ASTROMETRICS_CONFIG_PATH", str(tmp_path / "astrometrics.config"))
        config = AppConfiguration()
        config.update_config({"Image Library": {"path": str(tmp_path)}})

        import datetime as datetime_module

        session_a_base = datetime_module.datetime(2026, 1, 1, 22, 0, 0)
        session_b_base = datetime_module.datetime(2026, 1, 5, 22, 0, 0)

        frame_records = []
        for session_index, base_datetime in enumerate([session_a_base, session_b_base]):
            for frame_index in range(2):
                frame_datetime = base_datetime + datetime_module.timedelta(minutes=5 * frame_index)
                frame_path = tmp_path / f"session{session_index}_frame{frame_index}.fits"
                _write_frame_fits(frame_path, frame_datetime.isoformat())
                frame_records.append(
                    FrameRecord(path=str(frame_path), role="LIGHT", timestamp=frame_datetime.timestamp())
                )

        target = Target(id="SessionGroupingTestTarget", frames=frame_records)

        from astrometricslib.tasks.stellar_tasks.astrometry_tasks import session_identification

        identify_calls = []

        def _fake_identify_session_stars(  # ruff: ignore[missing-return-type-private-function]
            reference_image,  # ruff: ignore[missing-type-function-argument]
            star_identifier,  # ruff: ignore[missing-type-function-argument]
            center_ra=None,  # ruff: ignore[missing-type-function-argument]
            center_dec=None,  # ruff: ignore[missing-type-function-argument]
            **_kw,  # ruff: ignore[missing-type-kwargs]
        ):
            identify_calls.append(reference_image.path)
            return session_identification.SessionIdentificationResult(
                wcs=None,
                stellar_objects=[],
                reused_existing_header_wcs=False,
                solve_attempted=True,
                plate_solve_succeeded=False,
                simbad_matched_count=0,
                sources_detected=0,
            )

        monkeypatch.setattr(session_identification, "identify_session_stars", _fake_identify_session_stars)

        dispatched_batches = []

        def _fake_run_parallel_batch(item_ids, worker_function, worker_arguments=(), **_kw):  # ruff: ignore[missing-type-function-argument, missing-type-kwargs, missing-return-type-private-function]
            from astrometricslib.utilities import parallel_batch

            dispatched_batches.append((list(item_ids), worker_arguments))
            summary = parallel_batch.BatchRunSummary()
            for item_id in item_ids:
                summary.succeeded.append(item_id)
                summary.results[item_id] = {"status": "success", "stars_processed": 0}
            return summary

        from astrometricslib.utilities import parallel_batch as parallel_batch_module

        monkeypatch.setattr(parallel_batch_module, "run_parallel_batch", _fake_run_parallel_batch)

        fake_api = SimpleNamespace(config=config)

        summary, session_results = spectroscopy_batch_tasks.process_spectroscopy_frames_by_session(
            fake_api, target, frame_records
        )

        assert len(identify_calls) == 2
        assert len(session_results) == 2
        assert len(dispatched_batches) == 2
        dispatched_paths = [set(paths) for paths, _args in dispatched_batches]
        assert {
            str(tmp_path / "session0_frame0.fits"),
            str(tmp_path / "session0_frame1.fits"),
        } in dispatched_paths
        assert {
            str(tmp_path / "session1_frame0.fits"),
            str(tmp_path / "session1_frame1.fits"),
        } in dispatched_paths
        assert len(summary.succeeded) == 4

    def test_excludes_frames_with_no_timestamp(self, tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify a frame with no timestamp is excluded from sessions."""
        monkeypatch.setenv("ASTROMETRICS_CONFIG_PATH", str(tmp_path / "astrometrics.config"))
        config = AppConfiguration()
        config.update_config({"Image Library": {"path": str(tmp_path)}})

        frame_path = tmp_path / "no_timestamp.fits"
        _write_frame_fits(frame_path, "2026-01-01T00:00:00")
        frame_records = [FrameRecord(path=str(frame_path), role="LIGHT", timestamp=None)]

        target = Target(id="NoTimestampTestTarget", frames=frame_records)

        from astrometricslib.tasks.stellar_tasks.astrometry_tasks import session_identification

        monkeypatch.setattr(
            session_identification,
            "identify_session_stars",
            lambda *a, **k: pytest.fail("should never be called: no sessions to identify"),
        )

        fake_api = SimpleNamespace(config=config)

        summary, session_results = spectroscopy_batch_tasks.process_spectroscopy_frames_by_session(
            fake_api, target, frame_records
        )

        assert session_results == []
        assert summary.succeeded == []


def _make_session(session_id, frame_paths):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    return SimpleNamespace(id=session_id, frame_paths=frame_paths)


class TestAttachSpectroscopyQualitySummary:
    """Unit tests for _attach_spectroscopy_quality_summary.

    Ported from backend/tests/test_analysis_orchestrator.py when this
    aggregation moved from AnalysisOrchestrator into astrometricslib
    itself, so `target.spectroscopy_quality_summary` is attached by
    the library run, not built by the backend after the fact.
    """

    def test_aggregates_dispersion_and_trail_width_across_frames(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify per-frame quality inputs aggregate into one summary."""
        target = Target(id="QualitySummaryTestTarget")

        summary = parallel_batch.BatchRunSummary(
            succeeded=["a.fits", "b.fits"],
            failed=[],
            results={
                "a.fits": {
                    "status": "success",
                    "stars_processed": 1,
                    "dispersion_angles": [12.5],
                    "trail_widths": [3.0, 4.0],
                    "zero_order_saturation_fractions": [0.0],
                },
                "b.fits": {
                    "status": "success",
                    "stars_processed": 1,
                    "dispersion_angles": [],
                    "trail_widths": [5.0],
                    "zero_order_saturation_fractions": [0.2],
                },
            },
        )
        session = _make_session("Target:2026-01-01:800:0", ["a.fits", "b.fits"])
        session_results = [(session, SimpleNamespace())]

        spectroscopy_batch_tasks._attach_spectroscopy_quality_summary(target, summary, session_results)

        assert target.spectroscopy_quality_summary is not None
        metrics = target.spectroscopy_quality_summary.spectroscopy_metrics
        assert metrics.dispersion_angle_deg == pytest.approx(12.5)
        assert metrics.trail_width_profile_available is True
        assert metrics.median_trail_width_px == pytest.approx(4.0)
        assert metrics.zero_order_saturated_pixel_fraction == pytest.approx(0.2)

        assert target.spectroscopy_quality_summary.target_session_ids == [session.id]
        breakdown = target.spectroscopy_quality_summary.target_session_breakdown
        assert len(breakdown) == 1
        assert breakdown[0].frames_contributed == 2
        assert breakdown[0].frames_clipped == 0
        assert target.spectroscopy_quality_summary.upstream_quality_summary_reference == "raw_frames"

    def test_flags_target_when_zero_order_saturation_significant(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify significant zero-order saturation flags with a reason."""
        target = Target(id="SaturationFlagTestTarget")

        summary = parallel_batch.BatchRunSummary(
            succeeded=["a.fits"],
            failed=[],
            results={
                "a.fits": {
                    "status": "success",
                    "stars_processed": 1,
                    "dispersion_angles": [10.0],
                    "trail_widths": [],
                    "zero_order_saturation_fractions": [0.9],
                }
            },
        )
        session = _make_session("Target:2026-01-01:800:0", ["a.fits"])
        session_results = [(session, SimpleNamespace())]

        spectroscopy_batch_tasks._attach_spectroscopy_quality_summary(target, summary, session_results)

        assert target.spectroscopy_quality_summary.flagged is True
        assert "zero-order saturated" in target.spectroscopy_quality_summary.flag_reasons[0]

    def test_frames_clipped_counts_failed_paths_per_session(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify frames_clipped only counts that session's own failures."""
        target = Target(id="ClippedFramesTestTarget")

        summary = parallel_batch.BatchRunSummary(
            succeeded=["a.fits"],
            failed=[("b.fits", "boom")],
            results={
                "a.fits": {
                    "status": "success",
                    "stars_processed": 1,
                    "dispersion_angles": [],
                    "trail_widths": [],
                    "zero_order_saturation_fractions": [],
                }
            },
        )
        session_a = _make_session("Target:session_a", ["a.fits", "b.fits"])
        session_b = _make_session("Target:session_b", ["c.fits"])
        session_results = [(session_a, SimpleNamespace()), (session_b, SimpleNamespace())]

        spectroscopy_batch_tasks._attach_spectroscopy_quality_summary(target, summary, session_results)

        breakdown_by_session = {
            contribution.session_id: contribution
            for contribution in target.spectroscopy_quality_summary.target_session_breakdown
        }
        assert breakdown_by_session[session_a.id].frames_contributed == 2
        assert breakdown_by_session[session_a.id].frames_clipped == 1
        assert breakdown_by_session[session_b.id].frames_contributed == 1
        assert breakdown_by_session[session_b.id].frames_clipped == 0

    def test_no_saturation_data_does_not_flag_or_crash(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify an empty run builds a summary without flagging or raising."""
        target = Target(id="NoDataTestTarget")

        summary = parallel_batch.BatchRunSummary(succeeded=[], failed=[], results={})

        spectroscopy_batch_tasks._attach_spectroscopy_quality_summary(target, summary, [])

        assert target.spectroscopy_quality_summary.flagged is False
        assert (
            target.spectroscopy_quality_summary.spectroscopy_metrics.zero_order_saturated_pixel_fraction
            is None
        )
        assert target.spectroscopy_quality_summary.spectroscopy_metrics.median_trail_width_px is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
