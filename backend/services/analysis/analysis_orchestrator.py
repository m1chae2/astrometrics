"""Orchestrate scientific analysis tasks (photometry, spectroscopy)."""

import json
import logging
import os
import sqlite3
import threading
from collections.abc import Iterator
from datetime import datetime
from typing import TYPE_CHECKING, Any

from astrometricslib import AppConfiguration, Astrometrics, FilterType
from astrometricslib.exceptions import FITS_READ_ERRORS, AstrometricsError, InvalidArgumentError
from astrometricslib.jobs import RunAbandonedError, RunAbandonment, bind_abandonment
from backend.services.infrastructure.base_service import BaseBackgroundService

if TYPE_CHECKING:
    from backend.services.data.stellar_service import StellarService
    from backend.services.data.target_service import TargetService
    from backend.services.infrastructure.notification_service import NotificationService
    from backend.services.processing.job_service import JobService

logger = logging.getLogger(__name__)

# The job log file of a stack's star identification starts with this, so the
# analysis result reader and the duplicate check can tell it from an analysis.
STACK_IDENTIFICATION_LOG_PREFIX = "stack_astrometry_"

# How many stars are measured in the master stacked spectral image. It has
# the best signal-to-noise ratio of any spectral image, so the brightest
# ten stars are enough to set the baseline without spending time on faint
# detections that are mostly noise.
MASTER_STACK_STAR_LIMIT = 10

# The stages a client may ask for with the `stages` argument of
# `analyze_image`. They run in this order. Photometry and spectroscopy need
# astrometry, which the library adds on its own when it is missing or old.
# Spectral variation needs spectroscopy, which the library also adds when
# its result is missing or older than the spectral stack.
SELECTABLE_ANALYSIS_STAGES = ("astrometry", "photometry", "spectroscopy", "spectral_variation")


def _json_default(value: Any) -> Any:
    """Convert a value the `json` module cannot write into plain data.

    Returns
    -------
    plain : `Any`
        A Pydantic model as a dict, a NumPy scalar or array as Python
        numbers, or a `Path` as text.

    Raises
    ------
    TypeError
        If the value has no plain form.
    """
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    raise TypeError(f"Cannot store {type(value).__name__} in a job result.")


def _is_identification_job(job: Any) -> bool:
    """Say whether a job record is a stack's star identification.

    Parameters
    ----------
    job : `ProcessingJob`
        A job record.

    Returns
    -------
    is_identification : `bool`
        True when its log file name starts with the identification prefix.
    """
    log_file = getattr(job, "log_file_path", None) or ""
    return os.path.basename(log_file).startswith(STACK_IDENTIFICATION_LOG_PREFIX)


def _identification_payload(identification: Any) -> dict[str, Any]:
    """Turn a stack identification into the plain data the app reads.

    Parameters
    ----------
    identification : `StackIdentification`
        The library's result.

    Returns
    -------
    payload : `dict`
        ``stars`` in the overlay layout, placed on the viewer's copy of the
        stack (moved by its crop offset), the counts, ``solutionReused``,
        ``gaiaDepth``, ``catalogOnlyLimit``, ``gaiaTilesMissing``,
        ``simbadUsed`` and ``notes``.
    """
    # The viewer shows the stack's stretched copy, which may be a crop of it.
    offset_x = getattr(identification, "viewer_offset_x", 0) or 0
    offset_y = getattr(identification, "viewer_offset_y", 0) or 0
    shown_width = getattr(identification, "viewer_width", 0) or identification.width
    shown_height = getattr(identification, "viewer_height", 0) or identification.height
    stars = [
        {
            "id": star.id,
            "name": star.name,
            "x": star.x - offset_x,
            "y": star.y - offset_y,
            "spectralType": "",
            "isCatalogIdentified": star.kind != "unmatched",
            "referenceWidth": shown_width,
            "referenceHeight": shown_height,
            "radiusPx": None,
            "matchKind": star.kind,
            "raDeg": star.ra_deg,
            "decDeg": star.dec_deg,
            "gMag": star.g_mag,
            "gaiaSourceId": star.gaia_source_id,
            "nameSource": star.name_source,
        }
        for star in identification.stars
    ]
    return {
        "stars": stars,
        "matchedCount": identification.matched_count,
        "unmatchedCount": identification.unmatched_count,
        "catalogOnlyCount": identification.catalog_only_count,
        "solutionReused": identification.solution_reused,
        "gaiaDepth": identification.gaia_depth,
        "catalogOnlyLimit": identification.catalog_only_limit,
        "gaiaTilesMissing": identification.gaia_tiles_missing,
        "simbadUsed": identification.simbad_used,
        "notes": list(identification.notes),
    }


