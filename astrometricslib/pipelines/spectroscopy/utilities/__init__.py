"""Standalone spectroscopy tools used outside the per-run pipeline.

Unlike `pipeline.py`/`runner.py`/`batch.py`/`frame_analysis.py`, nothing
here is called as part of processing a target's spectra. These are
separate tools: one derives and persists camera calibration constants
ahead of time, the other checks stack alignment quality after the fact.
"""
