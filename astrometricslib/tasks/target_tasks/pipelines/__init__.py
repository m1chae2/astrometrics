"""One module per analysis mode, dispatched by name.

`pipeline_tasks.analyze_target` looks up the mode the caller asked for
("astrometry", "spectroscopy", "photometry", or "asteroid_recovery") in
`PIPELINE_RUNNERS` and calls whichever runner it finds. Adding a fifth
analysis mode means adding a module here and one entry to this dict --
`pipeline_tasks.py` itself does not need to change.

Every runner takes the same five arguments (``target``, ``frames``,
``filter_type``, ``butler``, ``path``, plus ``**kwargs``) even though most
of them ignore some of it -- astrometry and spectroscopy never look at
``frames``/``filter_type``, and asteroid recovery does not even use
``butler``. One shared signature is what lets this dict dispatch on name
alone, instead of every call site needing to know which pipeline wants
which subset of arguments.
"""

from astrometricslib.tasks.target_tasks.pipelines.asteroid_recovery import run_asteroid_recovery_analysis
from astrometricslib.tasks.target_tasks.pipelines.astrometry import run_astrometry_analysis
from astrometricslib.tasks.target_tasks.pipelines.photometry import run_photometry_analysis
from astrometricslib.tasks.target_tasks.pipelines.spectroscopy import run_spectroscopy_analysis

PIPELINE_RUNNERS = {
    "astrometry": run_astrometry_analysis,
    "spectroscopy": run_spectroscopy_analysis,
    "photometry": run_photometry_analysis,
    "asteroid_recovery": run_asteroid_recovery_analysis,
}