STAGE_DISPLAY_NAMES = {
    "astrometry": "Astrometry",
    "photometry": "Photometry",
    "spectroscopy": "Spectroscopy",
    "spectral_variation": "Spectral Variation",
    "asteroids": "Asteroids",
}
"""The words shown for each stage, the same as in the app's job subtitle."""

MAXIMUM_SKIP_REASON_LENGTH = 24
"""The longest first clause of a skip reason kept in a message."""


def stage_display_name(stage: str) -> str:
    """Give a stage the name the app shows.

    Parameters
    ----------
    stage : `str`
        The stage name, such as ``spectral_variation``.

    Returns
    -------
    name : `str`
        The name shown, such as ``Spectral Variation``.
    """
    return STAGE_DISPLAY_NAMES.get(stage, stage)


def skipped_stage_text(stage: str, reason: str) -> str:
    """Write a skipped stage with a short note, as the app's subtitle does.

    Parameters
    ----------
    stage : `str`
        The skipped stage.
    reason : `str`
        The reason the library gave.

    Returns
    -------
    text : `str`
        For example ``Photometry (skipped: needs astrometry)``. The reason
        is kept only when its first clause is short.
    """
    short_reason = reason.split(":")[0].strip()
    if short_reason and len(short_reason) <= MAXIMUM_SKIP_REASON_LENGTH:
        return f"{stage_display_name(stage)} (skipped: {short_reason})"
    return f"{stage_display_name(stage)} (skipped)"


