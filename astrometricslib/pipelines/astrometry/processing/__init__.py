"""Astrometry's processing stage: turning detections into named stars.

Plate-solving a reference frame and matching its detected sources
against SIMBAD/Gaia, scoped to one session's reference frame
(`session_identification.py`). The main per-target entry point,
`star_identifier.py`, stays at the package root -- see its own
docstring for why.
"""
