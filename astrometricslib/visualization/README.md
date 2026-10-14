# Visualization

This folder renders a target's or star's stored data as plots and interactive image views: light curves, spectra, star fields, and focus history. Nothing here computes new science results — it only displays results the pipelines already produced.

## What each file is for

- `helpers.py` — the high-level entry points other code calls: one function per plot type (astrometry star field, photometry light curve, spectroscopy spectrum, asteroid detection tracks, a combined target dashboard).
- `focus_plots.py` — plots showing how well the telescope stayed in focus over a session, from each frame's focuser temperature and position.
- `star_field_visualization.py` — the internal orchestrator for the interactive 2D star field view, coordinating the overlay layers below.
- `interaction_handler.py` — manages mouse events and interactive state for the star field view.
- `layers/` — one file per overlay drawn on the star field: detected stars, photometry markers, spectroscopy dispersion traces and extracted spectra, moving-object tracks, and the underlying image itself.
- `geometry.py` — geometry helpers for rotated visualization elements (drawing a box or line at an arbitrary angle).
- `spectroscopy_field_access.py` — reads a single field off a star's spectroscopy result whether the star is a real `StellarObject` or a plain dict, so the plotting code does not need to know which.
- `visualization_config.py` — shared configuration tokens (colors, sizes, default percentiles) for the whole suite.

For exact behavior, read the code — the code is always the source of truth.
