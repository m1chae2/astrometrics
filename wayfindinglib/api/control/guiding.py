"""Purpose: `control.guiding`, the guide camera, pulses and guider models.

Description: Sends guide pulses, takes guide exposures, computes the
pulse that corrects a measured drift, follows the guiding going on now
(its root-mean-square (RMS) error, through the guiding driver the
``guiding_protocol`` setting picks) and runs this app's own guide loop.
It runs the guider calibrations (the pulse-to-pixel calibration, the
declination backlash probe and the guide exposure test) and refits the
mount's periodic error model from recorded guiding samples. `status`
reads the saved calibration, the saved periodic error model, the guide
camera's plate scale and the live guiding. Pulses and guide exposures
need `AUTOGUIDING` to be `AUTHORITATIVE`.
"""

import threading
from typing import Any

from astrometricslib import ConfigurationError, ExternalServiceError
from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.models.control_status import GuidingStatus
from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.models.session.correction_result import GuidingCorrection
from wayfindinglib.models.session.guide_exposure_ladder import GuideExposureLadder
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.models.session.telemetry import GuidingSpectrumAnalysis
from wayfindinglib.tasks.control_tasks.calibration_routines import (
    BacklashCalibrationSteps,
    GuiderCalibrationSteps,
)

__all__ = ["GuidingControl"]

STATUS_SECTIONS = ("calibration", "spectrum_analysis", "plate_scale", "live")
"""Sections `GuidingControl.status` can read."""

SAVED_SECTIONS = ("calibration", "spectrum_analysis", "plate_scale")
"""The sections `GuidingControl.status` reads when none are named. They
need no device."""


