"""Purpose: `control.mount`, pointing and tracking.

Description: Reads the mount's position and commands it: slew (and
center by plate solving), stop, park, tracking, manual moves and the
slew rate. It also computes the pointing correction for one plate-solve
iteration and fits tonight's polar alignment error. Each command needs
`MOUNT_CONTROL` (or, for a sync, `PLATE_SOLVE_ALIGNMENT`) to be
`AUTHORITATIVE` in the delegation policy. A command returns `None` and
raises when it fails. The work is done in `tasks.control_tasks`.
"""

from typing import Any

from astrometricslib import SkyPosition, StellarObject, Target
from astrometricslib.arguments import TimeInput
from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.models.control_status import MountControlStatus
from wayfindinglib.models.session.centering_exposure import CenteringExposure
from wayfindinglib.models.session.correction_result import PointingCorrection
from wayfindinglib.models.session.telemetry import MountPointingModel

__all__ = ["MountControl"]


class MountControl(ControlChild):
    """Point and track the telescope mount."""

    def get_status(self) -> MountControlStatus:
        """Read the mount's position, tracking and connection.

        Only the mount: the filter, the focuser and the camera are read
        with `control.imaging.get_status`.

        Owner:
            wayfindinglib.tasks.control_tasks.hardware_operations.mount_status

        Returns
        -------
        status : `MountControlStatus`
            ``ra_deg`` and ``dec_deg`` are ICRS (J2000), like catalog stars
            and plate solutions: the mount's own current-epoch (JNow)
            reading is converted. Also ``altitude_deg``, ``azimuth_deg``,
            ``tracking_status``, ``connection_status``, ``target_name``,
            ``pier_side`` (``EAST`` or ``WEST``), ``parked``,
            ``track_mode`` (such as ``SIDEREAL``) and the ambient
            ``temperature`` and ``humidity``. A value the mount does not
            know yet is `None`.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.mount_status(self._context)

    def slew(
        self,
        destination: Target | StellarObject | SkyPosition,
        center: bool = False,
        tolerance_arcsec: float | None = None,
        max_iterations: int | None = None,
    ) -> None:
        """Slew the mount to a target, a star or a sky position.

        With ``center=True`` the mount is then centered by plate solving:
        take a short frame (its exposure comes from
        `get_centering_exposure`), solve it, and if the pointing error is
        above `tolerance_arcsec`, sync the mount to the solved position and
        slew again. Each round is recorded as an alignment attempt. `stop`
        ends the rounds.

        Owner:
            wayfindinglib.tasks.control_tasks.centering.slew_to

        Parameters
        ----------
        destination : `Target`, `StellarObject` or `SkyPosition`
            Where to point. All positions are ICRS (J2000); the mount is
            sent the current-epoch (JNow) equivalent.
        center : `bool`, optional
            Refine the pointing by plate solving after the slew. Needs
            `CAPTURE_ORCHESTRATION` and `PLATE_SOLVE_ALIGNMENT` as well as
            `MOUNT_CONTROL`.
        tolerance_arcsec : `float`, optional
            Largest pointing error that counts as centered. Used only with
            ``center=True``. 30 arcseconds by default.
        max_iterations : `int`, optional
            Most solve-and-correct rounds. Used only with ``center=True``.
            3 by default.

        Notes
        -----
        Raises `InvalidArgumentError` if `tolerance_arcsec` or
        `max_iterations` is given without ``center=True``, or the
        destination has no coordinates; `PermissionDeniedError` in monitor
        mode; and `HardwareError` if the mount refuses the slew or centering
        does not reach the tolerance.
        """
        from wayfindinglib.tasks.control_tasks.centering import slew_to

        slew_to(
            self._context,
            destination,
            center=center,
            tolerance_arcsec=tolerance_arcsec,
            max_iterations=max_iterations,
        )

    def get_centering_exposure(
        self,
        destination: Target | StellarObject | SkyPosition,
        time: TimeInput | None = None,
        filter_name: str | None = None,
    ) -> CenteringExposure:
        """Work out how long a centering frame should be, without taking one.

        `slew` with ``center=True`` does this itself. It reads the library's
        own measurements of the active telescope and camera (the Gaia G zero
        point and the star width of recent frames), the camera profile's
        gain and read noise, the predicted sky background for the pointing,
        and the downloaded Gaia star catalog. It finds the shortest exposure
        (from 1 to 30 s) at which enough catalog stars are bright enough to
        be detected for a plate solve. When an input is missing it gives
        the fixed ``alignment_exposure_s`` setting and says which one.
        It moves nothing and stores nothing.

        Owner:
            wayfindinglib.tasks.control_tasks.centering_exposure.get_centering_exposure

        Parameters
        ----------
        destination : `Target`, `StellarObject` or `SkyPosition`
            Where the telescope will point, as for `slew`.
        time : `TimeInput`, optional
            When the frame will be taken. Now when omitted.
        filter_name : `str`, optional
            Use this filter's measurements. The filter with the most
            frames when omitted.

        Returns
        -------
        choice : `CenteringExposure`
            ``exposure_s``, ``source`` (``"computed"`` or
            ``"fallback"``), ``reason``, the limiting magnitude and expected
            star count, ``catalog_is_lower_bound``, every number used, and
            ``trials`` (what each candidate exposure would show).
        """
        from wayfindinglib.tasks.control_tasks.centering_exposure import get_centering_exposure

        return get_centering_exposure(self._context, destination, time=time, filter_name=filter_name)

    def stop(self) -> None:
        """Stop all mount motion now, and any centering run.

        Monitor mode refuses a stop too, whether or not anything moves.

        Owner:
            wayfindinglib.tasks.control_tasks.hardware_operations.stop_mount

        Notes
        -----
        Raises `PermissionDeniedError` unless `MOUNT_CONTROL` is
        `AUTHORITATIVE`, and `HardwareError` if the mount refuses the abort.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        hardware_operations.stop_mount(self._context)

    def move(self, direction: str, start: bool = True) -> None:
        """Start or stop moving the mount in one direction.

        Owner:
            wayfindinglib.tasks.control_tasks.hardware_operations.manual_move

        Parameters
        ----------
        direction : `str`
            ``"north"``, ``"south"``, ``"east"`` or ``"west"``.
        start : `bool`, optional
            `True` to start moving, `False` to stop.

        Notes
        -----
        Raises `HardwareError` if the mount refuses the move.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        hardware_operations.manual_move(self._context, direction, start)

    def park(self) -> None:
        """Park the mount.

        Owner:
            wayfindinglib.tasks.control_tasks.hardware_operations.park

        Notes
        -----
        Raises `HardwareError` if the mount refuses the park command.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        hardware_operations.park(self._context)

    def unpark(self) -> None:
        """Unpark the mount.

        Owner:
            wayfindinglib.tasks.control_tasks.hardware_operations.unpark

        Notes
        -----
        Raises `HardwareError` if the mount refuses the unpark command.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        hardware_operations.unpark(self._context)

    def set_tracking(self, on: bool) -> None:
        """Turn mount tracking on or off.

        Owner:
            wayfindinglib.tasks.control_tasks.hardware_operations.set_tracking

        Parameters
        ----------
        on : `bool`
            `True` to track the sky.

        Notes
        -----
        Raises `HardwareError` if the mount refuses the command.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        hardware_operations.set_tracking(self._context, on)

    def set_slew_rate(self, rate: int) -> None:
        """Choose the mount's slew rate.

        Owner:
            wayfindinglib.tasks.control_tasks.hardware_operations.set_slew_rate

        Parameters
        ----------
        rate : `int`
            Index into the mount's list of slew rates.

        Notes
        -----
        Raises `HardwareError` if the mount refuses the rate.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        hardware_operations.set_slew_rate(self._context, rate)

    def _sync(self, position: SkyPosition | dict[str, float]) -> None:
        """Tell the mount it is pointing at a plate-solved position.

        Parameters
        ----------
        position : `SkyPosition` or `dict`
            The solved position, or ``{"ra_deg": ..., "dec_deg": ...}``,
            in ICRS (J2000). The mount is sent the current-epoch (JNow)
            equivalent.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        hardware_operations.sync_mount(self._context, hardware_operations.sky_position_from(position))

    def _compute_pointing_correction(
        self,
        comparison_input_id: str,
        commanded_ra_deg: float,
        commanded_dec_deg: float,
        solved_ra_deg: float,
        solved_dec_deg: float,
        iteration: int,
        pointing_model: MountPointingModel | None = None,
    ) -> PointingCorrection:
        """Compute one iteration's pointing error and the move that closes it.

        Computes only; nothing is sent to the mount.

        Parameters
        ----------
        comparison_input_id : `str`
            Id of the plate solve this correction is for.
        commanded_ra_deg, commanded_dec_deg : `float`
            Where the mount was told to point, in degrees.
        solved_ra_deg, solved_dec_deg : `float`
            Where the plate solve says it points, in degrees.
        iteration : `int`
            Which centering iteration this is, from 1.
        pointing_model : `MountPointingModel`, optional
            Tonight's fitted model, fed forward into the correction.
            Not looked up automatically: it belongs to one night.

        Returns
        -------
        correction : `PointingCorrection`
            The pointing error and the closing move.
        """
        from wayfindinglib.tasks.control_tasks.pointing_correction import compute_pointing_correction

        return compute_pointing_correction(
            comparison_input_id,
            commanded_ra_deg,
            commanded_dec_deg,
            solved_ra_deg,
            solved_dec_deg,
            iteration,
            self._context.correction_config,
            pointing_model=pointing_model,
            latitude_deg=self._context.observer_latitude_deg(),
        )

    def _run_polar_alignment_assist(
        self, attempts: list[dict[str, Any]], latitude_deg: float | None = None
    ) -> MountPointingModel:
        """Fit tonight's polar alignment error from this session's solves.

        Never saved: polar alignment changes every time it is redone, so
        this is a live adjust-and-check loop, not a standing model.

        Parameters
        ----------
        attempts : `list` [`dict` [`str`, `Any`]]
            This session's plate-solve records so far.
        latitude_deg : `float`, optional
            Observer latitude in degrees. Taken from the observer location
            when omitted.

        Returns
        -------
        model : `MountPointingModel`
            The fitted model.
        """
        from wayfindinglib.tasks.control_tasks.calibration_routines import (
            run_polar_alignment_assist as polar_alignment_assist,
        )

        if latitude_deg is None:
            latitude_deg = self._context.observer_latitude_deg()
        return polar_alignment_assist(attempts, latitude_deg=latitude_deg)
