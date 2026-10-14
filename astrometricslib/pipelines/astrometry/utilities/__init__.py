"""Standalone astrometry tools used outside the per-run pipeline.

Unlike `pipeline.py`/`runner.py`/`star_identifier.py`, nothing here is
called as part of solving and identifying stars in a single image. This
is a batch tool that pre-fills the local Gaia cache for every target
before parallel processing starts, so the workers don't all hit the
remote catalog server at once and get blocked.
"""
