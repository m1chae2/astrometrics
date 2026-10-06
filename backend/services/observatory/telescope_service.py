"""High-level telescope control operations.

TelescopeService manages high-level astronomical telescope control
operations, delegating all core hardware interaction and observation
state logic directly to the wayfindinglib domain high-level interface.
"""

import logging
from typing import Any

from astrometricslib import InvalidArgumentError

logger = logging.getLogger(__name__)


class TelescopeService:
    """Service responsible for telescope control and telemetry.

    Acts as the gateway to hardware devices via the Wayfinder
    high-level interface. REQ: BKD-1: Hardware Abstraction & Control
    """

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        driver: Any = None,
        guiding_service: Any = None,
        target_service: Any = None,
        wayfinder: Any = None,
        astrometrics_service: Any = None,
        alignment_service: Any = None,
    ):
        """Initialize the service and Wayfinder high-level interface.

        Parameters
        ----------
        driver : `Any`, optional
            Hardware driver injection for tests.
        guiding_service : `Any`, optional
            Service for managing guide camera loops.
        target_service : `Any`, optional
            Service for looking up targets in the library.
        wayfinder : `Any`, optional
            The Wayfinder high-level interface facade.
        astrometrics_service : `Any`, optional
            Service tracking cross-component state updates.
        alignment_service : `Any`, optional
            Service running plate-solving loops.
        """
        if wayfinder is None:
            from wayfindinglib import Wayfinder

            self.wayfinder = Wayfinder()
        else:
            self.wayfinder = wayfinder

        if driver is not None:
            self.wayfinder.control.driver = driver

        self._guiding_service = guiding_service
        self._target_service = target_service
        self._astrometrics_service = astrometrics_service
        self._alignment_service = alignment_service

    @property
    def indi_interface(self) -> Any:
        """The INDI hardware interface from the observatory.

        Returns
        -------
        driver : `Any`
            The underlying INDI driver instance orchestrating hardware calls.
        """
        return self.wayfinder.control.driver

    def get_status(self) -> dict[str, Any]:
        """Retrieve current telescope status.

        Includes RA/DEC, connection state, environmental sensors (if
        available), and Guiding History.

        Returns
        -------
        result : `dict`
            Current telescope status fields, including a fallback
            payload if the hardware query fails.
        """
        # REQ: BKD-1.5: The backend SHALL broadcast hardware status
        # updates to connected clients via WebSocket.
        import logging

        logger = logging.getLogger(__name__)
        try:
            data = self.wayfinder.control.mount.status()
        except Exception as e:
            logger.warning("Failed to query telescope status (telescope may be offline): %s", e)
            data = {
                "ra": "00 00 00",
                "dec": "+00 00 00",
                "altitude": "0.0",
                "azimuth": "0.0",
                "trackingStatus": "Idle",
                "connectionStatus": "Disconnected",
                "temperature": "0.0",
                "humidity": "0.0",
                "filter": "None",
                "focuserPosition": 0,
            }

        # Poll external guiding telemetry to drain real-time pulses from
        # KStars/Ekos/PHD2
        if self._guiding_service:
            try:
                self._guiding_service.poll_external_telemetry()
            except Exception as e:
                logger.debug("Failed to poll external guiding telemetry: %s", e)
            guiding_status = self._guiding_service.get_status()
            data["guidingHistory"] = guiding_status.get("history", [])

        # Inject real alignment attempt history if service is available
        if self._alignment_service:
            try:
                driver = getattr(self.wayfinder.control, "driver", None) or getattr(
                    self.wayfinder.control, "_driver", None
                )
                self._alignment_service.poll_external_syncs(driver)
            except Exception as e:
                logger.debug("Failed to poll external syncs: %s", e)
            data["alignmentAttempts"] = self._alignment_service.get_attempts()
            data["alignmentActive"] = self._alignment_service.is_active()
            if hasattr(self._alignment_service, "get_polar_alignment"):
                data["polarAlignment"] = self._alignment_service.get_polar_alignment()

        # Resolve active celestial target: prioritize target name reported
        # by driver (e.g. from camera FITS_HEADER / OBJECT or mount metadata),
        # then fall back to coordinate matching against catalog targets.
        target_name = data.get("targetName") or data.get("target_name")
        if not target_name:
            target_name = self._infer_target_at_coordinates(data.get("ra"), data.get("dec"))
        if target_name:
            data["targetName"] = target_name

        if self._astrometrics_service:
            self._astrometrics_service.update_telescope_state(data)

        return data

    def _infer_target_at_coordinates(self, ra_str: str | None, dec_str: str | None) -> str | None:
        """Infer target identity by matching coordinates against catalog.

        Parameters
        ----------
        ra_str : `str` | `None`
            Current Right Ascension coordinate string.
        dec_str : `str` | `None`
            Current Declination coordinate string.

        Returns
        -------
        `str` | `None`
            The matched target name, or None if coordinates are missing or no
            target is within 1 degree separation.
        """
        if not ra_str or not dec_str or ra_str in ("-", "Unknown", "00 00 00") or not self._target_service:
            return None

        try:
            from astrometricslib import parse_coordinate_string

            nearest = self._target_service.astrometrics.targets.query(
                ra=parse_coordinate_string(ra_str, is_ra=True),
                dec=parse_coordinate_string(dec_str, is_ra=False),
                radius_deg=1.0,  # 1 degree tolerance for sensor FOV match
                include_empty=True,
                sort="separation",
                limit=1,
            )
        except Exception as exc:
            logger.debug("Target inference failed: %s", exc)
            return None
        rows = nearest.get("targets") or []
        return (rows[0].get("common_name") or rows[0]["id"]) if rows else None

    def get_telescope_status(self) -> dict[str, Any]:
        """Alias of get_status for reflected tool calls.

        Returns
        -------
        result : `dict`
            Current telescope status fields.
        """
        return self.get_status()

    def connect(self) -> bool:
        """Connect to the telescope hardware via the high-level interface.

        Returns
        -------
        result : `bool`
            `True` if the connection succeeded.
        """
        return self.wayfinder.control.equipment.connect()

    def slew_to_coordinates(self, ra: float, dec: float) -> bool:
        """Command the telescope to slew to the specified coordinates.

        Parameters
        ----------
        ra : `float`
            Right ascension in hours, as the UI sends it.
        dec : `float`
            Declination in degrees.

        Returns
        -------
        result : `bool`
            `True` if the slew command succeeded.

        Raises
        ------
        InvalidArgumentError
            If the hardware driver rejects an argument of the slew command.
        """
        # REQ: BKD-1.2: The backend SHALL provide a generic interface
        # for Telescope control (Slew, Sync, Park, Track).
        try:
            from wayfindinglib import SkyPosition

            position = SkyPosition(ra_deg=(float(ra) * 15.0) % 360.0, dec_deg=float(dec))
            return self.wayfinder.control.mount.slew(position)
        except ValueError as e:
            raise InvalidArgumentError(str(e)) from e

    def slew_to_target_by_name(self, target_name: str) -> bool:
        """Resolve a target name from the library and slew to it.

        REQ: AGENT-2.1

        Returns
        -------
        result : `bool`
            `True` if the slew command succeeded.

        Raises
        ------
        InvalidArgumentError
            If ``target_name`` is empty. A name that is not in the library
            is refused by `control.mount.slew` with `NotFoundError`.
        """
        if not target_name or not target_name.strip():
            raise InvalidArgumentError("target_name must not be empty")

        return self.wayfinder.control.mount.slew(target_name)

    def slew_to_target(self, target_name: str) -> bool:
        """Reflected tool execution alias for slew_to_target_by_name.

        Returns
        -------
        result : `bool`
            `True` if the slew command succeeded.
        """
        return self.slew_to_target_by_name(target_name)

    def park_telescope(self) -> bool:
        """Command the telescope to park via the high-level interface.

        Returns
        -------
        result : `bool`
            `True` if the park command succeeded.

        Raises
        ------
        InvalidArgumentError
            If the hardware driver rejects an argument of the park command.
        """
        try:
            return self.wayfinder.control.mount.park()
        except ValueError as e:
            raise InvalidArgumentError(str(e)) from e

    def unpark_telescope(self) -> bool:
        """Command the telescope to unpark via the high-level interface.

        Returns
        -------
        result : `bool`
            `True` if the unpark command succeeded.

        Raises
        ------
        InvalidArgumentError
            If the hardware driver rejects an argument of the unpark command.
        """
        try:
            return self.wayfinder.control.mount.unpark()
        except ValueError as e:
            raise InvalidArgumentError(str(e)) from e

    def set_tracking(self, enabled: bool) -> bool:
        """Set tracking state via the high-level interface.

        Returns
        -------
        result : `bool`
            `True` if the tracking command succeeded.

        Raises
        ------
        InvalidArgumentError
            If the hardware driver rejects an argument of the tracking command.
        """
        try:
            return self.wayfinder.control.mount.set_tracking(enabled)
        except ValueError as e:
            raise InvalidArgumentError(str(e)) from e

    def manual_move(self, direction: str, start: bool = True) -> bool:
        """Start or stop manual movement via the high-level interface.

        Returns
        -------
        result : `bool`
            `True` if the movement command succeeded.

        Raises
        ------
        InvalidArgumentError
            If the hardware driver rejects an argument of the movement command.
        """
        try:
            return self.wayfinder.control.mount.manual_move(direction, start)
        except ValueError as e:
            raise InvalidArgumentError(str(e)) from e

    def abort_motion(self) -> bool:
        """Abort all telescope mount motion immediately via astrometrics.

        Returns
        -------
        result : `bool`
            `True` if the abort command succeeded.

        Raises
        ------
        InvalidArgumentError
            If the hardware driver rejects an argument of the abort command.
        """
        try:
            return self.wayfinder.control.mount.abort_motion()
        except ValueError as e:
            raise InvalidArgumentError(str(e)) from e

    def set_slew_rate(self, rate_index: int) -> bool:
        """Set slew rate (0-3) via the high-level interface.

        Returns
        -------
        result : `bool`
            `True` if the slew rate command succeeded.

        Raises
        ------
        InvalidArgumentError
            If the hardware driver rejects the slew rate.
        """
        try:
            return self.wayfinder.control.mount.set_slew_rate(rate_index)
        except ValueError as e:
            raise InvalidArgumentError(str(e)) from e

    def focus_move(self, steps: int) -> bool:
        """Move focuser via the high-level interface.

        Returns
        -------
        result : `bool`
            `True` if the focuser move command succeeded.

        Raises
        ------
        InvalidArgumentError
            If the hardware driver rejects the number of steps.
        """
        # REQ: BKD-1.4: The backend SHALL provide a generic interface
        # for Focuser control (Move, Position).
        try:
            return self.wayfinder.control.imaging.focus_move(steps)
        except ValueError as e:
            raise InvalidArgumentError(str(e)) from e

    def get_focuser_position(self) -> int:
        """Get current focuser position via the high-level interface.

        Returns
        -------
        result : `int`
            Current focuser step position.

        Raises
        ------
        InvalidArgumentError
            If the hardware driver rejects the query.
        """
        try:
            return self.wayfinder.control.imaging.status(include=["focuser"]).focuser_position
        except ValueError as e:
            raise InvalidArgumentError(str(e)) from e

    def set_filter(self, filter_name: str) -> bool:
        """Set the active filter on the filterwheel.

        Resolves requested filter names using. fuzzy matching rules in the
        Astrometrics library.

        Returns
        -------
        result : `bool`
            `True` if the filter change command succeeded.

        Raises
        ------
        InvalidArgumentError
            If ``filter_name`` is empty. A name the filter wheel does not
            know is refused by `control.imaging.set_filter` with
            `NotFoundError`.
        """
        if not filter_name or not filter_name.strip():
            raise InvalidArgumentError("filter_name must not be empty")

        return self.wayfinder.control.imaging.set_filter(filter_name)

    def get_observer_location(self) -> dict:
        """Return observer location from INDI GPSD or a fallback default.

        Returns
        -------
        location : `dict`
            Geographic coordinate dictionary containing ``"latitude"``,
            ``"longitude"``, and ``"elevation"``.

        REQ: PLN-2.3
        """
        try:
            geo = self.wayfinder.control.equipment.status(include=["observer_location"]).observer_location
            if geo:
                return geo
        except Exception as exc:
            logger.debug("Failed to get observer location from wayfinder: %s", exc)
        # Default fallback: Denver, CO
        return {"latitude": 39.7392, "longitude": -104.9903, "elevation": 1600.0}
