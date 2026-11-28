"""Purpose: Run one analysis pipeline for one target, as one recorded job.

Description: These functions are the owners of the analysis pipelines'
public ``run`` methods: `processing.astrometry.run`,
`processing.photometry.run`, `processing.spectroscopy.run`,
`processing.spectroscopy.record_variation` and `processing.asteroids.run`.
Each one runs its stage through `tasks.run_target_stages`, so the stage
order, the prerequisite rule and the skip reasons are the same as for any
other caller:

* Astrometry is the core layer. Photometry and spectroscopy only read its
  plate solution and never run astrometry. When the solution they need is
  missing or out of date, the stage is skipped, with the reason
  ``needs astrometry: <why>; run processing.astrometry.run(target) first``,
  one job warning and one INFO log line.
* Spectral variation needs spectroscopy first. Spectroscopy runs first when
  its saved result is missing or older than the spectral stack.

Each run records one ``analysis`` job and holds one of the shared job slots
while it works (stacking and analysis share the pool). A run inside a job
that is already current (a backend job, a batch) joins it.

A run works on one stack. ``stack`` is the stack a caller chose with
`processing.stacking.get_stack` (a `StackConfigurationResult`) or a path.
Left out, the target's main stack is used. The frames come from the camera
and optic of the stack analyzed, unless the caller passes ``frames``; a
frame list from more than one camera or optic is refused with the groups
and their counts.
"""

import os
from datetime import datetime
from typing import Any

from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.jobs.runner import registered_job
from astrometricslib.foundation.storage.process_locks import acquire_resource_slot
from astrometricslib.models.processing_results import ProcessTargetResult
from astrometricslib.models.target import StackConfigurationResult, Target
from astrometricslib.pipelines.shared.camera_passes import ProcessingFrameSelection, resolve_processing_frames
from astrometricslib.pipelines.shared.frame_choice import FrameChoice, check_frame_list, frames_from_choice

__all__ = [
    "StackChoice",
    "record_spectral_variation",
    "run_asteroid_search",
    "run_astrometry",
    "run_photometry",
    "run_spectroscopy",
    "stack_choice_path",
]

StackChoice = StackConfigurationResult | str | os.PathLike[str]
"""The stack a run works on: a record from `processing.stacking.get_stack`,
or the path of a stack or image."""


def stack_choice_path(stack: StackChoice | None) -> str | None:
    """Give the file path of a ``stack=`` argument.

    Parameters
    ----------
    stack : `StackConfigurationResult`, `str`, `os.PathLike` or `None`
        The stack record or path.

    Returns
    -------
    path : `str` or `None`
        The path, or `None` when no stack was given.
    """
    if stack is None:
        return None
    if isinstance(stack, StackConfigurationResult):
        return stack.stacked_image or None
    return os.fspath(stack)


def _same_file(first: str | None, second: str | None) -> bool:
    """Say whether two paths name the same file.

    Parameters
    ----------
    first, second : `str` or `None`
        The paths.

    Returns
    -------
    same : `bool`
        `True` when both are given and point at the same file.
    """
    return bool(first and second) and os.path.realpath(str(first)) == os.path.realpath(str(second))


def _other_imaging_stack(target: Target, stack_path: str | None) -> str | None:
    """Give the stack path when it is a recorded stack other than the main one.

    Parameters
    ----------
    target : `Target`
        The target.
    stack_path : `str` or `None`
        The stack the caller chose.

    Returns
    -------
    path : `str` or `None`
        The path when it names one of the target's stacks by camera and optic
        that is not its main imaging stack, else `None`.
    """
    if not stack_path or _same_file(stack_path, target.stacking.stacked_image):
        return None
    recorded = (item.stacked_image for item in target.stacking.stacks_by_configuration.values())
    return stack_path if any(_same_file(stack_path, path) for path in recorded) else None


def _chosen_frames(frames: FrameChoice | None, target: Target) -> list[Any] | None:
    """Turn a ``frames=`` argument into frame records, checked as one set.

    Parameters
    ----------
    frames : `FrameChoice` or `None`
        The frames the caller chose.
    target : `Target`
        The target they belong to.

    Returns
    -------
    records : `list` [`FrameRecord`] or `None`
        The frames, or `None` when the caller chose none.
    """
    if frames is None:
        return None
    records = frames_from_choice(frames, target)
    check_frame_list(records, target_id=target.id)
    return records


