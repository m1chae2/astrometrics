"""Purpose: Stellar object spectroscopy analysis and calibration tuning.

Description: Handles parsing and extraction of spectral data, object
queries, analysis runs, and calibration tuning.
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def get_plot_data(stellar_object) -> dict[str, list[float]]:  # ruff: ignore[missing-type-function-argument]
    """Normalize internal spectrum formats into a canonical plot format.

    Returns
    -------
    plot_data : `dict[str, list[float]]`
        A dict with ``"wavelengths"`` and ``"intensities"`` keys,
        each a list of floats; both lists are empty if no usable
        spectral data is found on `stellar_object`.
    """
    return stellar_object.get_plot_data()


def list_objects(analysis) -> list[Any]:  # ruff: ignore[missing-type-function-argument]
    """List all stellar objects extracted across the library.

    Returns
    -------
    stellar_objects : `list[Any]`
        All stellar objects loaded from disk, or an empty list if
        loading fails.
    """
    from astrometricslib.drivers import disk_interface

    try:
        return disk_interface.load_stellar_objects(analysis._config)
    except Exception:
        return []


def get_object(analysis, object_id: str) -> Any | None:  # ruff: ignore[missing-type-function-argument]
    """Get a single stellar object by ID using exact/fuzzy matching.

    Tries an indexed exact-id lookup first when the injected butler
    supports one (`get_by_ids`, an indexed primary-key query -- see
    `data_access.butler.DiskButler.get_by_ids`), which is what every
    real `StellarCatalog` is constructed with. This is the path a
    star's detail/spectrum/photometry view actually takes: exact id,
    not a fuzzy guess. Falls back to scanning the full, hydrated
    catalog only when the injected butler doesn't support indexed
    lookup at all (e.g. a minimal test double), or when the exact
    lookup misses and a normalized/case-insensitive match is still
    worth trying.

    Returns
    -------
    stellar_object : `Any` or `None`
        The matching stellar object, or `None` if no exact or
        normalized-fuzzy match is found.
    """
    get_by_ids = getattr(analysis.butler, "get_by_ids", None)
    if callable(get_by_ids):
        exact_matches = get_by_ids("stellar_catalog", [object_id])
        if exact_matches:
            return exact_matches[0]
        objects = analysis.list_objects()
    else:
        objects = analysis.list_objects()
        for obj in objects:
            if obj.id == object_id:
                return obj

    normalized = object_id.lower().replace(" ", "").replace("_", "")
    for obj in objects:
        if obj.id.lower().replace(" ", "").replace("_", "") == normalized:
            return obj

    return None


def get_plot_data_analysis(analysis, object_id: str):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Retrieve normalized plot data for a stellar object.

    Returns
    -------
    plot_data : `PlotData` or `None`
        The normalized wavelength/intensity plot data, or `None` if
        `object_id` does not resolve to a known stellar object.
    """
    from astrometricslib.models.stellar_source import PlotData

    obj = analysis.get_object(object_id)
    if not obj:
        return None
    raw = obj.get_plot_data()
    return PlotData(wavelengths=raw.get("wavelengths", []), intensities=raw.get("intensities", []))


def get_analysis_history(analysis, target_id: str) -> list[Any]:  # ruff: ignore[missing-type-function-argument]
    """Retrieve analysis run history for a target from the database.

    Returns
    -------
    results : `list[Any]`
        `AnalysisResult` entries for relevant job types, or an empty
        list if the logs database is missing or loading fails.
    """
    from astrometricslib.drivers.logger_interface import LoggerInterface
    from astrometricslib.models.stellar_source import AnalysisResult

    try:
        db_path = analysis._config.get_logs_db_path()
        if not os.path.exists(db_path):
            return []
        repository = LoggerInterface(db_path)
        jobs = repository.get_jobs_by_target(target_id)
        results = []
        for job in jobs:
            if job.job_type in ["variability", "spectroscopy", "processing", "analysis"]:
                results.append(
                    AnalysisResult(
                        status=job.status,
                        targetId=job.target_id,
                        jobId=job.job_id,
                        totalImages=job.progress_total,
                        analysisMode=job.job_type,
                        starsProcessed=0,
                        spectraExtracted=0,
                        starsFound=0,
                        framesProcessed=job.progress_current,
                        rejectedCount=0,
                        rejectedFiles=[],
                        variableCandidates=[],
                        error=job.message if job.status == "failed" else None,
                        message=job.message,
                    )
                )
        return results
    except Exception as error:
        logger.error(f"Failed to load analysis history for {target_id}: {error}")
        return []


def tune_spectroscopy_calibration(
    analysis,  # ruff: ignore[missing-type-function-argument]
    image_path: str,
    camera_name: str | None = None,
    star_x: float | None = None,
    star_y: float | None = None,
) -> dict[str, Any]:
    """Run the autonomous physical-model spectroscopy calibration tuner.

    Returns
    -------
    tuning_result : `dict[str, Any]`
        The calibration tuning result produced by
        `SpectroscopyCalibrationTuner.tune_calibration`.
    """
    from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.calibration_tuner import (
        SpectroscopyCalibrationTuner,
    )

    tuner = SpectroscopyCalibrationTuner(config=analysis._config)
    star_pos = (star_x, star_y) if (star_x is not None and star_y is not None) else None
    return tuner.tune_calibration(image_path, camera_name=camera_name, star_pos=star_pos)
