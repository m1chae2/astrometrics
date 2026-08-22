"""High-level telescope control operations.

TelescopeService manages high-level astronomical telescope control
operations, delegating all core hardware interaction and observation
state logic directly to the wayfindinglib domain high-level interface.
"""

import logging
from typing import Any

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
            data = self.wayfinder.control.get_telescope_status()
        except Exception as e:
            logger.warning(f"Failed to query telescope status (telescope may be offline): {e}")
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

        # Inject real guiding history if service is available
        if self._guiding_service:
            guiding_status = self._guiding_service.get_status()
            data["guidingHistory"] = guiding_status.get("history", [])

        # Inject real alignment attempt history if service is available
        if self._alignment_service:
            data["alignmentAttempts"] = self._alignment_service.get_attempts()
            data["alignmentActive"] = self._alignment_service.is_active()

        if self._astrometrics_service:
            self._astrometrics_service.update_telescope_state(data)

        return data

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
        return self.wayfinder.control.connect()

    def slew_to_coordinates(self, ra: float, dec: float) -> bool:
        """Command the telescope to slew to the specified coordinates.

        Returns
        -------
        result : `bool`
            `True` if the slew command succeeded.

        Raises
        ------
        HardwareCommandError
            If the hardware rejects or fails the slew command.
        """
        # REQ: BKD-1.2: The backend SHALL provide a generic interface
        # for Telescope control (Slew, Sync, Park, Track).
        from backend.exceptions import HardwareCommandError
        from wayfindinglib import AstrometryHardwareError

        try:
            return self.wayfinder.control.slew_to_coordinates(ra, dec)
        except (AstrometryHardwareError, ValueError) as e:
            raise HardwareCommandError(str(e)) from e

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
            If ``target_name`` is empty, or the name cannot be
            resolved for a reason other than not being found.
        TargetNotFoundError
            If ``target_name`` is not present in the library.
        """
        from backend.exceptions import InvalidArgumentError, TargetNotFoundError

        if not target_name or not target_name.strip():
            raise InvalidArgumentError("target_name must not be empty")

        try:
            return self.wayfinder.control.slew_to_target(target_name)
        except ValueError as e:
            if "not found in library" in str(e):
                raise TargetNotFoundError(str(e)) from e
            raise InvalidArgumentError(str(e)) from e

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
        HardwareCommandError
            If the hardware rejects or fails the park command.
        """
        from backend.exceptions import HardwareCommandError
        from wayfindinglib import AstrometryHardwareError

        try:
            return self.wayfinder.control.park()
        except (AstrometryHardwareError, ValueError) as e:
            raise HardwareCommandError(str(e)) from e

    def unpark_telescope(self) -> bool:
        """Command the telescope to unpark via the high-level interface.

        Returns
        -------
        result : `bool`
            `True` if the unpark command succeeded.

        Raises
        ------
        HardwareCommandError
            If the hardware rejects or fails the unpark command.
        """
        from backend.exceptions import HardwareCommandError
        from wayfindinglib import AstrometryHardwareError

        try:
            return self.wayfinder.control.unpark()
        except (AstrometryHardwareError, ValueError) as e:
            raise HardwareCommandError(str(e)) from e

    def set_tracking(self, enabled: bool) -> bool:
        """Set tracking state via the high-level interface.

        Returns
        -------
        result : `bool`
            `True` if the tracking command succeeded.

        Raises
        ------
        HardwareCommandError
            If the hardware rejects or fails the tracking command.
        """
        from backend.exceptions import HardwareCommandError
        from wayfindinglib import AstrometryHardwareError

        try:
            return self.wayfinder.control.set_tracking(enabled)
        except (AstrometryHardwareError, ValueError) as e:
            raise HardwareCommandError(str(e)) from e

    def manual_move(self, direction: str, start: bool = True) -> bool:
        """Start or stop manual movement via the high-level interface.

        Returns
        -------
        result : `bool`
            `True` if the movement command succeeded.

        Raises
        ------
        HardwareCommandError
            If the hardware rejects or fails the movement command.
        """
        from backend.exceptions import HardwareCommandError
        from wayfindinglib import AstrometryHardwareError

        try:
            return self.wayfinder.control.manual_move(direction, start)
        except (AstrometryHardwareError, ValueError) as e:
            raise HardwareCommandError(str(e)) from e

    def abort_motion(self) -> bool:
        """Abort all telescope mount motion immediately via astrometrics.

        Returns
        -------
        result : `bool`
            `True` if the abort command succeeded.

        Raises
        ------
        HardwareCommandError
            If the hardware rejects or fails the abort command.
        """
        from backend.exceptions import HardwareCommandError
        from wayfindinglib import AstrometryHardwareError

        try:
            return self.wayfinder.control.abort_motion()
        except (AstrometryHardwareError, ValueError) as e:
            raise HardwareCommandError(str(e)) from e

    def set_slew_rate(self, rate_index: int) -> bool:
        """Set slew rate (0-3) via the high-level interface.

        Returns
        -------
        result : `bool`
            `True` if the slew rate command succeeded.

        Raises
        ------
        HardwareCommandError
            If the hardware rejects or fails the slew rate command.
        """
        from backend.exceptions import HardwareCommandError
        from wayfindinglib import AstrometryHardwareError

        try:
            return self.wayfinder.control.set_slew_rate(rate_index)
        except (AstrometryHardwareError, ValueError) as e:
            raise HardwareCommandError(str(e)) from e

    def focus_move(self, steps: int) -> bool:
        """Move focuser via the high-level interface.

        Returns
        -------
        result : `bool`
            `True` if the focuser move command succeeded.

        Raises
        ------
        HardwareCommandError
            If the hardware rejects or fails the focuser move command.
        """
        # REQ: BKD-1.4: The backend SHALL provide a generic interface
        # for Focuser control (Move, Position).
        from backend.exceptions import HardwareCommandError
        from wayfindinglib import AstrometryHardwareError

        try:
            return self.wayfinder.control.focus_move(steps)
        except (AstrometryHardwareError, ValueError) as e:
            raise HardwareCommandError(str(e)) from e

    def get_focuser_position(self) -> int:
        """Get current focuser position via the high-level interface.

        Returns
        -------
        result : `int`
            Current focuser step position.

        Raises
        ------
        HardwareCommandError
            If the hardware rejects or fails the query.
        """
        from backend.exceptions import HardwareCommandError
        from wayfindinglib import AstrometryHardwareError

        try:
            return self.wayfinder.control.get_focuser_position()
        except (AstrometryHardwareError, ValueError) as e:
            raise HardwareCommandError(str(e)) from e

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
            If ``filter_name`` is empty.
        FilterNotFoundError
            If ``filter_name`` is not recognized by the filterwheel.
        HardwareCommandError
            If the hardware rejects or fails the filter command for
            any other reason.
        """
        from backend.exceptions import (
            FilterNotFoundError,
            HardwareCommandError,
            InvalidArgumentError,
        )
        from wayfindinglib import AstrometryHardwareError

        if not filter_name or not filter_name.strip():
            raise InvalidArgumentError("filter_name must not be empty")

        try:
            return self.wayfinder.control.set_filter(filter_name)
        except (AstrometryHardwareError, ValueError) as e:
            if "not recognized" in str(e):
                raise FilterNotFoundError(str(e)) from e
            raise HardwareCommandError(str(e)) from e

    def get_indi_devices(self) -> list:
        """RPC wrapper to get list of active INDI device names.

        Returns
        -------
        result : `list`
            Active INDI device names.
        """
        return self.wayfinder.control.get_indi_devices()

    def get_indi_properties(self, device_name: str) -> dict:
        """RPC wrapper to get all properties for a specified INDI device.

        Returns
        -------
        result : `dict`
            All properties for the specified device.
        """
        return self.wayfinder.control.indi_properties(device_name)

    def set_indi_property(
        self, device_name: str, property_name: str, value: str, element: str | None = None
    ) -> bool:
        """RPC wrapper to set a specific element of an INDI property.

        Returns
        -------
        result : `bool`
            `True` if the property was set successfully.
        """
        return self.wayfinder.control.set_indi_property(device_name, property_name, value, element)

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
            geo = self.wayfinder.control.get_observer_location()
            if geo:
                return geo
        except Exception as exc:
            logger.debug("Failed to get observer location from wayfinder: %s", exc)
        # Default fallback: Denver, CO
        return {"latitude": 39.7392, "longitude": -104.9903, "elevation": 1600.0}
