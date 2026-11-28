"""Purpose: The Siril driver, the only code that starts Siril or writes to it.

Description: The driver is laid out like the pipelines it serves:

- `core`: what every run needs: finding the program, the private settings
  file, the shared slot lock, short script runs, the long pipe session of a
  stack, the version, and reading Siril's output.
- `stacking`: calibrate, register and stack, for the stacking pipeline.
- `astrometry`: plate solve.
- `preview`: colour calibration, stretch and save, and StarNet, for the
  stack preview.

Each operation lives where its main user is and is called from there by the
other pipelines: the preview's colour calibration uses `astrometry`'s plate
solve. A rule test (``siril/test/test_siril_driver_rules.py``) fails when any
other module starts Siril or holds Siril commands.

This package file imports nothing, so a module that needs one part of the
driver does not load the others.
"""
