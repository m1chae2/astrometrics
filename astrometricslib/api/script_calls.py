"""Purpose: Four calls that only the library's own scripts and tests use.

Description: `ProcessingPipelines` no longer offers `restore_excluded_frames`,
`stack_subset`, `detect_point_sources` or `tune_spectroscopy_calibration`.
Only scripts inside `astrometricslib` and tests call them, so they are plain
functions here. Each takes the `ProcessingPipelines` to work with as its first
argument and does what the method did. A script or test reaches them through
`astrometricslib._internal.processing_scripts`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from astrometricslib.foundation.jobs.runner import registered_job
from astrometricslib.models.point_sources import PointSource, PointSourceDetection
from astrometricslib.models.quality.excluded_frames import RestoreReport
from astrometricslib.models.quality.subset_stacks import SubsetStackResult
from astrometricslib.models.spectroscopy_calibration import (
    CalibrationLineFit,
    SpectroscopyCalibrationResult,
)
from astrometricslib.models.target import FrameRecord, Target

if TYPE_CHECKING:
    from astrometricslib.api.processing import ProcessingPipelines

__all__ = [
    "detect_point_sources",
    "restore_excluded_frames",
    "stack_subset",
    "tune_spectroscopy_calibration",
]


def _optional_float(value: Any) -> float | None:
    """Turn a NumPy or Python number into a plain float, keeping `None`.

    Parameters
    ----------
    value : `Any`
        A number or `None`.

    Returns
    -------
    number : `float` or `None`
        The number as a Python float.
    """
    return None if value is None else float(value)


def _to_point_source(row: dict[str, Any]) -> PointSource:
    """Turn one detector row into a `PointSource` with plain Python numbers.

    Parameters
    ----------
    row : `dict` [`str`, `Any`]
        One star from the source detector. Its numbers may be NumPy values,
        and the centroid keys may be written ``xcentroid`` or ``x_centroid``.

    Returns
    -------
    source : `PointSource`
        The star.
    """
    identifier = row.get("id")
    pixel_count = row.get("n_pixels")
    return PointSource(
        id=None if identifier is None else int(identifier),
        x_centroid=float(row.get("x_centroid", row.get("xcentroid"))),
        y_centroid=float(row.get("y_centroid", row.get("ycentroid"))),
        flux=float(row.get("flux", 0.0)),
        peak=_optional_float(row.get("peak")),
        sharpness=_optional_float(row.get("sharpness")),
        roundness1=_optional_float(row.get("roundness1")),
        roundness2=_optional_float(row.get("roundness2")),
        n_pixels=None if pixel_count is None else int(pixel_count),
        mag=_optional_float(row.get("mag")),
        daofind_mag=_optional_float(row.get("daofind_mag")),
        radius_px=_optional_float(row.get("radius_px")),
    )


def restore_excluded_frames(
    pipelines: ProcessingPipelines, target: str | Target, apply: bool = False
) -> RestoreReport:
    """List, or move back, the frames the stacker set aside for a target.

    By default nothing moves: the call only lists the frames. With
    ``apply=True`` it moves them back and reindexes the target, which
    saves it. The next stack may set the same frames aside again. To
    keep them in the stack, turn off ``quarantine_bad_frames_enabled`` in
    the configuration first. To only read the list, use
    ``Astrometrics.quality.set_aside_frames``.

    Parameters
    ----------
    pipelines : `ProcessingPipelines`
        The processing pipelines to work with.
    target : `str` or `Target`
        The target whose frames to restore.
    apply : `bool`, optional
        Move the frames back. Defaults to `False`, which only lists them.

    Returns
    -------
    report : `RestoreReport`
        The frames that were set aside, and how many were moved back.
    """
    from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
        find_quarantine_folders,
        list_set_aside_frames,
        restore_quarantined_frames,
    )

    resolved = pipelines._resolve(target)
    frames_path = str(pipelines._config.get_frames_path())
    frames = list_set_aside_frames(frames_path, resolved.id)
    restored_count = 0
    if apply:
        for folder in find_quarantine_folders(frames_path, resolved.id):
            restored_count += len(restore_quarantined_frames(folder))
        if restored_count:
            pipelines._targets.reindex_frames(resolved)
    return RestoreReport(target_id=resolved.id, applied=apply, frames=frames, restored_count=restored_count)


def stack_subset(
    pipelines: ProcessingPipelines,
    target: str | Target,
    frames: list[FrameRecord],
    *,
    label: str,
    output_folder: str | None = None,
    record_forecast_rows: bool = False,
    register_job: bool = True,
) -> SubsetStackResult:
    """Stack exactly the given frames into a scratch folder.

    The stack uses the target's normal settings (rejection, weighting,
    exposure groups and edge trim). It is written to
    ``<target's stacks folder>/_subsets/<label>/<label>.fits``, or to
    ``output_folder`` when that lies inside the ``_subsets`` folder,
    never to the library stack's path. While it runs, no library stack
    and no other subset job of the target can run; a second one is
    refused with `ConflictError`, not queued. Nothing is
    assigned on the target and the target is not saved. The frame
    quarantine step is not run, so give frames the library stack already
    kept. The stack's noise in the target's stored sky apertures, its
    effective frame count and its effective time are stored in the quality
    database at level ``stack``. Delete the ``_subsets`` folder whenever
    you like; nothing reads it back. A label that is unsafe in a path,
    fewer than four frames, or frames from more than one camera raise
    `InvalidArgumentError`.

    Parameters
    ----------
    pipelines : `ProcessingPipelines`
        The processing pipelines to work with.
    target : `str` or `Target`
        The target the frames belong to.
    frames : `list` [`FrameRecord`]
        The frames to stack, at least four, all from one camera.
    label : `str`
        Names the subset's folder and file. Letters, digits, dots,
        underscores and hyphens; it may not start with a dot.
    output_folder : `str`, optional
        Write the stack here instead of the default subset folder. It
        must be inside the target's ``_subsets`` folder.
    record_forecast_rows : `bool`, optional
        Also store what the forecast check reads from a library stack, so
        the subset can be the earlier member of a check's pair (the
        library stack being the later one). The subset's rows carry the
        label as their ``variant`` and a run id of their own, and the
        noise is measured in the library stack's apertures as always.
        Added: one ``stacking.frame_weight`` for each frame (on the
        frame's subject id plus ``#`` and the label, so it never hides the
        library stack's newest row of that frame); the exposure-group rows
        (``stacking.group_exposure_s``, ``group_frames_stacked_count``,
        ``group_gain_applied``, ``group_reference_exposure_s`` and the
        others a library stack stores except the star-peak rows) on the
        subset's own group subjects; and a calibration record under the
        subset's subject. The predicted noise (with each frame's folding
        scale), the normalization scale and the aperture-set row are
        stored for every subset. Defaults to `False`, which stores only
        the rows every subset stores. The restack script uses
        ``label=EARLIER_NIGHTS_VARIANT`` with the frames of all nights
        but the last.
    register_job : `bool`, optional
        Record the run in the job list. Defaults to `True`.

    Returns
    -------
    result : `SubsetStackResult`
        The subset stack's path, its frame counts and its noise (or why
        there is none).
    """
    from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
    from astrometricslib.pipelines.stacking.subset_noise_run import stack_chosen_subset

    resolved = pipelines._resolve(target)
    with registered_job(enabled=register_job, job_type="stacking", target_id=resolved.id) as job:
        context = pipelines._subset_context(resolved, job.job_id)
        return stack_chosen_subset(
            resolved,
            frames,
            label,
            pipelines._library_stack_path(
                resolved,
                "spectral" if any(frame_is_spectral(frame) for frame in frames) else "imaging",
                must_exist=False,
            ),
            context=context,
            configuration=pipelines._config,
            drivers=pipelines._drivers,
            stacking_slot=pipelines.acquire_job_slot,
            output_folder=output_folder,
            record_forecast_rows=record_forecast_rows,
        )


def detect_point_sources(
    pipelines: ProcessingPipelines,
    image_data: Any,
    threshold_sigma: float = 5.0,
    fwhm: float = 4.0,
) -> PointSourceDetection:
    """Find stars (point sources) in an image's pixel data.

    Parameters
    ----------
    pipelines : `ProcessingPipelines`
        The processing pipelines to work with.
    image_data : `numpy.ndarray`
        2D pixel array to search.
    threshold_sigma : `float`, optional
        Detection threshold, in noise standard deviations above the
        background. Defaults to 5.0.
    fwhm : `float`, optional
        Expected star width (FWHM), in pixels. Defaults to 4.0.

    Returns
    -------
    detection : `PointSourceDetection`
        The stars found, brightest first.
    """
    from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector

    found = SourceDetector(threshold_sigma=threshold_sigma, fwhm=fwhm).detect(image_data)
    return PointSourceDetection(sources=[_to_point_source(row) for row in found])


def tune_spectroscopy_calibration(
    pipelines: ProcessingPipelines,
    image_path: str,
    camera_id: str | None = None,
    star_x: float | None = None,
    star_y: float | None = None,
) -> SpectroscopyCalibrationResult:
    """Fit the spectroscope's physical model to one spectrum image.

    Parameters
    ----------
    pipelines : `ProcessingPipelines`
        The processing pipelines to work with.
    image_path : `str`
        Path to the spectroscopy FITS image to calibrate against.
    camera_id : `str`, optional
        The camera, used to look up its quantum-efficiency curve.
    star_x : `float`, optional
        Zero-order star's x pixel position, if already known.
    star_y : `float`, optional
        Zero-order star's y pixel position, if already known.

    Returns
    -------
    calibration_result : `SpectroscopyCalibrationResult`
        The fitted calibration values and how well they fit.
    """
    from astrometricslib.pipelines.spectroscopy.utilities.calibration_tuner import (
        SpectroscopyCalibrationTuner,
    )

    tuner = SpectroscopyCalibrationTuner(config=pipelines._config, drivers=pipelines._drivers)
    star_position = (star_x, star_y) if (star_x is not None and star_y is not None) else None
    summary = tuner.tune_calibration(image_path, camera_name=camera_id, star_pos=star_position)
    maximum_length = summary["max_extraction_length_px"]
    return SpectroscopyCalibrationResult(
        status=str(summary["status"]),
        camera_name=str(summary["camera_name"]),
        fitted_grating_distance_mm=float(summary["fitted_grating_distance_mm"]),
        fitted_dispersion_start_px=float(summary["fitted_dispersion_start_px"]),
        rms_error_nm=float(summary["rms_error_nm"]),
        detected_angle_degrees=float(summary["detected_angle_degrees"]),
        detailed_calibration=[
            CalibrationLineFit(
                feature=str(line["feature"]),
                extracted_index=int(line["extracted_index"]),
                pixel_offset=float(line["pixel_offset"]),
                target_wavelength_nm=float(line["target_wavelength_nm"]),
                calibrated_wavelength_nm=float(line["calibrated_wavelength_nm"]),
                deviation_nm=float(line["deviation_nm"]),
            )
            for line in summary["detailed_calibration"]
        ],
        use_flare_mask_extraction=bool(summary["use_flare_mask_extraction"]),
        max_extraction_length_px=None if maximum_length is None else float(maximum_length),
    )