def _run_stage(
    stage: str,
    target: Target,
    stage_options: dict[str, dict[str, Any]],
    *,
    storage: Any,
    config: AppConfiguration,
    drivers: Any = None,
    frame_selection: ProcessingFrameSelection | None = None,
    measured_at: datetime | None = None,
) -> ProcessTargetResult:
    """Run one stage, and any prerequisite it adds, as one job with a slot.

    Parameters
    ----------
    stage : `str`
        The stage, from `ANALYSIS_STAGES`.
    target : `Target`
        The target. The stage records its results on it.
    stage_options : `dict` [`str`, `dict`]
        Each stage's options (see `tasks.STAGE_OPTIONS`).
    storage : `AbstractCatalogAccess`
        The star catalog the stages read and write.
    config : `AppConfiguration`
        The settings, for the job slot count.
    drivers : `Drivers`, optional
        The plate solver and SIMBAD driver.
    frame_selection : `ProcessingFrameSelection`, optional
        The frames of the camera and optic analyzed.
    measured_at : `datetime`, optional
        The time stamped on the quality rows the stage stores.

    Returns
    -------
    result : `ProcessTargetResult`
        The stage's result, the prerequisites run and the stages skipped.
    """
    from astrometricslib.pipelines.tasks import run_target_stages

    slot = acquire_resource_slot(
        config, "job", config.get_max_concurrent_jobs(), work_name=f"{stage} {target.id}"
    )
    # The job is recorded first, so a run waiting for a slot shows in the job
    # list. The stages it runs join this job.
    with (
        registered_job(job_type="analysis", target_id=target.id) as job,
        job.holding_slot(slot, working_message=f"Running {stage.replace('_', ' ')}"),
    ):
        return run_target_stages(
            target,
            (stage,),
            stage_options,
            storage,
            drivers=drivers,
            frame_selection=frame_selection,
            measured_at=measured_at,
            run_prerequisites=True,
        )


def run_astrometry(
    target: Target,
    stack: StackChoice | None = None,
    *,
    storage: Any,
    config: AppConfiguration,
    drivers: Any = None,
    measured_at: datetime | None = None,
) -> ProcessTargetResult:
    """Plate solve a target's stack and record the stars it names.

    Parameters
    ----------
    target : `Target`
        The target. Its astrometry summary is replaced.
    stack : `StackChoice`, optional
        The stack or image to solve. Left out, the main imaging stack.
    storage : `AbstractCatalogAccess`
        The star catalog.
    config : `AppConfiguration`
        The settings.
    drivers : `Drivers`, optional
        The plate solver and SIMBAD driver.
    measured_at : `datetime`, optional
        The time stamped on the quality rows.

    Returns
    -------
    result : `ProcessTargetResult`
        The astrometry result under ``results["astrometry"]``.
    """
    path = stack_choice_path(stack)
    options = (
        {"astrometry": {"path": path}} if path and not _same_file(path, target.stacking.stacked_image) else {}
    )
    return _run_stage(
        "astrometry",
        target,
        options,
        storage=storage,
        config=config,
        drivers=drivers,
        measured_at=measured_at,
    )


def run_photometry(
    target: Target,
    frames: FrameChoice | None = None,
    stack: StackChoice | None = None,
    *,
    storage: Any,
    config: AppConfiguration,
    drivers: Any = None,
    measured_at: datetime | None = None,
) -> ProcessTargetResult:
    """Measure the brightness of a target's stars across its frames.

    Photometry reads astrometry's plate solution and never runs
    astrometry. Without a current solution of the main stack, or of the
    image ``stack`` names, the stage is skipped with the reason
    ``needs astrometry: ...``. With ``stack`` naming another camera's or
    optic's stack, that stack's solution is read and the frames of its
    camera and optic are measured. A path that is not one of the target's
    stacks is the solved image read when the target has no stack.

    Parameters
    ----------
    target : `Target`
        The target. Its photometry summary is replaced.
    frames : `FrameChoice`, optional
        The frames to measure. Left out, the frames of the analyzed stack's
        camera and optic.
    stack : `StackChoice`, optional
        The solved stack whose solution names the stars. Left out, the main
        stack.
    storage : `AbstractCatalogAccess`
        The star catalog.
    config : `AppConfiguration`
        The settings.
    drivers : `Drivers`, optional
        The plate solver and SIMBAD driver.
    measured_at : `datetime`, optional
        The time stamped on the quality rows.

    Returns
    -------
    result : `ProcessTargetResult`
        The photometry result under ``results["photometry"]``, or a
        ``"skipped"`` entry and the reason in ``stages_skipped``.
    """
    path = stack_choice_path(stack)
    other_stack = _other_imaging_stack(target, path)
    options: dict[str, dict[str, Any]] = {}
    if path and not _same_file(path, target.stacking.stacked_image):
        # Not a stage to run: the image whose solution photometry reads (see
        # `tasks.missing_astrometry_reason`).
        options["astrometry"] = {"path": path}
    chosen = _chosen_frames(frames, target)
    if chosen is not None:
        options["photometry"] = {"frames": chosen}
    selection = resolve_processing_frames(target, config, stack_path=other_stack)
    return _run_stage(
        "photometry",
        target,
        options,
        storage=storage,
        config=config,
        drivers=drivers,
        frame_selection=selection,
        measured_at=measured_at,
    )


