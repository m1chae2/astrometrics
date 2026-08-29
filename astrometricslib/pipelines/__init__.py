"""One module per analysis mode, dispatched by name.

`dispatch.analyze_target` looks up the mode the caller asked for
("astrometry", "spectroscopy", "photometry", or "asteroid_recovery") in
`PIPELINE_RUNNERS` and calls whichever runner it finds. Adding a fifth
analysis mode means adding a module here and one entry to this dict --
`dispatch.py` itself does not need to change.

Every runner takes the same five arguments (``target``, ``frames``,
``filter_type``, ``catalog_access``, ``path``, plus ``**kwargs``) even
though most of them ignore some of it -- astrometry and spectroscopy
never look at ``frames``/``filter_type``, and asteroid recovery does not
even use ``catalog_access``. One shared signature is what lets this dict
dispatch on name alone, instead of every call site needing to know which
pipeline wants which subset of arguments.
"""

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
