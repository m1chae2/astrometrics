"""Measuring how good an image is, independent of any one pipeline.

Background level, saturation, per-target frame statistics, and the
FITS-file-level quality checks used before stacking -- none of these
own an external resource (a subprocess, a database file) the way a
driver does. They're pure measurement on already-loaded pixel data,
needed by stacking, spectroscopy, and the API layer alike, so they
live here rather than inside `drivers/` or any single pipeline.
"""