def run_spectroscopy(
    target: Target,
    stack: StackChoice | None = None,
    *,
    max_stars: int | None = None,
    storage: Any,
    config: AppConfiguration,
    drivers: Any = None,
    measured_at: datetime | None = None,
) -> ProcessTargetResult:
    """Extract and classify the spectra of a target's spectral stack.

    Spectroscopy reads a plate solution and never runs astrometry: the
    solved imaging stack's of the same camera and optic, else the spectral
    stack's own when astrometry has solved it. Without either the stage is
    skipped with the reason ``needs astrometry: ...``. A target with no
    spectral data is skipped, with a ``{"status": "skipped", ...}`` entry,
    not an error.

    Parameters
    ----------
    target : `Target`
        The target. Its spectroscopy summary is replaced.
    stack : `StackChoice`, optional
        The spectral stack or image. Left out, the target's spectral stack.
    max_stars : `int`, optional
        The most stars to process, brightest first. Left out, every usable
        identified star.
    storage : `AbstractCatalogAccess`
        The star catalog.
    config : `AppConfiguration`
        The settings.
    drivers : `Drivers`, optional
        The plate solver and SIMBAD driver.
    measured_at : `datetime`, optional
        The time stamped on the quality rows.

    Returns
    -------
    result : `ProcessTargetResult`
        The spectroscopy result under ``results["spectroscopy"]``, or a
        ``"skipped"`` entry and the reason in ``stages_skipped``.
    """
    from astrometricslib.foundation.arguments import check_limit

    check_limit(max_stars, 1, None, name="max_stars")
    spectroscopy: dict[str, Any] = {}
    path = stack_choice_path(stack)
    if path:
        spectroscopy["path"] = path
    if max_stars is not None:
        spectroscopy["limit"] = max_stars
    selection = resolve_processing_frames(target, config)
    return _run_stage(
        "spectroscopy",
        target,
        {"spectroscopy": spectroscopy},
        storage=storage,
        config=config,
        drivers=drivers,
        frame_selection=selection,
        measured_at=measured_at,
    )


def record_spectral_variation(
    target: Target,
    frames: FrameChoice | None = None,
    *,
    storage: Any,
    config: AppConfiguration,
    drivers: Any = None,
    measured_at: datetime | None = None,
) -> ProcessTargetResult:
    """Follow each star's spectrum across a target's raw spectral frames.

    One observing night at a time; a night with fewer than two frames is
    skipped, with the reason in the result. Spectroscopy runs first when its
    result is missing or older than the spectral stack; it is skipped, and
    so is this stage, when it has no plate solution to read. The summary is
    kept in ``quality.spectroscopy_variation``; the stack's spectra stay as
    they are.

    Parameters
    ----------
    target : `Target`
        The target.
    frames : `FrameChoice`, optional
        The raw spectral frames to follow. Left out, the spectral frames of
        the analyzed camera.
    storage : `AbstractCatalogAccess`
        The star catalog.
    config : `AppConfiguration`
        The settings.
    drivers : `Drivers`, optional
        The plate solver and SIMBAD driver.
    measured_at : `datetime`, optional
        The time stamped on the quality rows.

    Returns
    -------
    result : `ProcessTargetResult`
        The result under ``results["spectral_variation"]``.
    """
    chosen = _chosen_frames(frames, target)
    options = {"spectral_variation": {"frames": chosen}} if chosen is not None else {}
    selection = resolve_processing_frames(target, config)
    return _run_stage(
        "spectral_variation",
        target,
        options,
        storage=storage,
        config=config,
        drivers=drivers,
        frame_selection=selection,
        measured_at=measured_at,
    )


def run_asteroid_search(
    target: Target,
    *,
    storage: Any,
    config: AppConfiguration,
    measured_at: datetime | None = None,
) -> ProcessTargetResult:
    """Search a target's stack and frames for moving objects.

    The search is experimental: its false-positive rate is not measured on
    real data, so a person should check what it finds.

    Parameters
    ----------
    target : `Target`
        The target. It needs a stack.
    storage : `AbstractCatalogAccess`
        The star catalog.
    config : `AppConfiguration`
        The settings.
    measured_at : `datetime`, optional
        The time stamped on the quality rows.

    Returns
    -------
    result : `ProcessTargetResult`
        The search's result under ``results["asteroids"]``.
    """
    return _run_stage("asteroids", target, {}, storage=storage, config=config, measured_at=measured_at)
