"""Purpose: `control.guiding`, the guide camera, pulses and guider models.

Description: Starts and stops this app's own guide loop, takes guide
exposures, computes the pulse that corrects a measured drift and follows
the guiding going on now (its root-mean-square (RMS) error, through the
guiding driver the ``guiding_protocol`` setting picks). It runs the
guider calibrations (the pulse-to-pixel calibration, the declination
backlash probe and the guide exposure test). `get_status` reads the saved
calibration, the saved periodic error model, the guide camera's plate
scale and the live guiding; guider calibrations are records, read with
`query_guider_calibrations` and stored with `create_guider_calibration`.
Pulses and guide exposures need `AUTOGUIDING` to be `AUTHORITATIVE`. The
mount's periodic error model is fitted by
``review.guiding.measure_spectrum``.
"""

from typing import Any

from astrometricslib.arguments import Page
from astrometricslib.exceptions import ConfigurationError, ExternalServiceError
from astrometricslib.parameters import resolve_parameter
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


class GuidingControl(ControlChild):
    """Operate the guide camera and the guider's saved models."""

    def get_status(self, include: list[str] | None = None) -> GuidingStatus:
        """Read the saved guider models, and the guiding going on now.

        Owner:
            wayfindinglib.tasks.control_tasks.control_status.read_guiding_status

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
        from wayfindinglib.tasks.control_tasks.control_status import read_guiding_status

        return read_guiding_status(self._context, include)

    def start(self, exposure_s: float | None = None, gain: float | None = None) -> None:
        """Start this app's guide loop, which runs until `stop`.

        The loop runs on a thread of its own. Each cycle takes a guide
        exposure, measures the guide error and sends the correction pulses,
        through the guiding driver the active telescope's
        ``guiding_protocol`` names. Only the ``simulator`` driver runs a
        loop today: PHD2 runs its own, and the ``internal`` driver cannot
        measure the guide star yet. ``get_status(include=["live"])`` shows
        the progress. The loop ends by itself if the mount stops tracking.

        Owner:
            wayfindinglib.tasks.control_tasks.live_guiding.start_loop

        Parameters
        ----------
        exposure_s : `float`, optional
            Guide exposure length in seconds. The last one used when
            omitted (1 s at first).
        gain : `float`, optional
            Guide camera gain. The last one used when omitted.

        Notes
        -----
        Raises `PermissionDeniedError` unless `AUTOGUIDING` is
        `AUTHORITATIVE`, and `ConflictError` if the loop already runs or
        the mount does not track.
        """
        from wayfindinglib.tasks.control_tasks import live_guiding

        live_guiding.start_loop(self._context, exposure_s, gain)

    def stop(self) -> None:
        """Stop this app's guide loop and wait for it to end.

        Stopping when no loop runs does nothing. Monitor mode refuses this
        too.

        Owner:
            wayfindinglib.tasks.control_tasks.live_guiding.stop_loop

        Notes
        -----
        Raises `PermissionDeniedError` unless `AUTOGUIDING` is
        `AUTHORITATIVE`.
        """
        from wayfindinglib.tasks.control_tasks import live_guiding

        live_guiding.stop_loop(self._context)

    def capture(self, exposure_s: float, gain: float | None = None) -> None:
        """Take one exposure with the guide camera.

        Owner:
            wayfindinglib.tasks.control_tasks.hardware_operations.capture_guide_frame

        Parameters
        ----------
        exposure_s : `float`
            Exposure length in seconds.
        gain : `float`, optional
            Camera gain to set first. `None` leaves it alone.

        Notes
        -----
        Raises `PermissionDeniedError` unless `AUTOGUIDING` is
        `AUTHORITATIVE`, and `HardwareError` if the camera does not start.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        hardware_operations.capture_guide_frame(self._context, exposure_s, gain=gain)

    def query_guider_calibrations(self, limit: int | None = None, offset: int = 0) -> Page[GuiderCalibration]:
        """List every saved guider calibration, oldest first.

        `get_status` reads only the calibration of the active equipment.
        This reads them all, for every telescope and camera pairing.

        Owner:
            wayfindinglib.tasks.control_tasks.control_records.query_guider_calibrations

        Parameters
        ----------
        limit : `int`, optional
            The most records to return. Every one when omitted.
        offset : `int`, optional
            How many records to skip.

        Returns
        -------
        calibrations : `Page` [`GuiderCalibration`]
            The saved records in order of ``calibrated_at``.
        """
        from wayfindinglib.tasks.control_tasks.control_records import query_guider_calibrations

        return query_guider_calibrations(self._context, limit=limit, offset=offset)

    def create_guider_calibration(
        self, calibration: GuiderCalibration, force: bool = False
    ) -> GuiderCalibration:
        """Save a guider calibration that was measured elsewhere.

        Use this when the numbers come from a tool outside this library,
        such as PHD2's own calibration, or from a copy of an earlier record.
        The delegation rules treat the record like one measured here.
        Nothing is sent to a device.

        Owner:
            wayfindinglib.tasks.control_tasks.control_records.create_guider_calibration

        Parameters
        ----------
        calibration : `GuiderCalibration`
            The record. Its ``source_note`` says where the numbers came
            from; its id has at most 100 characters.
        force : `bool`, optional
            Replace a saved record that has the same id. `False` refuses
            instead.

        Returns
        -------
        calibration : `GuiderCalibration`
            The saved record.

        Notes
        -----
        Raises `InvalidArgumentError` if a number is out of range, the id
        or source note is empty or too long, or the id is already saved
        and `force` is false.
        """
        from wayfindinglib.tasks.control_tasks.control_records import create_guider_calibration

        return create_guider_calibration(self._context, calibration, force=force)

    def _pulse(self, direction: str, duration_ms: float) -> bool:
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

    def _get_image(self) -> Any:
        """Return the guide camera's last image.

        Returns
        -------
        image : `Any`
            The last image data the guide camera sent.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.guide_image(self._context)

    def _drain_external_pulses(self) -> list[dict[str, Any]]:
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

    def _compute_correction(
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

    def _run_calibration(
        self,
        steps: GuiderCalibrationSteps,
        calibration_id: str,
        camera_id: str,
        telescope_id: str,
        arcsec_per_pixel: float,
        ra_pulse_duration_sec: float | None = None,
        dec_pulse_duration_sec: float | None = None,
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
            Guide camera plate scale (the ``plate_scale`` section of
            `get_status`).
        ra_pulse_duration_sec, dec_pulse_duration_sec : `float`, optional
            Length of the test pulse on each axis. `None` works it out from
            the plate scale and the mount's guide rate.

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
        self._save_calibration(calibration)
        return calibration

    def _run_backlash_calibration(
        self,
        steps: BacklashCalibrationSteps,
        settle_direction: str = "north",
        reversed_direction: str = "south",
        settle_pulse_sec: float | None = None,
        reversal_test_pulse_sec: float | None = None,
        max_test_pulses: int | None = None,
        motion_detection_threshold_px: float | None = None,
    ) -> float:
        """Measure declination backlash by reversing direction gently.

        Not saved here: fold the result into the mount's periodic error
        model with `_save_spectrum_analysis`.

        Parameters
        ----------
        steps : `BacklashCalibrationSteps`
            The pulse and star-measurement operations to use.
        settle_direction, reversed_direction : `str`, optional
            The direction to settle in, then the opposite direction.
        settle_pulse_sec : `float`, optional
            Length of the settling pulse. `None` uses the declared value.
        reversal_test_pulse_sec : `float`, optional
            Length of each small probing pulse. `None` uses the declared
            value.
        max_test_pulses : `int`, optional
            Most probing pulses before giving up. `None` uses the declared
            value.
        motion_detection_threshold_px : `float`, optional
            Star movement that counts as the mount responding. `None` uses
            the declared value.

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

    def _run_exposure_test(
        self,
        exposure_s: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0),
        frames_per_exposure: int | None = None,
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
        exposure_s : `tuple` [`float`, ...], optional
            The exposure lengths to try.
        frames_per_exposure : `int`, optional
            Frames at each length. At least 5 are needed. `None` uses
            `FRAMES_PER_EXPOSURE_TEST` (8).
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
        from wayfindinglib.analytics import envelope_assembly
        from wayfindinglib.analytics.guide_exposure_ladder import (
            FRAMES_PER_EXPOSURE_TEST,
            analyze_guide_exposure_ladder,
        )
        from wayfindinglib.tasks.control_tasks import (
            guide_exposure_ladder_tasks,
            hardware_operations,
            night_analysis,
            remote_transfer_tasks,
        )

        if frames_per_exposure is None:
            frames_per_exposure = int(resolve_parameter(FRAMES_PER_EXPOSURE_TEST).value)
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
        ceiling = envelope_assembly.sensor_limits_for_camera(
            context.astrometrics, guide_camera.name
        ).clip_ceiling_adu
        frames = guide_exposure_ladder_tasks.capture_guide_ladder(
            lambda seconds, camera_gain: hardware_operations.guide_expose(context, seconds, camera_gain),
            lambda: hardware_operations.guide_image(context),
            exposure_s,
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

    def _save_calibration(self, calibration: GuiderCalibration) -> None:
        """Save a measured guider calibration.

        Parameters
        ----------
        calibration : `GuiderCalibration`
            The calibration, keyed by its own id.
        """
        self._context.butler.put(calibration, "guider_calibration", {"id": calibration.id})

    def _save_spectrum_analysis(self, analysis: GuidingSpectrumAnalysis) -> None:
        """Save `analysis` as the active mount's periodic error model.

        The model belongs to the mount, so it is keyed by the active
        telescope and replaced on every save.

        Parameters
        ----------
        analysis : `GuidingSpectrumAnalysis`
            The fitted model.
        """
        self._context.save_guiding_spectrum_analysis(analysis)

    def _save_run(self, run: GuidingRunSummary) -> None:
        """Save one guiding run, replacing an earlier read of the same run.

        Parameters
        ----------
        run : `GuidingRunSummary`
            The run, keyed by its guide log's name and its place in it.
        """
        self._context.save_guiding_run(run)
