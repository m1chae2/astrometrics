"""Purpose: Siril as the stacking program.

Description: `SirilStackingDriver` implements the `StackingDriver`
interface (`drivers/interfaces/stacking_driver.py`) on top of the Siril
session class (`ImageProcessing`, in `siril.stacking.image_processing`). It
calls the session's `process_target` and returns what the session reported as a
`StackRunResult`, instead of leaving the report in the session for the
caller to fetch.

The ``registration`` names are Siril's spectral star detection settings:
``"standard"``, ``"relaxed"`` and ``"phase_correlation"`` (see
`SPECTRAL_STAR_DETECTION_COMMANDS` in `siril/stacking/script_builders.py`).
"""

from typing import Any

from astrometricslib._internal.drivers.interfaces.stacking_driver import (
    PipelineSteps,
    StackingDriver,
    StackRunResult,
    StackSettings,
)
from astrometricslib._internal.drivers.siril.core.version import siril_version


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
            from astrometricslib._internal.drivers.siril.stacking import ImageProcessing

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
            self._version = siril_version(self.session.siril_executable)
        return self._version

    def open_in_viewer(self, file_path: str) -> None:
        """Open a file in the Siril program's window.

        The Siril command comes from the session. Nothing is stacked. Siril
        keeps running after the caller exits.

        Parameters
        ----------
        file_path : `str`
            The FITS file to open. An empty string opens Siril alone.
        """
        from astrometricslib._internal.drivers.siril.core.session import launch_siril_gui

        launch_siril_gui(self.session.siril_executable, file_path)

    def stop_running_programs(self) -> None:
        """Stop every Siril process this program started, in any session.

        Each Siril runs as the leader of its own process group, and the whole
        group is stopped. Nothing is matched by name, so a Siril window a
        person opened by hand is not touched.
        """
        from astrometricslib._internal.drivers.siril.core.session import stop_all_siril_processes

        stop_all_siril_processes()

    def read_stack_artifacts(self, stacked_path: str) -> dict[str, Any]:
        """Read Siril's registration sequence and rejection map for a stack.

        Returns
        -------
        artifacts : `dict`
            The per-frame registration results and the rejected pixel
            fraction (see `StackingDriver.read_stack_artifacts`).
        """
        from astrometricslib._internal.drivers.siril.core.output_parsing import (
            measure_rejected_fraction,
            parse_seq_file,
            parse_seq_inclusion,
        )

        stem = stacked_path.rsplit(".", 1)[0]
        return {
            "registration_frames": parse_seq_file(f"{stem}_Registration.seq"),
            "registration_included": parse_seq_inclusion(f"{stem}_Registration.seq"),
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
        steps: PipelineSteps | None = None,
    ) -> StackRunResult:
        """Stack frames that can be combined as one set, with Siril.

        Returns
        -------
        result : `StackRunResult`
            The stacked path and the session's diagnostics for this run.
        """
        from astrometricslib._internal.drivers.siril.stacking.run_models import SirilStackRequest

        options = settings.as_options()
        if registration is not None:
            options["spectral_star_detection"] = registration
        path = self.session.process_target(
            SirilStackRequest(
                id=target_id,
                image_files=frames,
                output_file=output_file,
                log_file=log_file,
                is_spectral=is_spectral,
                job_id=job_id,
                steps=steps,
                **options,
            )
        )
        return StackRunResult(
            stacked_path=path,
            diagnostics=dict(self.session.last_run_diagnostics),
            engine_name=self.name,
            engine_version=self._version,
        )