class GuidingControl(ControlChild):
    """Operate the guide camera and the guider's saved models."""

    def status(self, include: list[str] | None = None) -> GuidingStatus:
        """Read the saved guider models, and the guiding going on now.

        Parameters
        ----------
        include : `list` [`str`], optional
            Sections to read: ``calibration`` (the pulse-to-pixel
            calibration), ``spectrum_analysis`` (the mount's periodic error
            and backlash model), ``plate_scale`` (the guide camera's
            arcseconds per pixel) and ``live`` (whether guiding runs, its
            root-mean-square (RMS) error and newest samples). The first
            three when omitted; they need no device. ``live`` first reads
            what the guider measured since the last read, through the
            guiding driver the active telescope's ``guiding_protocol``
            names, and records those samples in the log database.

        Returns
        -------
        status : `GuidingStatus`
            The sections read. The others stay `None`.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations, live_guiding

        sections = hardware_operations.check_sections(
            include if include is not None else list(SAVED_SECTIONS), STATUS_SECTIONS
        )
        status = GuidingStatus(sections=sections)
        if "calibration" in sections:
            status.calibration = self._context.active_guider_calibration()
        if "spectrum_analysis" in sections:
            status.spectrum_analysis = self._context.active_guiding_spectrum_analysis()
        if "plate_scale" in sections:
            status.plate_scale_arcsec_per_px = self._context.guider_plate_scale_arcsec_per_px()
        if "live" in sections:
            live_guiding.poll(self._context)
            status.live = self._context.live_guiding.status()
        return status

    def run_loop(
        self,
        stop: threading.Event,
        exposure_seconds: float | None = None,
        gain: float | None = None,
    ) -> None:
        """Run this app's guide loop until `stop` is set or tracking is lost.

        Blocks, so run it on its own thread. Each cycle takes a guide
        exposure, measures the guide error and sends the correction
        pulses, through the guiding driver the active telescope's
        ``guiding_protocol`` names. Only the ``simulator`` driver runs a
        loop today: PHD2 runs its own, and the ``internal`` driver cannot
        measure the guide star yet. `status(include=["live"])` shows the
        progress.

        Parameters
        ----------
        stop : `threading.Event`
            Set it to end the loop after the current cycle.
        exposure_seconds : `float`, optional
            Guide exposure length. The last one used when omitted (1 s
            at first).
        gain : `float`, optional
            Guide camera gain. The last one used when omitted.

        Notes
        -----
        Raises `ConflictError` if the mount does not track when the loop
        starts or the guider runs its own loop, and `ConfigurationError`
        if the guiding driver cannot run this loop. Pulses and guide
        exposures need `AUTOGUIDING` to be `AUTHORITATIVE`.
        """
        from wayfindinglib.tasks.control_tasks import live_guiding

        live_guiding.run_loop(self._context, stop, exposure_seconds=exposure_seconds, gain=gain)

    def pulse(self, direction: str, duration_ms: float) -> bool:
        """Send one guide pulse to the mount.

        Parameters
        ----------
        direction : `str`
            ``"north"``, ``"south"``, ``"east"`` or ``"west"``.
        duration_ms : `float`
            Pulse length in milliseconds.

        Returns
        -------
        success : `bool`
            Whether the pulse command was accepted.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.pulse(self._context, direction, duration_ms)

    def expose(self, exposure_seconds: float, gain: float | None = None) -> Any:
        """Take one exposure with the guide camera.

        Parameters
        ----------
        exposure_seconds : `float`
            Exposure length in seconds.
        gain : `float`, optional
            Camera gain to set first. `None` leaves it alone.

        Returns
        -------
        result : `Any`
            The guide camera driver's exposure result.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.guide_expose(self._context, exposure_seconds, gain=gain)

    def get_image(self) -> Any:
        """Return the guide camera's last image.

        Returns
        -------
        image : `Any`
            The last image data the guide camera sent.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.guide_image(self._context)

    def drain_external_pulses(self) -> list[dict[str, Any]]:
        """Return and clear the guide pulses another program sent.

        Lets this app watch guiding that KStars/Ekos or PHD2 commands
        directly on the mount.

        Returns
        -------
        pulses : `list` [`dict`]
            Guide pulses seen since the last call.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.drain_external_pulses(self._context)

    def compute_correction(
        self,
        comparison_input_id: str,
        drift_x_px: float,
        drift_y_px: float,
        elapsed_guiding_seconds: float | None = None,
        dec_direction_reversal: bool = False,
    ) -> GuidingCorrection:
        """Compute the guide pulse on each axis that corrects a measured drift.

        Computes only; nothing is sent to the mount. The saved periodic
        error model of the active mount is fed forward automatically.

        Parameters
        ----------
        comparison_input_id : `str`
            Id of the guide frame this correction is for.
        drift_x_px, drift_y_px : `float`
            The guide star's measured drift in pixels.
        elapsed_guiding_seconds : `float`, optional
            Seconds since this guiding run began, for the periodic error
            feed-forward.
        dec_direction_reversal : `bool`, optional
            Whether this declination pulse reverses the previous one, for
            the backlash feed-forward.

        Returns
        -------
        correction : `GuidingCorrection`
            The signed pulse on each axis.

        Raises
        ------
        ConfigurationError
            If no guider calibration is saved for the active telescope and
            camera. Guiding never assumes a default calibration.
        """
        from wayfindinglib.tasks.control_tasks.guiding_correction import compute_guiding_correction

        calibration = self._context.active_guider_calibration()
        if calibration is None:
            raise ConfigurationError("No GuiderCalibration exists for the active telescope/camera pairing")
        return compute_guiding_correction(
            comparison_input_id,
            drift_x_px,
            drift_y_px,
            calibration,
            self._context.correction_config,
            mount_model=self._context.active_guiding_spectrum_analysis(),
            elapsed_guiding_seconds=elapsed_guiding_seconds,
            dec_direction_reversal=dec_direction_reversal,
        )

    def run_calibration(
        self,
        steps: GuiderCalibrationSteps,
        calibration_id: str,
        camera_id: str,
        telescope_id: str,
        arcsec_per_pixel: float,
        ra_pulse_duration_sec: float = 3.0,
        dec_pulse_duration_sec: float = 3.0,
    ) -> GuiderCalibration:
        """Send a known pulse on each axis, measure the move, and save it.

        Parameters
        ----------
        steps : `GuiderCalibrationSteps`
            The pulse and star-measurement operations to use.
        calibration_id : `str`
            Id for the new calibration.
        camera_id, telescope_id : `str`
            The equipment pairing the calibration belongs to.
        arcsec_per_pixel : `float`
            Guide camera plate scale (see `status(include=["plate_scale"])`).
        ra_pulse_duration_sec, dec_pulse_duration_sec : `float`, optional
            Length of the test pulse on each axis.

        Returns
        -------
        calibration : `GuiderCalibration`
            The measured and saved calibration.
        """
        from wayfindinglib.tasks.control_tasks.calibration_routines import run_guider_calibration

        calibration = run_guider_calibration(
            steps,
            calibration_id,
            camera_id,
            telescope_id,
            arcsec_per_pixel,
            ra_pulse_duration_sec,
            dec_pulse_duration_sec,
        )
        self.save_calibration(calibration)
        return calibration

    def run_backlash_calibration(
        self,
        steps: BacklashCalibrationSteps,
        settle_direction: str = "north",
        reversed_direction: str = "south",
        settle_pulse_sec: float = 1.0,
        reversal_test_pulse_sec: float = 0.05,
        max_test_pulses: int = 40,
        motion_detection_threshold_px: float = 0.5,
    ) -> float:
        """Measure declination backlash by reversing direction gently.

        Not saved here: fold the result into the mount's periodic error
        model with `save_spectrum_analysis`.

        Parameters
        ----------
        steps : `BacklashCalibrationSteps`
            The pulse and star-measurement operations to use.
        settle_direction, reversed_direction : `str`, optional
            The direction to settle in, then the opposite direction.
        settle_pulse_sec : `float`, optional
            Length of the settling pulses.
        reversal_test_pulse_sec : `float`, optional
            Length of each small probing pulse.
        max_test_pulses : `int`, optional
            Most probing pulses before giving up.
        motion_detection_threshold_px : `float`, optional
            Star movement that counts as the mount responding.

        Returns
        -------
        backlash_estimate_ms : `float`
            The measured reversal delay in milliseconds.
        """
        from wayfindinglib.tasks.control_tasks.calibration_routines import run_backlash_calibration

        return run_backlash_calibration(
            steps,
            settle_direction,
            reversed_direction,
            settle_pulse_sec,
            reversal_test_pulse_sec,
            max_test_pulses,
            motion_detection_threshold_px,
        )

    def run_exposure_test(
        self,
        exposure_seconds: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0),
        frames_per_exposure: int = 8,
        gain: float | None = None,
    ) -> GuideExposureLadder:
        """Find the shortest guide exposure this guide camera needs.

        Takes a short series of guide frames at each exposure length while
        the mount tracks without guiding, and measures how bright the
        guide star is, whether it saturates, and how much its measured
        position jumps from frame to frame. The answer is the shortest
        length that works, or a statement that none did and what to check.
        This commands the guide camera only. Point the guide scope at a
        bright star first.

        Parameters
        ----------
        exposure_seconds : `tuple` [`float`, ...], optional
            The exposure lengths to try.
        frames_per_exposure : `int`, optional
            Frames at each length. At least 5 are needed.
        gain : `float`, optional
            Guide camera gain for the test. `None` leaves it alone.

        Returns
        -------
        test : `GuideExposureLadder`
            One result per length and the recommended exposure.

        Raises
        ------
        ExternalServiceError
            If the telescope computer cannot be reached.
        ConfigurationError
            If no guide camera and guide optics are configured.
        """
        from wayfindinglib.analytics.guide_exposure_ladder import analyze_guide_exposure_ladder
        from wayfindinglib.tasks.control_tasks import (
            guide_exposure_ladder_tasks,
            hardware_operations,
            night_analysis,
            performance_envelope_tasks,
            remote_transfer_tasks,
        )

        context = self._context
        if not remote_transfer_tasks.check_remote_connection(context):
            raise ExternalServiceError(
                "The telescope host is not reachable, so the guide camera cannot be used."
            )
        guide_camera = context.active_guide_camera()
        plate_scale = context.guider_plate_scale_arcsec_per_px()
        if guide_camera is None or plate_scale is None:
            raise ConfigurationError("No guide camera and guide optics are configured.")
        contexts, runs = night_analysis.recorded_sessions(context)
        envelope = night_analysis.performance_envelope(context, None, None, contexts, runs, {})
        ceiling = performance_envelope_tasks.sensor_limits_for_camera(
            guide_camera.name, context.config
        ).clip_ceiling_adu
        frames = guide_exposure_ladder_tasks.capture_guide_ladder(
            lambda seconds, camera_gain: hardware_operations.guide_expose(context, seconds, camera_gain),
            lambda: hardware_operations.guide_image(context),
            exposure_seconds,
            frames_per_exposure,
            gain,
        )
        return analyze_guide_exposure_ladder(
            frames,
            plate_scale,
            ceiling,
            envelope.value("guiding_rms_limit") if envelope else None,
            envelope.blur_tolerance_fraction if envelope else 0.10,
        )

    def refit_spectrum(
        self,
        session_id: str | None = None,
        limit: int = 2000,
        file_path: str | None = None,
        download: bool = False,
        destination_dir: str | None = None,
        target_name: str | None = None,
    ) -> GuidingSpectrumAnalysis | None:
        """Refit and save the mount's periodic error model.

        The arguments choose where the guiding samples come from:

        - `file_path`: one PHD2-format guide log. Its samples are stored,
          then its night is refit. Uses `target_name`.
        - ``download=True``: every guide log on the observatory computer,
          copied into `destination_dir`. Their samples are stored, then the
          latest night is refit. Uses `destination_dir` and `target_name`.
        - Otherwise: the samples already recorded. Uses `session_id` and
          `limit`.

        Parameters
        ----------
        session_id : `str`, optional
            Use only this observing night's recorded samples.
        limit : `int`, optional
            Most recorded samples to read.
        file_path : `str`, optional
            One local guide log to read first.
        download : `bool`, optional
            Download every guide log from the observatory computer first.
        destination_dir : `str`, optional
            Local folder for downloaded logs. ``ekos_logs`` in the
            wayfinding library's data folder when omitted.
        target_name : `str`, optional
            Target name to store with new samples.

        Returns
        -------
        analysis : `GuidingSpectrumAnalysis` or `None`
            The refit and saved model, or `None` if a log held no samples
            (or the remote driver cannot fetch guide logs).

        Raises
        ------
        InvalidArgumentError
            If an argument is given that the chosen source does not use.
        """
        from astrometricslib import InvalidArgumentError
        from wayfindinglib.tasks.control_tasks import guiding_log_ingestion

        context = self._context
        reading_logs = file_path is not None or download
        unused = [
            name
            for name, given in (
                ("session_id", reading_logs and session_id is not None),
                ("limit", reading_logs and limit != 2000),
                ("download", file_path is not None and download),
                ("destination_dir", not download and destination_dir is not None),
                ("target_name", not reading_logs and target_name is not None),
            )
            if given
        ]
        if unused:
            raise InvalidArgumentError(f"This source of samples does not use {', '.join(unused)}.")
        if file_path is not None:
            return guiding_log_ingestion.ingest_guide_log_file(
                context, context.logger_interface, file_path, target_name
            )
        if download:
            return guiding_log_ingestion.fetch_and_ingest_new_guide_logs(
                context,
                context.logger_interface,
                destination_dir or context.ekos_log_directory(),
                target_name,
            )
        return guiding_log_ingestion.refit_and_persist_guiding_spectrum(
            context, context.logger_interface, session_id, limit
        )

    def save_calibration(self, calibration: GuiderCalibration) -> None:
        """Save a measured guider calibration.

        Parameters
        ----------
        calibration : `GuiderCalibration`
            The calibration, keyed by its own id.
        """
        self._context.butler.put(calibration, "guider_calibration", {"id": calibration.id})

    def save_spectrum_analysis(self, analysis: GuidingSpectrumAnalysis) -> None:
        """Save `analysis` as the active mount's periodic error model.

        The model belongs to the mount, so it is keyed by the active
        telescope and replaced on every save.

        Parameters
        ----------
        analysis : `GuidingSpectrumAnalysis`
            The fitted model.
        """
        self._context.save_guiding_spectrum_analysis(analysis)

    def save_run(self, run: GuidingRunSummary) -> None:
        """Save one guiding run, replacing an earlier read of the same run.

        Parameters
        ----------
        run : `GuidingRunSummary`
            The run, keyed by its guide log's name and its place in it.
        """
        self._context.save_guiding_run(run)
