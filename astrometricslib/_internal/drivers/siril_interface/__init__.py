"""Siril driver for Astrometrics.

Wraps the Siril headless CLI (via named pipes) to build working
directories, run calibration/registration/stacking scripts, and
retrieve the resulting stacked image.

The code is split by job, one module each:

- `script_builders`: the Siril command lists, as plain functions.
- `session`: the machine-wide slot lock, the named pipes, the launch and
  tear-down of the Siril process, and the parsing of its output.
- `staging`: the work folder, the staged frames and the master cache.
- `spectral_stacker`: the phase-correlation way to stack spectra.
- `imaging_stacker`: the run that stacks one target (`process_target`).
- `run_models`: the request and the state each phase hands to the next.
- `image_processing`: `ImageProcessing`, which joins them.

The names other code imports are re-exported here, so
``from astrometricslib._internal.drivers.siril_interface import ...``
works. Code that replaces a name for a test must replace it in the module
that uses it.
"""

from astrometricslib._internal.drivers.siril_interface.image_processing import ImageProcessing
from astrometricslib._internal.drivers.siril_interface.run_models import SirilStackRequest
from astrometricslib._internal.drivers.siril_interface.script_builders import (
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
from astrometricslib._internal.drivers.siril_interface.session import (
    SIRIL_PROCESS_LOCK_PATH,
    bind_lock_wait,
    siril_process_lock,
)
from astrometricslib._internal.drivers.siril_interface.spectral_stacker import (
    ALIGNED_SIRIL_SEQUENCE_NAME,
    find_calibrated_frame_paths,
)
from astrometricslib._internal.drivers.siril_interface.staging import (
    CALIBRATION_MASTER_CACHE_DIRECTORY_NAME,
    dominant_exposure,
    purge_stale_work_directories,
    representative_light_binning,
    representative_light_temperature_c,
    work_directory_name,
)

# Declares this package's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "ALIGNED_SIRIL_SEQUENCE_NAME",
    "CALIBRATION_BLOCKING_FLAGS_KEY",
    "CALIBRATION_MASTER_CACHE_DIRECTORY_NAME",
    "SIRIL_PROCESS_LOCK_PATH",
    "SIRIL_SCRIPT_PREAMBLE",
    "SPECTRAL_STAR_DETECTION_COMMANDS",
    "ImageProcessing",
    "SirilStackRequest",
    "bind_lock_wait",
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
    "siril_process_lock",
    "work_directory_name",
]
