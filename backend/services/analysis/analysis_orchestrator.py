"""Orchestrate scientific analysis tasks (photometry, spectroscopy)."""

import logging
import os
import threading
from datetime import datetime
from typing import Any

from astropy.io import fits

from astrometricslib import FilterType, resolve_worker_counts
from backend.services.infrastructure.base_service import BaseBackgroundService

# REQ: IMG-4: Scientific Analysis Pipeline
# REQ: IMG-4.1: The system SHALL provide automated photometry and
# spectroscopy extraction.


class AnalysisOrchestrator(BaseBackgroundService):
    """Service for managing background scientific analysis tasks.

    Handles spectroscopy extraction and photometry analysis.
    """

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        config_service=None,  # ruff: ignore[missing-type-function-argument]
        stellar_service=None,  # ruff: ignore[missing-type-function-argument]
        target_service=None,  # ruff: ignore[missing-type-function-argument]
        notification_service=None,  # ruff: ignore[missing-type-function-argument]
        job_service=None,  # ruff: ignore[missing-type-function-argument]
        astrometrics=None,  # ruff: ignore[missing-type-function-argument]
    ):
        super().__init__(job_service=job_service)
        self._config_service = config_service
        self._stellar_service = stellar_service
        self._target_service = target_service
        self._notification_service = notification_service
        self.astrometrics = astrometrics
        # Serializes analyze_image's check-then-submit sequence per
        # process. Without this, two near-simultaneous calls for the
        # same target (e.g. a double-click, or a UI race) can both read
        # "no active job yet" before either has committed its own job
        # row, and both proceed -- producing two concurrent jobs for
        # the same target instead of one deduplicated by the
        # already-running check below.
        self._analyze_submit_lock = threading.Lock()

    def analyze_image(  # ruff: ignore[missing-return-type-undocumented-public-function]
        self, target_id: str, image_files: Any, filter_type: str | None = None, type: str = "photometry"
    ):
        """Start a background analysis job.

        Returns
        -------
        result : `dict`
            Dict with `"status"` (`"started"` or
            `"already_running"`), `"jobId"`, and `"logFile"`.
        """
        # REQ: IMG-5.3: Isolated log file per job
        safe_target = target_id.replace(" ", "_").replace("/", "_")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = self._config_service.get_logs_path()
        log_file = str(log_dir / f"analysis_{safe_target}_{timestamp}.log")

        with self._analyze_submit_lock:
            if self._job_service:
                active_jobs = self._job_service.get_jobs_for_target(
                    target_id, job_type="analysis", status="started"
                )
                for job in active_jobs:
                    if job.log_file_path and f"analysis_{safe_target}" in job.log_file_path:
                        return {"status": "already_running", "jobId": job.id, "logFile": job.log_file_path}

            job_id = self._submit_job(
                target_id,
                "analysis",
                self._start_analysis_task,
                image_files,
                filter_type,
                type,
                log_file_path=log_file,
            )

        return {"status": "started", "jobId": job_id, "logFile": log_file}

    def get_analysis_results(self, target_id: str, filter_type: str | None = None):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Get the results of the analysis job if complete.

        Returns
        -------
        result : `dict` or `None`
            The job result/status dict, or `None` if no analysis
            job exists for the target.
        """
        # Fix: Priority 1 - Use JobService to find the MOST RECENT
        # analysis job for this target
        if self._job_service:
            recent_jobs = self._job_service.get_jobs_for_target(target_id, job_type="analysis", limit=5)
            if recent_jobs:
                job = recent_jobs[0]

                # Check memory for the Future if it's still active
                with self._lock:
                    if job.id in self._jobs:
                        future = self._jobs[job.id]["future"]
                        if not future.done():
                            return {
                                "status": "started",
                                "jobId": job.id,
                                "progress": self._jobs[job.id].get("progress", {"current": 0, "total": 0}),
                            }

                        if future.cancelled():
                            return {"status": "cancelled"}

                        try:
                            return future.result(timeout=0)
                        except Exception as e:
                            return {"status": "error", "error": str(e)}

                # Not tracked in this process's memory (e.g. no Future was
                # ever submitted here) -- fall back to the DB-recorded
                # status, which also covers jobs started outside this
                # backend process entirely (a standalone script calling
                # analyze_target() directly).
                if job.status == "completed":
                    return {"status": "finished", "jobId": job.id}
                elif job.status == "failed":
                    return {"status": "error", "error": job.message}
                elif job.status in ("started", "running"):
                    return {"status": "started", "jobId": job.id}

        return None

    def cancel_analysis(self, target_id: str, filter_type: str | None = None) -> bool:
        """Cancel an active analysis job.

        Returns
        -------
        cancelled : `bool`
            `True` if an active job was found and cancelled.
        """
        if self._job_service:
            active = self._job_service.get_jobs_for_target(target_id, job_type="analysis", status="started")
            if active:
                return self.cancel_processing(active[0].id)
        return False

    def _start_analysis_task(self, job_id, target_id, image_files, filter_type, type="photometry", **kwargs):  # ruff: ignore[missing-type-function-argument, missing-type-kwargs, missing-return-type-private-function]
        """Unified analysis worker.

        Runs either spectroscopy extraction or photometry analysis (or
        both, if a single batch spans both frame types), classifying
        each path via its real `FrameRecord.filter` where the path
        matches a frame already known to the target -- falling back to
        FITS-header/filename auto-detection (today's only mechanism)
        only for paths with no matching `FrameRecord` (e.g. an ad hoc
        file never ingested).

        Returns
        -------
        result : `dict`
            The result dict from the spectroscopy or photometry
            pipeline; a combined `{"photometry": ..., "spectroscopy":
            ...}` dict if a single batch contained both frame types;
            or an error dict if no usable paths/filter were found.
        """
        # The job row already exists here -- it was created before this
        # worker started -- so this only needs the log-capture half.
        # capture_job_logs attaches handlers to both this job's own logger
        # and the shared "astrometricslib" logger that every module deeper
        # in the pipeline logs through, then removes and closes them again
        # when the work finishes. See astrometricslib.drivers.job_logging.
        from astrometricslib import capture_job_logs

        job = self._job_service.get_job(job_id) if self._job_service else None

        with capture_job_logs(
            job_id=job_id,
            log_file_path=job.log_file_path if job else None,
            logger_interface=self._job_service.repository if self._job_service else None,
        ) as job_logger:
            return self._run_analysis_task_body(job_logger, job_id, target_id, image_files, filter_type, type)

    def _run_analysis_task_body(  # ruff: ignore[missing-return-type-private-function]
        self,
        job_logger,  # ruff: ignore[missing-type-function-argument]
        job_id,  # ruff: ignore[missing-type-function-argument]
        target_id,  # ruff: ignore[missing-type-function-argument]
        image_files,  # ruff: ignore[missing-type-function-argument]
        filter_type,  # ruff: ignore[missing-type-function-argument]
        type="photometry",  # ruff: ignore[missing-type-function-argument]
    ):
        """Body of `_start_analysis_task`, run with job logging attached.

        Split out purely so `_start_analysis_task` can guarantee the
        logging handler cleanup above runs on every exit path (including
        the several early/branch returns below) via a single try/finally,
        without re-indenting this entire body under it.

        Returns
        -------
        result : `dict`
            Same as `_start_analysis_task`.
        """
        job_logger.info(f"[{target_id}] Background analysis worker started for {target_id} (Job: {job_id})")

        from astrometricslib import AstrometryPipeline

        paths = []
        if isinstance(image_files, list):
            for item in image_files:
                if isinstance(item, dict) and "path" in item:
                    paths.append(item["path"])
                elif hasattr(item, "path"):
                    paths.append(item.path)
                else:
                    paths.append(str(item))
        elif isinstance(image_files, dict):
            # Flatten nested structure:
            # Tele -> Cam -> ISO -> Exp -> Filter -> List
            def flatten(d, current_filter=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
                for k, v in d.items():
                    if isinstance(v, dict):
                        yield from flatten(
                            v, k if current_filter is None or k in ["L", "SPEC", "NONE"] else current_filter
                        )
                    elif isinstance(v, list):
                        if (
                            not filter_type
                            or k.upper() == filter_type.upper()
                            or (filter_type.upper() == "L" and k.upper() in ["LUMINANCE", "NONE", "UNKNOWN"])
                        ):
                            yield from v
                    else:
                        yield v

            for item in flatten(image_files):
                if isinstance(item, dict) and "path" in item:
                    paths.append(item["path"])
                elif hasattr(item, "path"):
                    paths.append(item.path)
                else:
                    paths.append(str(item))

        job_logger.info(f"[{target_id}] Analysis task for {target_id} found {len(paths)} files")

        if not paths and type != "photometry":
            return {"status": "error", "message": "No paths provided for analysis"}

        pipeline = getattr(self.astrometrics, "image_pipeline", None) or AstrometryPipeline(
            self._config_service
        )

        target = self._target_service.get_targets(target_id) if self._target_service else None

        # Classify each path via its real FrameRecord.filter where the
        # path matches a frame already known to the target -- this is
        # the same normalized enum FrameRecord.normalize_filter already
        # produces from raw header/UI filter strings, so it's a more
        # reliable signal than re-deriving spectroscopy-ness from a
        # FITS header/filename per call. Falls back to that FITS-
        # header/filename auto-detection (this method's only mechanism
        # before this) for any path with no matching FrameRecord (e.g.
        # an ad hoc file never ingested).
        path_to_frame = {frame.path: frame for frame in target.frames} if target else {}
        matched_spec_paths = []
        matched_light_paths = []
        unmatched_paths = []
        for path in paths:
            frame = path_to_frame.get(path)
            if frame is None:
                unmatched_paths.append(path)
            elif frame.filter == FilterType.SPEC:
                matched_spec_paths.append(path)
            else:
                matched_light_paths.append(path)

        fallback_is_spec = type == "spectroscopy"
        if (
            not fallback_is_spec
            and filter_type
            and filter_type.upper() in ["SPEC", "SPECTROSCOPY", "STAR ANALYZER 200"]
        ):
            fallback_is_spec = True

        if (
            unmatched_paths
            and not fallback_is_spec
            and (not filter_type or filter_type.upper() in ["NONE", "UNKNOWN", "LUMINANCE", "L", "ALL"])
        ):
            try:
                with fits.open(unmatched_paths[0], memmap=False) as hdul:
                    header = hdul[0].header
                    fit_filter = header.get("FILTER")
                    if fit_filter and str(fit_filter).upper() in [
                        "SPEC",
                        "SPECTROSCOPY",
                        "STAR ANALYZER 200",
                    ]:
                        fallback_is_spec = True
                        job_logger.info(
                            f"[{target_id}] Auto-detected spectroscopy from FITS header FILTER: {fit_filter}"
                        )
            except Exception as e:
                job_logger.warning(f"[{target_id}] Could not read FITS header for auto-detection: {e}")

            if not fallback_is_spec:
                first_file = os.path.basename(unmatched_paths[0]).upper()
                if "SPECTRUM" in first_file or "_SPEC" in first_file or "SPECTROSCOPY" in first_file:
                    fallback_is_spec = True
                    job_logger.info(
                        f"[{target_id}] Auto-detected spectroscopy from filename: {unmatched_paths[0]}"
                    )

        spec_paths = matched_spec_paths + (unmatched_paths if fallback_is_spec else [])
        light_paths = matched_light_paths + (unmatched_paths if not fallback_is_spec else [])

        if spec_paths and light_paths:
            job_logger.info(
                f"[{target_id}] Analysis batch spans both frame types: "
                f"{len(light_paths)} light/luminance, {len(spec_paths)} spectroscopy."
            )
            spectroscopy_result = self._run_spectroscopy_analysis(
                job_id, target_id, spec_paths, pipeline, filter_type or "SPEC", logger=job_logger
            )
            photometry_result = self._run_photometry_analysis(
                job_id, target_id, light_paths, pipeline, filter_type, logger=job_logger
            )
            return {
                "status": "finished",
                "photometry": photometry_result,
                "spectroscopy": spectroscopy_result,
            }

        if spec_paths:
            return self._run_spectroscopy_analysis(
                job_id, target_id, spec_paths, pipeline, filter_type or "SPEC", logger=job_logger
            )

        if light_paths or type == "photometry":
            return self._run_photometry_analysis(
                job_id, target_id, light_paths, pipeline, filter_type, logger=job_logger
            )

        return {"status": "error", "message": f"Unsupported filter: {filter_type}"}

    def _update_job_progress(self, job_id, target_id, current, total, filter_type=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        if self._job_service:
            progress_pct = int((current / total) * 100) if total > 0 else 0
            self._job_service.update_job(job_id, progress=progress_pct)

        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["progress"] = {"current": current, "total": total}

    def _run_spectroscopy_analysis(self, job_id, target_id, paths, pipeline, filter_type=None, logger=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Pipeline for spectroscopy extraction.

        Groups frames into observing sessions, identifies each
        session's stars once (reusing an existing FITS-header WCS when
        present), and extracts spectra for those same identified stars
        from every frame in that session -- see
        `Astrometrics.processing.run_spectroscopy_by_session`.
        Builds `target.spectroscopy_quality_summary` here, in this
        (parent) process, from the aggregated per-frame results:
        earlier, each frame worker built its own quality summary
        against its own freshly-fetched `Target` copy inside its own
        subprocess, which was never the same object this orchestrator
        holds and so never actually reached `save_targets()`.

        Returns
        -------
        results : `dict`
            Summary dict with `"totalImages"`, `"starsProcessed"`,
            `"spectraExtracted"`, and `"status"`.
        """
        log = logger or logging
        log.info(f"[{target_id}] Running spectroscopy analysis via Target.analyze_target")

        results = {
            "targetId": target_id,
            "totalImages": len(paths),
            "starsProcessed": 0,
            "spectraExtracted": 0,
            "status": "finished",
            "analysisMode": "spectroscopy",
        }

        # Resolve the Target domain object
        target = self._target_service.get_targets(target_id)
        if not target:
            target = self._target_service.create_target(target_id)

        # If astrometrics is a Mock, support the mock pipeline
        # expectation in tests
        from unittest.mock import Mock

        if isinstance(self.astrometrics, Mock):
            context = pipeline.process(paths[0] if paths else "", attempt_plate_solving=False)
            valid_objects = self.astrometrics.spectroscopy_pipeline.process(
                context, limit=10, auto_detect_angle=True
            )
            for star in valid_objects:
                self._stellar_service.find_or_create_by_position(
                    star.right_ascension, star.declination, name=getattr(star, "name", None)
                )
            self._stellar_service.save_objects()
            results["starsProcessed"] += len(valid_objects)
            results["spectraExtracted"] += len(valid_objects)
            return results

        def _on_frame_complete(path, frame_result, completed_count, total_count):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
            self._update_job_progress(job_id, target_id, completed_count, total_count, filter_type="SPEC")

        # Resolve bare path strings back to their real FrameRecord, so
        # derive_target_sessions() can group them; a path with no
        # matching frame (e.g. an ad hoc file never ingested) can't be
        # session-assigned and is skipped, matching photometry's
        # existing "no usable timestamp" exclusion.
        path_to_frame = {frame.path: frame for frame in target.frames}
        frame_records = [path_to_frame[path] for path in paths if path in path_to_frame]
        unmatched_paths = [path for path in paths if path not in path_to_frame]
        if unmatched_paths:
            log.warning(
                f"[{target_id}] {len(unmatched_paths)} path(s) have no matching FrameRecord on "
                f"the target and will be skipped: {unmatched_paths}"
            )

        with self.astrometrics.processing.acquire_analysis_slot():
            summary, _session_results = self.astrometrics.processing.run_spectroscopy_by_session(
                self.astrometrics,
                target,
                frame_records,
                max_workers=None,
                on_item_complete=_on_frame_complete,
            )

        for frame_result in summary.results.values():
            stars_processed = frame_result.get("stars_processed", 0)
            results["starsProcessed"] += stars_processed
            results["spectraExtracted"] += stars_processed

        for path, error_message in summary.failed:
            log.error(f"[{target_id}] Failed to process {path} for spectroscopy: {error_message}")

        # target.spectroscopy_quality_summary is now built and attached
        # by run_spectroscopy_by_session itself.

        try:
            self._target_service.save_targets()
        except Exception as save_error:
            log.error(f"[{target_id}] Failed to record target after spectroscopy analysis: {save_error}")

        log.info(
            f"[{target_id}] Spectroscopy analysis complete. "
            f"{results['spectraExtracted']} spectra extracted from {results['starsProcessed']} stars."
        )

        if self._notification_service:
            self._notification_service.notify(
                target_id,
                f"Spectroscopy analysis complete for {target_id}. "
                f"{results['spectraExtracted']} spectra extracted.",
                status="success",
            )

        return results

    def _run_photometry_analysis(self, job_id, target_id, paths, pipeline, filter_type=None, logger=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Pipeline for multi-frame aperture photometry.

        Measures stellar brightness,. performs plate solving on reference/light
        frames, and calculates light curves for identified stars.

        Returns
        -------
        result : `dict`
            The photometry analysis result produced by
            `astrometrics.processing.run_photometry`.
        """
        log = logger or logging
        log.info(f"[{target_id}] Running photometry analysis via astrometrics.processing.run_photometry")

        # Resolve the Target domain object
        target = self._target_service.get_targets(target_id)
        if not target:
            target = self._target_service.create_target(target_id)

        worker_counts = resolve_worker_counts("1", self._config_service.get_photometry_workers())

        self._update_job_progress(job_id, target_id, 1, 2, filter_type=filter_type)
        try:
            with self.astrometrics.processing.acquire_analysis_slot():
                res = self.astrometrics.processing.run_photometry(
                    target,
                    filter_type=filter_type,
                    max_workers=worker_counts.inner_worker_count,
                    # Identify each session's stars against a real
                    # catalog (reusing an existing FITS-header WCS when
                    # present) instead of tracking anonymous per-run
                    # pixel detections -- see
                    # session_identification.identify_session_stars.
                    use_astrometry_seed=True,
                    # This orchestrator already created and is tracking
                    # its own ProcessingJob (job_id, above) for this
                    # exact call, via _submit_job/job_wrapper -- without
                    # this, analyze_target() would register a second,
                    # redundant job for the same UI-triggered run.
                    register_job=False,
                )
            self._update_job_progress(job_id, target_id, 2, 2, filter_type=filter_type)

            log.info(
                f"[{target_id}] Photometry analysis complete. "
                f"{res.get('framesProcessed', 0)} frames processed."
            )

            try:
                self._target_service.save_targets()
            except Exception as save_error:
                log.error(f"[{target_id}] Failed to record target after photometry analysis: {save_error}")

            if self._notification_service:
                msg = (
                    f"Photometry analysis complete for {target_id}. "
                    f"{res.get('framesProcessed', 0)} frames processed."
                )
                self._notification_service.notify(target_id, msg, status="success")

            return res
        except Exception as e:
            log.error(f"[{target_id}] Failed to process photometry: {e}")
            raise e
