# Driver interfaces

This folder holds one abstract base class (`*Driver`) for each outside program or service the library uses. The rest of the library depends on these interfaces, and each concrete driver in `drivers/` implements one.

Terms are defined in the [glossary](../../../documentation/library_design/Astrometrics_Library_Architecture.md#appendix-a-glossary). This document also defines each term the first time it appears.

## What each file is for

- `stacking_driver.py` — `StackingDriver`, the interface to a stacking program (the "engine" that calibrates, registers and combines frames; Siril is the one in use). A driver takes a `StackSettings` and returns a `StackRunResult`. `PipelineSteps` carries the stacking pipeline's own steps into the driver, so the driver never imports pipeline code.
- `plate_solve_driver.py` — `PlateSolveDriver`, the interface to a plate solver (Astrometry.net is the one in use). A plate solver works out which part of the sky an image shows. The driver returns a `PlateSolveHeader`, a FITS header that may carry the fit residual and the matched-star count.
- `simbad_driver.py` — `SimbadDriver`, the interface to the SIMBAD database of named astronomical objects.
- `gaia_xp_driver.py` — `GaiaXpDriver`, the interface to Gaia DR3's low-resolution XP spectra, which serve as an outside reference for a spectrum the library has calibrated itself.
- `reddening_driver.py` — `ReddeningDriver`, the interface to a catalog of interstellar reddening values (E(B-V), the color excess that dust adds to a star's light).

For exact behavior, read the code.
