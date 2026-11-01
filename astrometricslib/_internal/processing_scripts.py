"""Purpose: Private `processing_scripts` names of `astrometricslib`.

Description: Four calls that only scripts inside `astrometricslib` and tests
use: `restore_excluded_frames`, `stack_subset`, `detect_point_sources` and
`tune_spectroscopy_calibration`. Each is a function that takes the
`ProcessingPipelines` as its first argument. Only `astrometricslib` itself,
and the tests of either library, may import this module. Each name is
imported from the module where its code lives today, so moving the code later
changes this file only.
"""

from astrometricslib.api.script_calls import (
    detect_point_sources,
    restore_excluded_frames,
    stack_subset,
    tune_spectroscopy_calibration,
)

__all__ = [
    "detect_point_sources",
    "restore_excluded_frames",
    "stack_subset",
    "tune_spectroscopy_calibration",
]
