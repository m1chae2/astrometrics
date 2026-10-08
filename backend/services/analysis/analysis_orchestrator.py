"""Orchestrate scientific analysis tasks (photometry, spectroscopy)."""

import logging
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any

from astropy.io import fits

from astrometricslib import FITS_READ_ERRORS, AstrometricsError, FilterType, InvalidArgumentError
from backend.services.infrastructure.base_service import BaseBackgroundService

logger = logging.getLogger(__name__)

# REQ: IMG-4: Scientific Analysis Pipeline
# REQ: IMG-4.1: The system SHALL provide automated photometry and
# spectroscopy extraction.


# How many stars are measured in the master stacked spectral image. It has
# the best signal-to-noise ratio of any spectral image, so the brightest
# ten stars are enough to set the baseline without spending time on faint
# detections that are mostly noise.
MASTER_STACK_STAR_LIMIT = 10


def _is_same_file(first_path: str, second_path: str) -> bool:
    """Say whether two paths point at the same file.

    Parameters
    ----------
    first_path : `str`
        One path.
    second_path : `str`
        Another path.

    Returns
    -------
    is_same : `bool`
        `True` when the paths are the same once normalized (extra
        slashes and ``..`` removed), or resolve to the same file.
    """
    if os.path.normpath(first_path) == os.path.normpath(second_path):
        return True
    try:
        return os.path.samefile(first_path, second_path)
    except OSError:
        return False


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

    def get_analysis_results(self, target_id: str, filter_type: str | None = None) -> dict[str, Any] | None:
        """Get the results of the analysis job if complete.

        Returns
        -------
        result : `dict` or `None`
            The job result/status dict, or `None` if no analysis
            job exists for the target. A job that failed is reported
            as ``{"status": "failed", "jobId": ..., "error": ...}``,
            where ``error`` is the job's error message. The call itself
            still succeeds, because the job's state is the answer.
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
                            # The job may have raised anything. Its failure
                            # was logged with a traceback when it happened,
                            # so here it is only recorded in the reply.
                            logger.debug("Analysis job %s failed.", job.id, exc_info=True)
                            return {"status": "failed", "jobId": job.id, "error": str(e)}

                # Not tracked in this process's memory (e.g. no Future was
                # ever submitted here) -- fall back to the DB-recorded
                # status, which also covers jobs started outside this
                # backend process entirely (a standalone script calling
                # analyze_target() directly).
                if job.status == "completed":
                    return {"status": "finished", "jobId": job.id}
                elif job.status == "failed":
                    return {"status": "failed", "jobId": job.id, "error": job.message}
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

    def _start_analysis_task(
        self,
        job_id: str,
        target_id: str,
        image_files: Any,
        filter_type: str | None,
        type: str = "photometry",
        **kwargs: Any,
    ) -> dict[str, Any]:
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
            ...}` dict if a single batch contained both frame types.

        Notes
        -----
        The body raises `InvalidArgumentError` if no usable paths or
        filter were found. The job runner then marks the job failed.
        """
        # The job row already exists here -- it was created before this
        # worker started -- so this only needs the log-capture half.
        # capture_job_logs attaches handlers to both this job's own logger
        # and the shared "astrometricslib" logger that every module deeper
        # in the pipeline logs through, then removes and closes them again
        # when the work finishes. See astrometricslib.foundation.jobs.runner.
        from astrometricslib import capture_job_logs

        job = self._job_service.get_job(job_id) if self._job_service else None

        with capture_job_logs(
            job_id=job_id,
            log_file_path=job.log_file_path if job else None,
            job_store=self._job_service.repository if self._job_service else None,
        ):
            return self._run_analysis_task_body(job_id, target_id, image_files, filter_type, type)

    def _run_analysis_task_body(
        self,
        job_id: str,
        target_id: str,
        image_files: Any,
        filter_type: str | None,
        type: str = "photometry",
    ) -> dict[str, Any]:
        """Body of `_start_analysis_task`, run with job logging attached.

        Split out purely so `_start_analysis_task` can guarantee the
        logging handler cleanup above runs on every exit path (including
        the several early/branch returns below) via a single try/finally,
        without re-indenting this entire body under it.

        Returns
        -------
        result : `dict`
            Same as `_start_analysis_task`.

        Raises
        ------
        InvalidArgumentError
            If no image files were given (outside photometry mode), or
            if the filter is not one the analysis supports.
        """
        logger.info(
            "[%s] Background analysis worker started for %s (Job: %s)", target_id, target_id, job_id
        )

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

        logger.info("[%s] Analysis task for %s found %s files", target_id, target_id, len(paths))

        if not paths and type != "photometry":
            raise InvalidArgumentError(
                "No image files were given for analysis. Pick at least one frame.",
                details={"target_id": target_id},
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
                        logger.info(
                            "[%s] Auto-detected spectroscopy from FITS header FILTER: %s",
                            target_id,
                            fit_filter,
                        )
            except FITS_READ_ERRORS as e:
                logger.warning("[%s] Could not read FITS header for auto-detection: %s", target_id, e)

            if not fallback_is_spec:
                first_file = os.path.basename(unmatched_paths[0]).upper()
                if "SPECTRUM" in first_file or "_SPEC" in first_file or "SPECTROSCOPY" in first_file:
                    fallback_is_spec = True
                    logger.info(
                        "[%s] Auto-detected spectroscopy from filename: %s", target_id, unmatched_paths[0]
                    )

        spec_paths = matched_spec_paths + (unmatched_paths if fallback_is_spec else [])
        light_paths = matched_light_paths + (unmatched_paths if not fallback_is_spec else [])

        if spec_paths and light_paths:
            logger.info(
                "[%s] Analysis batch spans both frame types: %s light/luminance, %s spectroscopy.",
                target_id,
                len(light_paths),
                len(spec_paths),
            )
            spectroscopy_result = self._run_spectroscopy_analysis(
                job_id, target_id, spec_paths, filter_type or "SPEC", logger=logger
            )
            photometry_result = self._run_photometry_analysis(
                job_id, target_id, light_paths, filter_type, logger=logger
            )
            return {
                "status": "finished",
                "photometry": photometry_result,
                "spectroscopy": spectroscopy_result,
            }

        if spec_paths:
            return self._run_spectroscopy_analysis(
                job_id, target_id, spec_paths, filter_type or "SPEC", logger=logger
            )

        if light_paths or type == "photometry":
            return self._run_photometry_analysis(
                job_id, target_id, light_paths, filter_type, logger=logger
            )

        raise InvalidArgumentError(
            f"The filter {filter_type!r} is not supported for analysis.",
            details={"target_id": target_id, "filter_type": filter_type},
        )

    def _update_job_progress(self, job_id, target_id, current, total, filter_type=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        if self._job_service:
            progress_pct = int((current / total) * 100) if total > 0 else 0
            self._job_service.update_job(job_id, progress=progress_pct)

        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["progress"] = {"current": current, "total": total}

    def _run_spectroscopy_analysis(
        self,
        job_id: str,
        target_id: str,
        paths: list[str],
        filter_type: str | None = None,
        logger: logging.Logger | None = None,
    ) -> dict[str, Any]:
        """Pipeline for spectroscopy extraction.

        Groups frames into observing sessions, identifies each
        session's stars once (reusing an existing FITS-header WCS when
        present), and extracts spectra for those same identified stars
        from every frame in that session -- see
        `ProcessingPipelines.run_spectroscopy_by_session`.
        Builds `target.quality.spectroscopy` here, in this
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
        log.info("[%s] Running spectroscopy analysis via Target.analyze_target", target_id)

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

        def _on_frame_complete(path, frame_result, completed_count, total_count):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
            self._update_job_progress(job_id, target_id, completed_count, total_count, filter_type="SPEC")

        # Stage one: the master stacked spectral image. It is a generated
        # file that lives outside `target.frames`, so it cannot be grouped
        # into observing sessions. It is analyzed on its own, as one
        # high-signal image, which sets the baseline (dispersion geometry,
        # wavelengths, features, spectral type) that stage two relies on.
        # Stage two (the raw per-session frames, below) then follows how
        # the spectra change over time.
        stacked_spectral_path = getattr(getattr(target, "spectral_stacking", None), "stacked_image", None)
        master_paths = [
            path for path in paths if stacked_spectral_path and _is_same_file(path, stacked_spectral_path)
        ]
        paths = [path for path in paths if path not in master_paths]
        for master_path in master_paths:
            log.info("[%s] Analyzing the master stacked spectral image: %s", target_id, master_path)
            with self.astrometrics.processing.acquire_analysis_slot():
                master_result = self.astrometrics.processing.process_target(
                    target,
                    stages=["spectroscopy"],
                    spectroscopy={"path": master_path, "limit": MASTER_STACK_STAR_LIMIT},
                    register_job=False,
                ).results["spectroscopy"]
            master_star_count = len((master_result or {}).get("stellar_objects") or [])
            results["starsProcessed"] += master_star_count
            results["spectraExtracted"] += master_star_count
            self._update_job_progress(job_id, target_id, 1, results["totalImages"], filter_type="SPEC")

        if not paths:
            try:
                self._target_service.save_targets()
            except AstrometricsError, sqlite3.Error, OSError:
                log.exception("[%s] Failed to record target after master stack analysis", target_id)
            log.info(
                "[%s] Master stack analysis complete. %s spectra extracted from %s stars.",
                target_id,
                results["spectraExtracted"],
                results["starsProcessed"],
            )
            return results

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
                "[%s] %s path(s) have no matching FrameRecord on the target and will be skipped: %s",
                target_id,
                len(unmatched_paths),
                unmatched_paths,
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
            log.error("[%s] Failed to process %s for spectroscopy: %s", target_id, path, error_message)

        # target.quality.spectroscopy is now built and attached
        # by run_spectroscopy_by_session itself.

        try:
            self._target_service.save_targets()
        except AstrometricsError, sqlite3.Error, OSError:
            log.exception("[%s] Failed to record target after spectroscopy analysis", target_id)

        log.info(
            "[%s] Spectroscopy analysis complete. %s spectra extracted from %s stars.",
            target_id,
            results["spectraExtracted"],
            results["starsProcessed"],
        )

        if self._notification_service:
            self._notification_service.notify(
                target_id,
                f"Spectroscopy analysis complete for {target_id}. "
                f"{results['spectraExtracted']} spectra extracted.",
                status="success",
            )

        return results

    def _run_photometry_analysis(
        self,
        job_id: str,
        target_id: str,
        paths: list[str],
        filter_type: str | None = None,
        logger: logging.Logger | None = None,
    ) -> dict[str, Any]:
        """Pipeline for multi-frame aperture photometry.

        Measures stellar brightness,. performs plate solving on reference/light
        frames, and calculates light curves for identified stars.

        Returns
        -------
        result : `dict`
            The photometry stage's result from
            `ProcessingPipelines.process_target`.
        """
        log = logger or logging
        log.info("[%s] Running the photometry stage of processing.process_target", target_id)

        # Resolve the Target domain object
        target = self._target_service.get_targets(target_id)
        if not target:
            target = self._target_service.create_target(target_id)

        self._update_job_progress(job_id, target_id, 1, 2, filter_type=filter_type)
        try:
            with self.astrometrics.processing.acquire_analysis_slot():
                # The worker count comes from the configured photometry
                # workers. Each session's stars are identified against a
                # real catalog (reusing an existing FITS-header WCS when
                # present) instead of tracked as anonymous detections.
                res = self.astrometrics.processing.process_target(
                    target,
                    stages=["photometry"],
                    photometry={"filter_type": filter_type, "use_astrometry_seed": True},
                    # This orchestrator already created and is tracking
                    # its own job (job_id, above) for this exact call, via
                    # _submit_job/job_wrapper, so the stage registers none.
                    register_job=False,
                ).results["photometry"]
            self._update_job_progress(job_id, target_id, 2, 2, filter_type=filter_type)

            log.info(
                "[%s] Photometry analysis complete. %s frames processed.",
                target_id,
                res.get("framesProcessed", 0),
            )

            try:
                self._target_service.save_targets()
            except AstrometricsError, sqlite3.Error, OSError:
                log.exception("[%s] Failed to record target after photometry analysis", target_id)

            if self._notification_service:
                msg = (
                    f"Photometry analysis complete for {target_id}. "
                    f"{res.get('framesProcessed', 0)} frames processed."
                )
                self._notification_service.notify(target_id, msg, status="success")

            return res
        except Exception:
            log.exception("[%s] Failed to process photometry", target_id)
            raise
