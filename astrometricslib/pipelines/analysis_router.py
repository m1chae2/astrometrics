"""Picks which analysis pipeline runs, by name.

`analyze_target` looks up the mode the caller asked for ("astrometry",
"spectroscopy", "photometry", or "asteroid_recovery") in
`PIPELINE_RUNNERS` and calls whichever runner it finds -- adding a
fifth analysis mode means adding a module under `pipelines/` and one
entry to this dict; nothing else here needs to change.

Every runner takes the same five arguments (``target``, ``frames``,
``filter_type``, ``catalog_access``, ``path``, plus ``**kwargs``) even
though most of them ignore some of it -- astrometry and spectroscopy
never look at ``frames``/``filter_type``, and asteroid recovery does not
even use ``catalog_access``. One shared signature is what lets this dict
dispatch on name alone, instead of every call site needing to know which
pipeline wants which subset of arguments.
"""

from typing import Any

from astrometricslib.drivers.job_logging import registered_job
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.asteroid_recovery.runner import run_asteroid_recovery_analysis
from astrometricslib.pipelines.astrometry.runner import run_astrometry_analysis
from astrometricslib.pipelines.photometry.runner import run_photometry_analysis
from astrometricslib.pipelines.spectroscopy.runner import run_spectroscopy_analysis

PIPELINE_RUNNERS = {
    "astrometry": run_astrometry_analysis,
    "spectroscopy": run_spectroscopy_analysis,
    "photometry": run_photometry_analysis,
    "asteroid_recovery": run_asteroid_recovery_analysis,
}


def analyze_target(
    target: Target,
    frames: list[FrameRecord] | None = None,
    pipeline_type: str = "astrometry",
    filter_type: str | None = None,
    catalog_access=None,  # ruff: ignore[missing-type-function-argument]
    register_job: bool = True,
    path: str | None = None,
    **kwargs,  # ruff: ignore[missing-type-kwargs]
) -> dict[str, Any]:
    """Run a specific analysis pipeline on the given target.

    You can ask it to run "astrometry" (finding star positions),
    "spectroscopy" (light spectrum), "photometry" (brightness changes),
    or "asteroid_recovery" (finding moving rocks).

    Parameters
    ----------
    register_job : bool, optional
        Set to True (default) if you want this run to automatically
        show up in the user interface's job tracker. Set to False if
        you are calling this from a tool that already tracks its own jobs
        (to prevent double-counting).

    Returns
    -------
    result : dict
        A dictionary with the final results and status info.

    Raises
    ------
    ValueError
        If you ask for an unknown pipeline type, or if we don't have
        the right images needed to run it.
    """
    if catalog_access is None:
        from astrometricslib.drivers.catalog_access import CatalogAccess

        catalog_access = CatalogAccess()

    # Record this run in the job list so work started from a script,
    # notebook, or the command line shows up in the user interface the
    # same way a run started from the interface does. Skipped when
    # register_job=False -- see that parameter's docstring.
    with registered_job(
        enabled=register_job,
        job_type="analysis",
        target_id=target.id,
        completed_message=f"[{target.id}] Analysis completed successfully.",
        failed_message=f"[{target.id}] Analysis failed.",
    ) as job:
        job.info(
            f"[{target.id}] Analysis job started for {target.id} (type: {pipeline_type}, Job: {job.job_id})"
        )

        # Resolve image path for astrometry/spectroscopy modes
        # if not explicitly provided
        if not path and pipeline_type in ("astrometry", "spectroscopy"):
            if frames:
                path = frames[0].path
            elif pipeline_type == "spectroscopy" and target.stacked_spectral_target:
                path = target.stacked_spectral_target
            elif pipeline_type == "astrometry" and target.stacked_image:
                path = target.stacked_image
            elif target.frames:
                path = target.frames[0].path
            else:
                raise ValueError(
                    f"No frames or stacked image available for {pipeline_type} analysis"
                    f" on target {target.id}."
                )

        return _run_analysis_pipeline_match(
            target, frames, pipeline_type, filter_type, catalog_access, path, **kwargs
        )


def _run_analysis_pipeline_match(
    target,  # ruff: ignore[missing-type-function-argument]
    frames,  # ruff: ignore[missing-type-function-argument]
    pipeline_type,  # ruff: ignore[missing-type-function-argument]
    filter_type,  # ruff: ignore[missing-type-function-argument]
    catalog_access,  # ruff: ignore[missing-type-function-argument]
    path,  # ruff: ignore[missing-type-function-argument]
    **kwargs,  # ruff: ignore[missing-type-kwargs]
) -> dict[str, Any]:
    runner = PIPELINE_RUNNERS.get(pipeline_type)
    if runner is None:
        raise ValueError(f"Unknown analysis type: {pipeline_type}")
    return runner(target, frames, filter_type, catalog_access, path, **kwargs)
