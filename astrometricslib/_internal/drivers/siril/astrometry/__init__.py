"""Purpose: Siril's plate solve.

Description: `plate_solve` writes Siril's ``platesolve`` command. Today the
preview's colour calibration uses it to solve a stack before Siril's
photometric colour calibration. The app's own plate-solve driver (behind the
``PlateSolveDriver`` interface, for image astrometry and mount centering)
will be built on the same module.
"""
