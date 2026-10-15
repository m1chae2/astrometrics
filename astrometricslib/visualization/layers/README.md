# Visualization layers

This folder holds the overlays drawn on top of a plotted image in the interactive star field view. Each file is one overlay class.

Terms are defined in the [glossary](../../../documentation/library_design/Astrometrics_Library_Architecture.md#appendix-a-glossary). This document also defines each term the first time it appears.

## What each file is for

- `image_overlay.py` — `ImageOverlay`, which draws the 2D FITS image array with percentile-based intensity clipping.
- `star_overlay.py` — `StarOverlay`, which draws star centroids and selection circles, with catalog labels and limit filtering.
- `star_selection_overlay.py` — `StarSelectionOverlay`, which highlights the active star selection with crosshair markers.
- `photometry_overlay.py` — `PhotometryOverlay`, which draws time-series differential light curves and Lomb-Scargle periodograms.
- `spectrum_overlay.py` — `SpectrumOverlay`, which draws 1D wavelength spectra and absorption line overlays.
- `dispersion_overlay.py` — `DispersionOverlay`, which draws the rotated extraction boxes for slitless grating frames.
- `track_overlay.py` — `TrackOverlay`, which draws the paths of moving-object candidates. A candidate is a path across several raw frames, so the overlay projects each detection's sky coordinates into the displayed image's WCS (World Coordinate System) before drawing.

For exact behavior, read the code.
