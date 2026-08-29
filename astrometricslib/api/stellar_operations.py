"""Tools for looking at stars and calibrating the spectroscope.

This contains the functions we need to search for a specific star in
the database, pull its light spectrum data so we can graph it, and
run the automatic calibration tool.
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def get_plot_data(stellar_object) -> dict[str, list[float]]:  # ruff: ignore[missing-type-function-argument]
    """Take a star's raw light data and make it ready to graph.

    Returns
    -------
    plot_data : `dict[str, list[float]]`
        A dictionary containing the x-axis (wavelengths) and y-axis
        (intensities) for the graph. If the star has no data, these
        lists will be empty.
    """
    return stellar_object.get_plot_data()


def list_objects(analysis) -> list[Any]:  # ruff: ignore[missing-type-function-argument]
    """Get a list of every single star we've ever analyzed.

    Returns
    -------
    stellar_objects : `list[Any]`
        The full list of stars from the hard drive, or an empty list
        if something goes wrong.
    """
    try:
        return analysis.catalog_access.get("stellar_catalog", {})
    except Exception:
        return []


def get_object(analysis, object_id: str) -> Any | None:  # ruff: ignore[missing-type-function-argument]
    """Find a specific star in the database by its name.

    First we try to look it up exactly (which is very fast). If that
    doesn't work, we load the whole list of stars and search through
    it, ignoring spaces and capital letters, just in case the user
    typed it slightly wrong.

    Returns
    -------
    stellar_object : `Any` or `None`
        The star if we found it, or None if it doesn't exist.
    """
    get_by_ids = getattr(analysis.catalog_access, "get_by_ids", None)
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
    """Look up a star by name and get its graph data.

    Returns
    -------
    plot_data : `PlotData` or `None`
        The graphing data, or None if the star wasn't found.
    """
    from astrometricslib.models.stellar_source import PlotData

    obj = analysis.get_object(object_id)
    if not obj:
        return None
    raw = obj.get_plot_data()
    return PlotData(wavelengths=raw.get("wavelengths", []), intensities=raw.get("intensities", []))


def get_analysis_history(analysis, target_id: str) -> list[Any]:  # ruff: ignore[missing-type-function-argument]
    """Get the history of everything we've done to a specific target.

    Returns
    -------
    results : `list[Any]`
        A list of past analysis jobs, or an empty list if there's no history.
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
    """Run the tool that figures out the spectroscope's settings automatically.

    Returns
    -------
    tuning_result : `dict[str, Any]`
        The calculated settings that make the spectroscope data line up
        with reality.
    """
    from astrometricslib.pipelines.spectroscopy.calibration_tuner import (
        SpectroscopyCalibrationTuner,
    )

    tuner = SpectroscopyCalibrationTuner(config=analysis._config)
    star_pos = (star_x, star_y) if (star_x is not None and star_y is not None) else None
    return tuner.tune_calibration(image_path, camera_name=camera_name, star_pos=star_pos)
