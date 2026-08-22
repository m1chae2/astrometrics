"""Observatory Service.

This service coordinates interactions with peripheral observatory devices:
- Dome/Roof control (Slaving to mount, Open/Close/Shutter).
- Weather Monitoring (Cloud sensors, Rain sensors).
- Power Control (Switching equipment on/off).
- Safety Monitoring.
"""

import logging

from wayfindinglib import IndiInterface

logger = logging.getLogger(__name__)


class ObservatoryService:
    """Manage observatory-wide safety and peripheral hardware.

    Covers Domes, Power, and Weather via the Wayfinder high-level interface.
    """

    def __init__(self, indi_interface: IndiInterface | None = None, wayfinder=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        """Initialize the ObservatoryService."""
        if wayfinder is None:
            from wayfindinglib import Wayfinder

            self.wayfinder = Wayfinder()
        else:
            self.wayfinder = wayfinder

        if indi_interface is not None:
            self.wayfinder.control.driver = indi_interface

    @property
    def indi(self) -> IndiInterface:
        """The active INDI driver interface."""
        return self.wayfinder.control.driver

    def check_safety(self) -> dict:
        """Check the observatory status via the high-level interface.

        Returns
        -------
        status : `dict`
            Safety status including safety flag, connection state, enclosure,
            and humidity.
        """
        driver = self.wayfinder.control.driver
        is_connected = driver.isServerConnected() if driver else False
        status = driver.status if driver else {}

        humidity_str = str(status.get("HUMIDITY", "0%")).replace("%", "")
        humidity = float(humidity_str) if humidity_str.replace(".", "").isdigit() else 0.0

        enclosure = self.wayfinder.control.active_enclosure()
        enclosure_state = enclosure.enclosure_type.name if enclosure else "NONE"

        is_safe = is_connected and (humidity < 90.0)

        return {
            "safe": is_safe,
            "connected": is_connected,
            "humidity": humidity,
            "enclosure": enclosure_state,
            "reason": "" if is_safe else ("Disconnected" if not is_connected else "High Humidity"),
        }
