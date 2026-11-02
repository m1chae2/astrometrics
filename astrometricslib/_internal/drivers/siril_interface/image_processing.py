"""The Siril driver class: `ImageProcessing`.

Joins the layers of the Siril driver (session, staging, spectral stacker and
imaging stacker) into one class and sets up the settings they share.
"""

from typing import Any

from astrometricslib._internal.drivers.siril_interface.imaging_stacker import SirilImagingStacker
from astrometricslib._internal.drivers.siril_interface.run_models import empty_run_diagnostics
from astrometricslib._internal.drivers.siril_interface.session import _active_image_processing_instances
from astrometricslib.foundation.paths import siril_work_directory


class ImageProcessing(SirilImagingStacker):
    """Drive Siril to calibrate, register, and stack target frames.

    Builds the working directory Siril expects, launches Siril in
    headless mode over named pipes, and copies the resulting stacked
    image (and optional diagnostics) back out to the target library.
    """

    def __init__(self, config=None, calibration_library=None, job_repository=None) -> None:  # ruff: ignore[missing-type-function-argument]
        """Initialize the ImageProcessing class with optional dependencies."""
        from astrometricslib._internal.drivers.calibration_library import CalibrationLibrary
        from astrometricslib.foundation.config import get_configuration

        self.config = config or get_configuration()
        if calibration_library is None:
            self.calibration_library = CalibrationLibrary(app_config=self.config)
            self.calibration_library.load_library()
        else:
            self.calibration_library = calibration_library
        self.siril_executable = self.config.get_siril_executable()
        # On the local disk, not in the RAM-backed system temp folder.
        self.workdir = siril_work_directory()
        self.subprocesses = []
        self.gui_launched = False
        self.job_repository = job_repository
        # Reset at the start of every process_target call; initialized
        # here too so build_directories can also be called standalone
        # (as existing tests do) without requiring a prior
        # process_target call.
        self.last_run_diagnostics: dict[str, Any] = empty_run_diagnostics()
        _active_image_processing_instances.add(self)
