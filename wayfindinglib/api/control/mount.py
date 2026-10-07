"""Purpose: `control.mount`, pointing and tracking.

Description: Reads the mount's position and commands it: slew (and
center by plate solving), sync, park, tracking, manual moves and the
slew rate. It also computes the
pointing correction for one plate-solve iteration and fits tonight's
polar alignment error. Each command needs `MOUNT_CONTROL` (or, for a
sync, `PLATE_SOLVE_ALIGNMENT`) to be `AUTHORITATIVE` in the delegation
policy. The work is done in `tasks.control_tasks`.
"""

from typing import Any

from astrometricslib import Target
from wayfindinglib.api.control.context import ControlChild
from wayfindinglib.models.session.correction_result import PointingCorrection
from wayfindinglib.models.session.telemetry import MountPointingModel
from wayfindinglib.models.sky_position import SkyPosition

__all__ = ["MountControl"]


class MountControl(ControlChild):
    """Point and track the telescope mount."""

    def status(self, include: list[str] | None = None) -> dict[str, Any]:
        """Read the mount's position and tracking, and the imaging devices.

        Parameters
        ----------
        include : `list` [`str`], optional
            Device reads to make: ``mount`` (position, tracking, pier
            side, park state, tracking rate, connection, ambient
            temperature and humidity), ``filter``
            (current filter), ``focuser`` (position) and ``camera``
            (sensor temperature). All of them when omitted.

        Returns
        -------
        status : `dict` [`str`, `Any`]
            ``ra``, ``dec``, ``altitude``, ``azimuth``, ``trackingStatus``,
            ``connectionStatus``, ``targetName``, ``pierSide`` (``EAST`` or
            ``WEST``), ``parked``, ``trackMode`` (such as ``SIDEREAL``),
            ``temperature``,
            ``humidity``, ``cameraStatus``, ``filter``, ``focuserPosition``
            and ``cameraTemperature``, for the sections read.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.mount_status(self._context, include)

    def slew(
        self,
        destination: str | Target | SkyPosition,
        center: bool = False,
        tolerance_arcsec: float | None = None,
        max_iterations: int | None = None,
    ) -> bool:
        """Slew the mount to a library target or a sky position.

        With ``center=True`` the mount is then centered by plate solving:
        take a short frame, solve it, and if the pointing error is above
        `tolerance_arcsec`, sync the mount to the solved position and slew
        again. Each round is recorded as an alignment attempt (see
        `control.history.query(kind="alignment")`). `abort_motion` stops
        the rounds.

        Parameters
        ----------
        destination : `str`, `Target` or `SkyPosition`
            A library target or its id (its plate-solved coordinates are
            used), or a position ``{"ra_deg": ..., "dec_deg": ...}``.
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

        Returns
        -------
        success : `bool`
            Whether the slew command was accepted, or with
            ``center=True``, whether the mount ended within the tolerance.

        Raises
        ------
        InvalidArgumentError
            If `tolerance_arcsec` or `max_iterations` is given without
            ``center=True``.
        """
        from astrometricslib import InvalidArgumentError
        from wayfindinglib.tasks.control_tasks import centering, hardware_operations

        position = hardware_operations.resolve_destination(self._context, destination)
        if not center:
            if tolerance_arcsec is not None or max_iterations is not None:
                raise InvalidArgumentError(
                    "tolerance_arcsec and max_iterations are used only with center=True."
                )
            return hardware_operations.slew(self._context, position)
        target_name = destination if isinstance(destination, str) else getattr(destination, "id", None)
        return centering.center_on(
            self._context,
            position,
            tolerance_arcsec=tolerance_arcsec,
            max_iterations=max_iterations,
            target_name=target_name,
        )

    def sync(self, position: SkyPosition | dict[str, float]) -> bool:
        """Tell the mount it is pointing at a plate-solved position.

        Parameters
        ----------
        position : `SkyPosition` or `dict`
            The solved position, or ``{"ra_deg": ..., "dec_deg": ...}``.

        Returns
        -------
        success : `bool`
            Whether the sync command was accepted. A dictionary that is not
            a valid position is refused with `InvalidArgumentError`.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.sync_mount(self._context, hardware_operations.sky_position_from(position))

    def park(self) -> bool:
        """Park the mount.

        Returns
        -------
        success : `bool`
            Whether the park command was accepted.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.park(self._context)

    def unpark(self) -> bool:
        """Unpark the mount.

        Returns
        -------
        success : `bool`
            Whether the unpark command was accepted.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.unpark(self._context)

    def set_tracking(self, enabled: bool) -> bool:
        """Turn mount tracking on or off.

        Parameters
        ----------
        enabled : `bool`
            `True` to track the sky.

        Returns
        -------
        success : `bool`
            Whether the tracking command was accepted.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.set_tracking(self._context, enabled)

    def manual_move(self, direction: str, start: bool = True) -> bool:
        """Start or stop moving the mount in one direction.

        Parameters
        ----------
        direction : `str`
            ``"north"``, ``"south"``, ``"east"`` or ``"west"``.
        start : `bool`, optional
            `True` to start moving, `False` to stop.

        Returns
        -------
        success : `bool`
            Whether the move command was accepted.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.manual_move(self._context, direction, start)

    def abort_motion(self) -> bool:
        """Stop all mount motion now.

        Returns
        -------
        success : `bool`
            Whether the abort command was accepted.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.abort_motion(self._context)

    def set_slew_rate(self, rate_index: int) -> bool:
        """Choose the mount's slew rate.

        Parameters
        ----------
        rate_index : `int`
            Index into the mount's list of slew rates.

        Returns
        -------
        success : `bool`
            Whether the rate command was accepted.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        return hardware_operations.set_slew_rate(self._context, rate_index)

    def compute_pointing_correction(
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

    def run_polar_alignment_assist(
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
