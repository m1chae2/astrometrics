"""Astrometry's processing stage: turning detections into named stars.

Takes the sources `pre_processing/source_detection.py` found and the WCS
the plate solver produced, and matches them against SIMBAD and Gaia to
give each one a stable, permanent name (`star_identifier.py`).
"""
