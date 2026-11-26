# Visualization

This folder renders a target's or star's stored data as plots and interactive image views: light curves, spectra, star fields, and focus history. Nothing here computes new science results. It only displays results the pipelines already produced. A light curve is a star's brightness plotted against time. A spectrum is the star's light spread out by wavelength.

Terms are defined in the [glossary](../../documentation/Glossary.md). This document also defines each term the first time it appears.

## What each file is for

- `helpers.py` — the high-level entry points other code calls. It has one function per plot type: astrometry star field, photometry light curve, spectroscopy spectrum, asteroid detection tracks and a combined target dashboard.
- `image_display.py` — the overlays of `astrometrics.visualization.render_fits`. It finds the stars the astrometry run's star finder detects and the stack's named stars that have photometry, as Image Processing's Astrometry overlay does, and draws them on the PNG (`kind="image"`) or on the Matplotlib figure (`kind="figure"`) for scripts and notebooks. Both kinds use the same marks and colors.
- `sky_placement.py` — places a target's stack picture on the sky for a sky chart, behind `astrometrics.visualization.stack_sky_picture_files` and `place_stack_picture_on_sky`. It chooses the picture (the color picture, else the preview) and the stretched stack, reads only the stretched stack's header, and maps a 17 x 17 grid of picture points to right ascension and declination with the full plate solution, including the SIP distortion terms. A picture pixel is the same pixel of the stretched stack, first row at the top. A header with only the mount's pointing is not a plate solution, so that stack is not placed.
- `focus_plots.py` — plots showing how well the telescope stayed in focus over a session. They use each frame's focuser temperature and position. The focuser is the motor that moves the camera along the optical axis to bring stars into focus. Each frame record keeps the focuser's position and temperature at the time of the exposure.
- `star_field_visualization.py` — the internal orchestrator for the interactive 2D star field view. It coordinates the overlay layers below.
- `interaction_handler.py` — manages mouse events and interactive state for the star field view.
- `layers/` — one file per overlay drawn on the star field. The overlays show detected stars, photometry markers, spectroscopy dispersion traces and extracted spectra, moving-object tracks, the underlying image itself, and the selected star. A dispersion trace is a line along which a grating spreads each star's light. `star_selection_overlay.py` draws crosshair markers on the star the user picked. See [layers/README.md](layers/README.md).
- `geometry.py` — geometry helpers for rotated visualization elements, such as a box or line drawn at an arbitrary angle.
- `spectroscopy_field_access.py` — reads a single field off a star's spectroscopy result. It works whether the star is a real `StellarObject` or a plain dict, so the plotting code does not need to know which.
- `visualization_config.py` — shared configuration tokens (colors, sizes, default percentiles) for the whole suite. The percentiles set the display stretch. Pixel values below the 1st percentile show as black. Values above the upper percentile (default 99.5) show as white. A few very bright stars therefore do not leave the rest of the image dark.

For exact behavior, read the code.
