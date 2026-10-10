"""Purpose: Siril as the stacking program.

Description: `SirilStackingDriver` implements the `StackingDriver`
interface (`drivers/interfaces/stacking_driver.py`) on top of the Siril
session class (`ImageProcessing` in `siril_interface.py`). It calls the
session's `process_target` and returns what the session reported as a
`StackRunResult`, instead of leaving the report in the session for the
caller to fetch.

The ``registration`` names are Siril's spectral star detection settings:
``"standard"``, ``"relaxed"`` and ``"phase_correlation"`` (see
`SPECTRAL_STAR_DETECTION_COMMANDS` in `siril_interface.py`).
"""

import re
import shlex
import subprocess
from typing import Any

from astrometricslib.drivers.interfaces.stacking_driver import StackingDriver, StackRunResult, StackSettings


class SirilStackingDriver(StackingDriver):
    """Stacks frames by driving Siril through its headless script mode.

    Parameters
    ----------
    session : `ImageProcessing`, optional
        The Siril session to use. A new one is made when `None`.
    """

    name = "Siril"

    def __init__(self, session: Any | None = None) -> None:
        """Hold the Siril session, making one if none was given."""
        if session is None:
            from astrometricslib.drivers.siril_interface import ImageProcessing

            session = ImageProcessing()
        self.session = session
        self._version: str | None = None

    def version(self) -> str | None:
        """Ask Siril for its version.

        Returns
        -------
        version : `str` or `None`
            The version text, such as ``"1.4.4"``, or `None` when Siril
            cannot be run. The answer is kept after the first call.
        """
        if self._version is None:
            self._version = _siril_version(self.session.siril_executable)
        return self._version

    def read_stack_artifacts(self, stacked_path: str) -> dict[str, Any]:
        """Read Siril's registration sequence and rejection map for a stack.

        Returns
        -------
        artifacts : `dict`
            The per-frame registration results and the rejected pixel
            fraction (see `StackingDriver.read_stack_artifacts`).
        """
        from astrometricslib.drivers.siril_output_parsing import parse_seq_file
        from astrometricslib.pipelines.shared.quality.quality_metrics import measure_rejected_fraction

        stem = stacked_path.rsplit(".", 1)[0]
        return {
            "registration_frames": parse_seq_file(f"{stem}_Registration.seq"),
            "rejected_pixel_fraction": measure_rejected_fraction(stacked_path),
        }

    def stack_batch(
        self,
        frames: list[Any],
        target_id: str,
        output_file: str,
        log_file: str | None,
        is_spectral: bool,
        settings: StackSettings,
        registration: str | None = None,
        job_id: str | None = None,
    ) -> StackRunResult:
        """Stack frames that can be combined as one set, with Siril.

        Returns
        -------
        result : `StackRunResult`
            The stacked path and the session's diagnostics for this run.
        """
        options = settings.as_options()
        if registration is not None:
            options["spectral_star_detection"] = registration
        path = self.session.process_target(
            id=target_id,
            image_files=frames,
            output_file=output_file,
            log_file=log_file,
            is_spectral=is_spectral,
            job_id=job_id,
            **options,
        )
        return StackRunResult(
            stacked_path=path,
            diagnostics=dict(self.session.last_run_diagnostics),
            engine_name=self.name,
            engine_version=self._version,
        )


def _siril_version(siril_executable: str) -> str | None:
    """Read Siril's version from its command line.

    Parameters
    ----------
    siril_executable : `str`
        The command that starts Siril, as the driver stores it. A Flatpak
        command is run with its command-line program instead of the GUI.

    Returns
    -------
    version : `str` or `None`
        The first version number found, or `None` when Siril is missing or
        prints none.
    """
    parts = shlex.split(siril_executable)
    if "flatpak" in parts[0]:
        parts.insert(-1, "--command=siril-cli")
    try:
        completed = subprocess.run(
            [*parts, "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except OSError, subprocess.SubprocessError:
        return None
    match = re.search(r"siril\s+(\d+\.\d+(?:\.\d+)?)", completed.stdout + completed.stderr)
    return match.group(1) if match else None
