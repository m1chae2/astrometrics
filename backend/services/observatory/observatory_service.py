"""Observatory Service.

This service coordinates interactions with peripheral observatory devices:
- Dome/Roof control (Slaving to mount, Open/Close/Shutter).
- Weather Monitoring (Cloud sensors, Rain sensors).
- Power Control (Switching equipment on/off).
- Safety Monitoring.
"""

import logging

from wayfindinglib.api.control_registry import ObservatoryControl

logger = logging.getLogger(__name__)


class ObservatoryService:
    """Manage observatory-wide safety and peripheral hardware.

    Covers Domes, Power, and Weather via `ObservatoryControl`.
    """

    def __init__(self, observatory_api: ObservatoryControl) -> None:
        """Initialize the ObservatoryService.

        Parameters
        ----------
        observatory_api : `ObservatoryControl`
            The shared high-level hardware-control interface -- no
            deprecated `indi_interface`/`wayfinder` fallback; every
            caller passes this directly (§4's "one interface per
            device type" migration, M9's backend cleanup).
        """
        self._observatory = observatory_api

    def check_safety(self) -> dict:
        """Check the observatory status via `ObservatoryControl`.

        Flagged, not fixed, in this pass: the humidity/connectivity
        check below duplicates real, more capable machinery this
        library already has (`ObservatoryControl.assess_safety`'s
        `SafetyMonitor`, with configurable rules and hysteresis) --
        worth a follow-up to replace this ad hoc threshold with that.

        Returns
        -------
        status : `dict`
            Safety status including safety flag, connection state, enclosure,
            and humidity.
        """
        from wayfindinglib.tasks.control_tasks import hardware_operations

        telescope_status = hardware_operations.get_telescope_status(self._observatory)
        is_connected = telescope_status["connectionStatus"] == "Connected"

        humidity_str = str(telescope_status.get("humidity", "0%")).replace("%", "")
        humidity = float(humidity_str) if humidity_str.replace(".", "").isdigit() else 0.0

        enclosure = self._observatory.active_enclosure()
        enclosure_state = enclosure.enclosure_type.name if enclosure else "NONE"

        is_safe = is_connected and (humidity < 90.0)

        return {
            "safe": is_safe,
            "connected": is_connected,
            "humidity": humidity,
            "enclosure": enclosure_state,
            "reason": "" if is_safe else ("Disconnected" if not is_connected else "High Humidity"),
        }
