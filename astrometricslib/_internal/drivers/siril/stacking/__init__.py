"""Purpose: Siril's calibrate, register and stack, for the stacking pipeline.

Description: Builds the work folder Siril expects, runs Siril's calibration,
registration and stacking commands in a pipe session (`core.session`), and
copies the stacked image (and its diagnostics) back to the library.

The code is split by job, one module each:

- `script_builders`: the Siril command lists, as plain functions.
- `staging`: the work folder, the staged frames and the master cache.
- `spectral_stacker`: the phase-correlation way to stack spectra.
- `imaging_stacker`: the run that stacks one target (`process_target`).
- `run_models`: the request and the state each phase hands to the next.
- `image_processing`: `ImageProcessing`, which joins them.
- `stacking_driver`: `SirilStackingDriver`, the `StackingDriver` the
  pipeline calls.
- `comparison_scripts`: short register and stack scripts for the library's
  study scripts.

The names other code imports most are re-exported here, so
``from astrometricslib._internal.drivers.siril.stacking import ...`` works.
Code that replaces a name for a test must replace it in the module that uses
it. The slot lock is `core.lock`.
"""

from astrometricslib._internal.drivers.batch_calibration import (
    dominant_exposure,
    representative_light_binning,
    representative_light_temperature_c,
)
from astrometricslib._internal.drivers.siril.stacking.image_processing import ImageProcessing
from astrometricslib._internal.drivers.siril.stacking.run_models import SirilStackRequest
from astrometricslib._internal.drivers.siril.stacking.script_builders import (
    CALIBRATION_BLOCKING_FLAGS_KEY,
    SIRIL_SCRIPT_PREAMBLE,
    SPECTRAL_STAR_DETECTION_COMMANDS,
    build_bias_master_commands,
    build_dark_master_commands,
    build_flat_master_commands,
    build_single_light_commands,
    calibration_count_flags,
    light_calibration_flags,
)
from astrometricslib._internal.drivers.siril.stacking.spectral_stacker import (
    ALIGNED_SIRIL_SEQUENCE_NAME,
    find_calibrated_frame_paths,
)
from astrometricslib._internal.drivers.siril.stacking.staging import (
    CALIBRATION_MASTER_CACHE_DIRECTORY_NAME,
    purge_stale_work_directories,
    work_directory_name,
)

# Declares this package's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "ALIGNED_SIRIL_SEQUENCE_NAME",
    "CALIBRATION_BLOCKING_FLAGS_KEY",
    "CALIBRATION_MASTER_CACHE_DIRECTORY_NAME",
    "SIRIL_SCRIPT_PREAMBLE",
    "SPECTRAL_STAR_DETECTION_COMMANDS",
    "ImageProcessing",
    "SirilStackRequest",
    "build_bias_master_commands",
    "build_dark_master_commands",
    "build_flat_master_commands",
    "build_single_light_commands",
    "calibration_count_flags",
    "dominant_exposure",
    "find_calibrated_frame_paths",
    "light_calibration_flags",
    "purge_stale_work_directories",
    "representative_light_binning",
    "representative_light_temperature_c",
    "work_directory_name",
]
