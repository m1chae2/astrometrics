"""The names of the analysis stages, and which of them a caller may choose.

This is the one list of stage names. `tasks.run_target_stages` runs them in
this order, `ProcessingPipelines.process_target` checks a caller's stages
against it, and the app and its AI tools offer the selectable subset. The
module holds only names, so the public API can show the lists without loading
the pipelines.
"""

ANALYSIS_STAGES = ("astrometry", "photometry", "spectroscopy", "spectral_variation", "asteroids")
"""The analysis stages `run_target_stages` can run, in the order run."""

SELECTABLE_ANALYSIS_STAGES = tuple(stage for stage in ANALYSIS_STAGES if stage != "asteroids")
"""The stages the app and its AI tools may ask for by name, in running order.

The asteroid search is left out: it is experimental, and its false-positive
rate is not measured on real data, so only a script asks for it.
"""

DEFAULT_ANALYSIS_STAGES = ("astrometry", "photometry", "spectroscopy")
"""The stages that run for one target when a library caller names none."""