class AnalysisOrchestrator(BaseBackgroundService):
    """Service for managing background scientific analysis tasks.

    Handles spectroscopy extraction and photometry analysis.
    """

    def __init__(
        self,
        config_service: AppConfiguration | None = None,
        stellar_service: StellarService | None = None,
        target_service: TargetService | None = None,
        notification_service: NotificationService | None = None,
        job_service: JobService | None = None,
        astrometrics: Astrometrics | None = None,
    ) -> None:
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
        # The stop switch of each running star identification, by job id.
        self._identification_stops: dict[str, threading.Event] = {}
        # The give-up switch of each running analysis job, by job id.
        self._analysis_abandonments: dict[str, RunAbandonment] = {}

    def analyze_image(
        self,
        target_id: str,
        image_files: Any,
        filter_name: str | None = None,
        type: str = "photometry",
        stages: list[str] | None = None,
    ) -> dict[str, Any]:
        """Start a background analysis job.

        Parameters
        ----------
        target_id : `str`
            The target to analyze.
        image_files : `Any`
            The image files the app sends: a list of paths or frame
            records, or a nested dictionary of them. With ``stages`` they
            stand in for a stack the target does not have.
        filter_name : `str`, optional
            The filter of the frames to analyze.
        type : `str`, optional
            The kind of analysis when ``stages`` is not given.
        stages : `list` [`str`], optional
            The stages to run, from ``"astrometry"``, ``"photometry"``,
            ``"spectroscopy"`` and ``"spectral_variation"``. When left out, the
            stages come from the kind of the image files, as before.
            Either way the library first runs astrometry when it is needed
            and its result is missing or older than the stack. When given,
            the stack is used for each stage that has one, and a sent file
            only for a stage whose stack the target lacks.

        Returns
        -------
        result : `dict`
            Dict with `"status"` (`"started"` or
            `"already_running"`), `"jobId"`, and `"logFile"`.

        Notes
        -----
        A ``stages`` list that is empty or names a stage other than
        astrometry, photometry and spectroscopy raises
        `InvalidArgumentError` before any job starts.
        """
        chosen_stages = self._checked_stages(stages)
        # Isolated log file per job
        safe_target = target_id.replace(" ", "_").replace("/", "_")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = self._config_service.get_logs_path()
        log_file = str(log_dir / f"analysis_{safe_target}_{timestamp}.log")

        with self._analyze_submit_lock:
            if self._job_service:
                for job in self.get_live_jobs_for_target(target_id, "analysis"):
                    if job.log_file_path and f"analysis_{safe_target}" in job.log_file_path:
                        return {"status": "already_running", "jobId": job.id, "logFile": job.log_file_path}

            job_id = self._submit_job(
                target_id,
                "analysis",
                self._start_analysis_task,
                image_files,
                filter_name,
                type,
                chosen_stages,
                log_file_path=log_file,
            )

        return {"status": "started", "jobId": job_id, "logFile": log_file}

    @staticmethod
    def _checked_stages(stages: list[str] | None) -> list[str] | None:
        """Check the stages a client asked for.

        Parameters
        ----------
        stages : `list` [`str`] or `None`
            The stages, or `None` for the choice by file type.

        Returns
        -------
        chosen_stages : `list` [`str`] or `None`
            The stages without repeats, in running order, or `None`.

        Raises
        ------
        InvalidArgumentError
            If the list is empty or has a stage that cannot be chosen.
        """
        if stages is None:
            return None
        unknown = sorted({str(stage) for stage in stages} - set(SELECTABLE_ANALYSIS_STAGES))
        if unknown or not stages:
            raise InvalidArgumentError(
                f"Unknown or missing analysis stage(s): {', '.join(unknown) or 'none given'}. "
                f"Choose from: {', '.join(SELECTABLE_ANALYSIS_STAGES)}.",
                details={"stages": stages},
            )
        return [stage for stage in SELECTABLE_ANALYSIS_STAGES if stage in stages]

    def get_analysis_results(self, target_id: str, filter_name: str | None = None) -> dict[str, Any] | None:
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
            recent_jobs = [
                job
                for job in self._job_service.get_jobs_for_target(target_id, job_type="analysis", limit=10)
                if not _is_identification_job(job)
            ]
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
                    # The finished summary was saved on the job record
                    # (see `_store_job_result`), so it survives a restart.
                    stored_result = (job.output_metrics or {}).get("result")
                    if isinstance(stored_result, dict):
                        return stored_result
                    return {"status": "finished", "jobId": job.id}
                elif job.status == "failed":
                    return {"status": "failed", "jobId": job.id, "error": job.message}
                elif job.status in ("started", "running"):
                    return {"status": "started", "jobId": job.id}

        return None

    def cancel_analysis(self, target_id: str, filter_name: str | None = None) -> dict[str, Any]:
        """Cancel an active analysis job, or say why it cannot be cancelled.

        Returns
        -------
        result : `dict`
            `"cancelled"` is `True` if an active job was stopped. If not,
            `"reason"` says why (no active job, or the job runs in another
            process).
        """
        if self._job_service:
            active = self._job_service.get_jobs_for_target(target_id, job_type="analysis", status="started")
            if not active:
                # A job that reported a stage is "running", not "started".
                active = self._job_service.get_jobs_for_target(
                    target_id, job_type="analysis", status="running"
                )
            if active:
                stop = self._identification_stops.get(active[0].id)
                if stop is not None:
                    stop.set()
                    return {"cancelled": True, "own_process": True, "reason": None}
                abandonment = self._analysis_abandonments.get(active[0].id)
                outcome = self.cancel_processing(active[0].id)
                if abandonment is not None:
                    # Running: stop its programs, slot wait and next stage.
                    abandonment.abandon()
                    outcome = {"cancelled": True, "own_process": True, "reason": None}
                if outcome["cancelled"]:
                    self._job_service.update_job(
                        active[0].id, status="cancelled", progress=0, status_message="Cancelled by user"
                    )
                return outcome
        return {
            "cancelled": False,
            "own_process": False,
            "reason": "There is no active analysis job for this target.",
        }

    def identify_stack(self, target_id: str, stack_path: str) -> dict[str, Any]:
        """Start naming the detected stars of a target's stack, as a job.

        The job has the type of an analysis, so the Analyze Target button
        shows it as running and its Cancel button stops it. A raw frame is
        refused; its star names come from the overlay instead.

        Parameters
        ----------
        target_id : `str`
            The target.
        stack_path : `str`
            The stack, inside the library folders.

        Returns
        -------
        result : `dict`
            ``status`` (``started`` or ``already_running``) and ``jobId``.

        Raises
        ------
        InvalidArgumentError
            If the path is outside the library or not a stack.
        """
        checked = self._checked_stack_path(stack_path)
        try:
            frame_count = int(self.astrometrics.targets.read_frame_header(checked).get("STACKCNT", 0) or 0)
        except FITS_READ_ERRORS as error:
            raise InvalidArgumentError(f"The stack could not be read: {error}") from error
        if frame_count <= 1:
            raise InvalidArgumentError("Star names are made for stacks only, not for single frames.")
        safe_target = target_id.replace(" ", "_").replace("/", "_")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_name = f"{STACK_IDENTIFICATION_LOG_PREFIX}{safe_target}_{timestamp}.log"
        log_file = str(self._config_service.get_logs_path() / log_name)
        with self._analyze_submit_lock:
            if self._job_service:
                active = self._job_service.get_jobs_for_target(
                    target_id, job_type="analysis", status="started"
                )
                for job in active:
                    if _is_identification_job(job):
                        return {"status": "already_running", "jobId": job.id}
            stop = threading.Event()
            job_id = self._submit_job(
                target_id, "analysis", self._run_stack_identification, checked, stop, log_file_path=log_file
            )
            self._identification_stops[job_id] = stop
        return {"status": "started", "jobId": job_id}

    def get_stack_identification(self, target_id: str, stack_path: str) -> dict[str, Any]:
        """Read the star names of a stack, and whether a job is making them.

        Parameters
        ----------
        target_id : `str`
            The target.
        stack_path : `str`
            The stack, inside the library folders.

        Returns
        -------
        result : `dict`
            ``running`` (a job is naming the stars), ``jobId``, ``progress``
            (0 to 1) and ``message`` of that job, ``error`` of the last
            failed job, and ``identification``: the saved result with the
            stars in the overlay layout (``id``, ``name``, ``x``, ``y``,
            ``referenceWidth``, ``referenceHeight``, ``isCatalogIdentified``,
            ``matchKind``, ``raDeg``, ``decDeg``, ``gMag``), or `None`.
        """
        checked = self._checked_stack_path(stack_path)
        answer: dict[str, Any] = {
            "running": False,
            "jobId": None,
            "progress": None,
            "message": None,
            "error": None,
        }
        if self._job_service:
            jobs = [
                job
                for job in self._job_service.get_jobs_for_target(target_id, job_type="analysis", limit=10)
                if _is_identification_job(job)
            ]
            if jobs:
                latest = jobs[0]
                answer["jobId"] = latest.id
                if latest.status in ("started", "running"):
                    answer["running"] = True
                    total = latest.progress_total or 100
                    answer["progress"] = min(1.0, max(0.0, (latest.progress_current or 0) / total))
                    answer["message"] = latest.message
                elif latest.status == "failed":
                    answer["error"] = latest.message
        identification = self.astrometrics.processing.get_stack_identification(checked)
        answer["identification"] = None if identification is None else _identification_payload(identification)
        return answer

    def _checked_stack_path(self, stack_path: str) -> str:
        """Refuse a path outside the library folders.

        Returns
        -------
        path : `str`
            The resolved path.

        Raises
        ------
        InvalidArgumentError
            If no path is given or it is outside the library.
        """
        if not stack_path:
            raise InvalidArgumentError("No stack was given.")
        if self._stellar_service is None:
            return stack_path
        return self._stellar_service._checked_image_path(stack_path)

    def _run_stack_identification(
        self, job_id: str, target_id: str, stack_path: str, stop: threading.Event, **kwargs: Any
    ) -> dict[str, Any]:
        """Name the stack's stars, logging to the job and reporting progress.

        Returns
        -------
        result : `dict`
            A short summary: the counts and the notes.
        """
        from astrometricslib.jobs import capture_job_logs

        job = self._job_service.get_job(job_id) if self._job_service else None

        def report(share: float, message: str) -> None:
            """Store the progress on the job record.

            Parameters
            ----------
            share : `float`
                The share done, 0 to 1.
            message : `str`
                What is being done.
            """
            logger.info("[%s] %s (%.0f%%)", target_id, message, share * 100)
            if self._job_service:
                self._job_service.update_job(job_id, progress=share * 100, status_message=message)

        try:
            with capture_job_logs(
                job_id=job_id,
                log_file_path=job.log_file_path if job else None,
                job_store=self._job_service.repository if self._job_service else None,
            ):
                identification = self.astrometrics.processing.identify_stack(
                    stack_path, progress=report, should_stop=stop.is_set
                )
                result = {
                    "status": "finished",
                    "kind": "stack_identification",
                    "matched": identification.matched_count,
                    "unmatched": identification.unmatched_count,
                    "catalogOnly": identification.catalog_only_count,
                    "aliasRecordsUpdated": identification.alias_records_updated,
                    "notes": list(identification.notes),
                }
                self._store_job_result(job_id, result)
                return result
        finally:
            self._identification_stops.pop(job_id, None)

    def _start_analysis_task(
        self,
        job_id: str,
        target_id: str,
        image_files: Any,
        filter_name: str | None,
        type: str = "photometry",
        stages: list[str] | None = None,
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
        from astrometricslib.jobs import capture_job_logs

        job = self._job_service.get_job(job_id) if self._job_service else None

        with capture_job_logs(
            job_id=job_id,
            log_file_path=job.log_file_path if job else None,
            job_store=self._job_service.repository if self._job_service else None,
        ):
            abandonment = RunAbandonment()
            self._analysis_abandonments[job_id] = abandonment
            try:
                with bind_abandonment(abandonment):
                    result = self._run_analysis_task_body(
                        job_id, target_id, image_files, filter_name, type, stages
                    )
            except RunAbandonedError:
                # The user cancelled the job. `cancel_analysis` marked it.
                logger.info("[%s] The analysis was cancelled.", target_id)
                return {"status": "cancelled"}
            finally:
                self._analysis_abandonments.pop(job_id, None)
            self._store_job_result(job_id, result)
            return result

    def _store_job_result(self, job_id: str, result: dict[str, Any]) -> None:
        """Save the finished summary on the job record.

        The job table already keeps each job's output numbers, and the
        library's own job runner keeps a job's result under the
        `"result"` key there, so this follows the same convention. It
        lets `get_analysis_results` answer after a backend restart.
        Failing to save is logged, not raised: the analysis itself
        succeeded.
        """
        if not self._job_service:
            return
        try:
            plain_result = json.loads(json.dumps(result, default=_json_default))
            self._job_service.update_job(job_id, output_metrics={"result": plain_result})
        except TypeError, ValueError, sqlite3.Error:
            logger.warning("Could not save the result of analysis job %s.", job_id, exc_info=True)

    def _run_analysis_task_body(
        self,
        job_id: str,
        target_id: str,
        image_files: Any,
        filter_name: str | None,
        type: str = "photometry",
        stages: list[str] | None = None,
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
        logger.info("[%s] Background analysis worker started for %s (Job: %s)", target_id, target_id, job_id)

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
            def flatten(d: dict[str, Any], current_filter: str | None = None) -> Iterator[Any]:
                for k, v in d.items():
                    if isinstance(v, dict):
                        yield from flatten(
                            v, k if current_filter is None or k in ["L", "SPEC", "NONE"] else current_filter
                        )
                    elif isinstance(v, list):
                        if (
                            not filter_name
                            or k.upper() == filter_name.upper()
                            or (filter_name.upper() == "L" and k.upper() in ["LUMINANCE", "NONE", "UNKNOWN"])
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

        if stages is not None:
            return self._run_chosen_stages(job_id, target_id, paths, filter_name, stages)

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
            and filter_name
            and filter_name.upper() in ["SPEC", "SPECTROSCOPY", "STAR ANALYZER 200"]
        ):
            fallback_is_spec = True

        if (
            unmatched_paths
            and not fallback_is_spec
            and (not filter_name or filter_name.upper() in ["NONE", "UNKNOWN", "LUMINANCE", "L", "ALL"])
        ):
            try:
                header = self.astrometrics.targets.read_frame_header(unmatched_paths[0])
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
            spectroscopy_result = self._run_chosen_stages(
                job_id, target_id, spec_paths, filter_name or "SPEC", ["spectroscopy"]
            )
            photometry_result = self._run_photometry_analysis(
                job_id, target_id, light_paths, filter_name, logger=logger
            )
            return {
                "status": "finished",
                "photometry": photometry_result,
                "spectroscopy": spectroscopy_result,
            }

        if spec_paths:
            return self._run_chosen_stages(
                job_id, target_id, spec_paths, filter_name or "SPEC", ["spectroscopy"]
            )

        if light_paths or type == "photometry":
            return self._run_photometry_analysis(job_id, target_id, light_paths, filter_name, logger=logger)

        raise InvalidArgumentError(
            f"The filter {filter_name!r} is not supported for analysis.",
            details={"target_id": target_id, "filter_name": filter_name},
        )

    def _run_chosen_stages(
        self, job_id: str, target_id: str, paths: list[str], filter_name: str | None, stages: list[str]
    ) -> dict[str, Any]:
        """Run the stages a client chose, as one library call.

        The library adds astrometry first when a chosen stage needs it and
        its result is missing or older than the stack. Astrometry and
        spectroscopy use the target's stack. A sent file takes the stack's
        place only for a stage whose stack the target lacks.

        Parameters
        ----------
        job_id : `str`
            The analysis job.
        target_id : `str`
            The target.
        paths : `list` [`str`]
            The image files the app sent.
        filter_name : `str`, optional
            The filter for photometry.
        stages : `list` [`str`]
            The checked stages.

        Returns
        -------
        result : `dict`
            ``status``, ``message`` (also saved as the job's message),
            ``stagesRun``, ``prerequisitesRun``, ``stagesSkipped`` (stage to
            reason) and ``results`` (stage to its status).
        """
        target = self._target_service.get_targets(target_id)
        if not target:
            target = self._target_service.create_target(target_id)

        spectral_paths = set()
        for frame in target.frames:
            if frame.filter == FilterType.SPEC:
                spectral_paths.add(frame.path)
        sent_light_paths = [path for path in paths if path not in spectral_paths]
        sent_spectral_paths = [path for path in paths if path in spectral_paths]

        astrometry_options: dict[str, Any] = {}
        imaging_stack = target.stacking.stacked_image
        if not (imaging_stack and os.path.exists(imaging_stack)) and sent_light_paths:
            astrometry_options["path"] = sent_light_paths[0]

        spectroscopy_options: dict[str, Any] = {}
        spectral_stack = target.spectral_stacking.stacked_image
        if spectral_stack and os.path.exists(spectral_stack):
            spectroscopy_options = {"path": spectral_stack, "limit": MASTER_STACK_STAR_LIMIT}
        elif sent_spectral_paths:
            spectroscopy_options = {"path": sent_spectral_paths[0]}

        # A raw spectral frame the app sent is a frame to follow. Without
        # one, the stage follows every spectral frame of the target.
        spectral_variation_options: dict[str, Any] = {}
        if sent_spectral_paths and "spectral_variation" in stages:
            spectral_variation_options["frames"] = [
                frame for frame in target.frames if frame.path in set(sent_spectral_paths)
            ]

        self._update_job_progress(job_id, target_id, 1, 2, filter_name=filter_name)
        self._record_steps(job_id, requested=list(stages))
        with self.astrometrics.processing.acquire_job_slot():
            outcome = self.astrometrics.processing.process_target(
                target,
                stages=stages,
                astrometry=astrometry_options or None,
                photometry={"filter_name": filter_name, "use_astrometry_seed": True},
                spectroscopy=spectroscopy_options or None,
                spectral_variation=spectral_variation_options or None,
                run_prerequisites=True,
                # This job is already tracked (job_id), so the stages
                # register none of their own.
                register_job=False,
                on_stages_planned=lambda requested, added: self._record_steps(
                    job_id, requested=list(requested), prerequisites=list(added)
                ),
            )
        self._update_job_progress(job_id, target_id, 2, 2, filter_name=filter_name)
        skipped = dict(outcome.stages_skipped)
        for stage, stage_result in outcome.results.items():
            if isinstance(stage_result, dict) and stage_result.get("status") == "skipped":
                skipped.setdefault(stage, str(stage_result.get("reason") or ""))
        self._record_steps(
            job_id,
            requested=list(stages),
            prerequisites=list(outcome.prerequisites_run),
            run=[stage for stage in outcome.stages_run if stage not in skipped],
            skipped=skipped,
            finished=True,
        )

        message = self._chosen_stages_message(stages, outcome)
        if self._job_service:
            self._job_service.update_job(job_id, status_message=message)
        logger.info("[%s] %s", target_id, message)
        try:
            self._target_service.save_targets()
        except AstrometricsError, sqlite3.Error, OSError:
            logger.exception("[%s] Failed to record target after the analysis", target_id)
        if self._notification_service and outcome.stages_run:
            self._notification_service.notify(target_id, message, status="success")

        return {
            "status": "finished",
            "targetId": target_id,
            "message": message,
            "stagesRun": list(outcome.stages_run),
            "prerequisitesRun": list(outcome.prerequisites_run),
            "stagesSkipped": dict(outcome.stages_skipped),
            "results": {
                stage: (
                    stage_result.get("status", "finished") if isinstance(stage_result, dict) else "finished"
                )
                for stage, stage_result in outcome.results.items()
            },
        }

    def _record_steps(self, job_id: str, **steps: Any) -> None:
        """Save the steps of a chosen-stages run on the job record.

        The Recent Jobs list names a job's steps from this record. Each call
        adds to the record already saved: ``requested`` (the stages asked
        for), ``prerequisites`` (stages added because a result was missing or
        old), ``run`` (stages that ran), ``skipped`` (stage to reason) and
        ``finished`` (`True` once the run is over). Failing to save is logged,
        not raised: the analysis itself does not depend on it.

        Parameters
        ----------
        job_id : `str`
            The analysis job.
        **steps : `Any`
            The parts of the record to set.
        """
        if not self._job_service:
            return
        try:
            job = self._job_service.get_job(job_id)
            saved = dict((job.input_metrics or {}).get("steps") or {}) if job else {}
            self._job_service.record_input_metrics(job_id, {"steps": {**saved, **steps}})
        except sqlite3.Error, TypeError, ValueError:
            logger.warning("Could not save the steps of analysis job %s.", job_id, exc_info=True)

    @staticmethod
    def _chosen_stages_message(stages: list[str], outcome: Any) -> str:
        """Write the job's closing message for a run of chosen stages.

        Parameters
        ----------
        stages : `list` [`str`]
            The stages the client asked for.
        outcome : `ProcessTargetResult`
            The library's answer.

        Returns
        -------
        message : `str`
            The same short step text as the Recent Jobs subtitle, with no
            closing punctuation: the stages added first, an arrow, then the
            stages that ran, for example ``Spectroscopy → Spectral Variation``.
            A skipped stage is written ``Photometry (skipped: needs
            astrometry)``, and a run with no stage ``No stage run``.
        """
        variation = outcome.results.get("spectral_variation")
        variation_skipped = isinstance(variation, dict) and variation.get("status") == "skipped"
        ran = [
            stage for stage in outcome.stages_run if not (variation_skipped and stage == "spectral_variation")
        ]
        added = [stage for stage in outcome.prerequisites_run if stage in ran]
        chosen = [stage_display_name(stage) for stage in ran if stage not in added]
        for stage, reason in outcome.stages_skipped.items():
            chosen.append(skipped_stage_text(stage, reason))
        if variation_skipped and "spectral_variation" not in outcome.stages_skipped:
            chosen.append(skipped_stage_text("spectral_variation", str(variation.get("reason") or "")))
        chosen_text = " + ".join(chosen)
        added_text = " + ".join(stage_display_name(stage) for stage in added)
        if added_text and chosen_text:
            return f"{added_text} \u2192 {chosen_text}"
        return added_text or chosen_text or "No stage run"

    def _update_job_progress(
        self, job_id: str, target_id: str, current: int, total: int, filter_name: str | None = None
    ) -> None:
        if self._job_service:
            progress_pct = int((current / total) * 100) if total > 0 else 0
            self._job_service.update_job(job_id, progress=progress_pct)

        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["progress"] = {"current": current, "total": total}

    def _run_photometry_analysis(
        self,
        job_id: str,
        target_id: str,
        paths: list[str],
        filter_name: str | None = None,
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

        # Photometry needs astrometry, which the library runs first when it is
        # missing or older than the imaging stack. A target with no stack
        # solves the first image the app sent instead.
        astrometry_options: dict[str, Any] | None = None
        imaging_stack = target.stacking.stacked_image
        if not (imaging_stack and os.path.exists(imaging_stack)) and paths:
            astrometry_options = {"path": paths[0]}

        self._update_job_progress(job_id, target_id, 1, 2, filter_name=filter_name)
        try:
            with self.astrometrics.processing.acquire_job_slot():
                # The worker count comes from the configured photometry
                # workers. Each session's stars are identified against a
                # real catalog (reusing an existing FITS-header WCS when
                # present) instead of tracked as anonymous detections.
                res = self.astrometrics.processing.process_target(
                    target,
                    stages=["photometry"],
                    astrometry=astrometry_options,
                    photometry={"filter_name": filter_name, "use_astrometry_seed": True},
                    # This orchestrator already created and is tracking
                    # its own job (job_id, above) for this exact call, via
                    # _submit_job/job_wrapper, so the stage registers none.
                    register_job=False,
                ).results["photometry"]
            self._update_job_progress(job_id, target_id, 2, 2, filter_name=filter_name)

            if res.get("status") == "skipped":
                # Astrometry could not run, so photometry did not run.
                log.warning("[%s] Photometry was skipped: %s", target_id, res.get("reason"))
                if self._job_service:
                    self._job_service.update_job(
                        job_id, status_message=skipped_stage_text("photometry", str(res.get("reason") or ""))
                    )
                return res

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
