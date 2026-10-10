"""Enclosure controller for INDI devices.

Handles roll-off-roof/dome shutter state, open, and close, mirroring
`mount_controller.py`'s send/confirm pattern. Uses the standard INDI
Dome Interface's ``DOME_SHUTTER`` switch (elements ``SHUTTER_OPEN``/
``SHUTTER_CLOSE``), the same property both roll-off-roof and dome INDI
drivers publish for shutter motion.
"""

import logging
from typing import TYPE_CHECKING

from wayfindinglib.models.equipment_and_site.enclosure import EnclosureState

from .property_wait import wait_for_switch_state
from .pyindi_compatibility import PyIndi

if TYPE_CHECKING:
    from wayfindinglib.drivers.indi_interface import IndiInterface

logger = logging.getLogger(__name__)

_SHUTTER_PROPERTY = "DOME_SHUTTER"
_OPEN_ELEMENT = "SHUTTER_OPEN"
_CLOSE_ELEMENT = "SHUTTER_CLOSE"


class EnclosureController:
    """Manages roll-off-roof/dome shutter operations via INDI."""

    def __init__(self, client: IndiInterface) -> None:
        self.client = client

    def get_state(self, enclosure_device: PyIndi.BaseDevice) -> EnclosureState:
        """Return the enclosure's current motion state.

        Reads both the ``DOME_SHUTTER`` switch vector's own state
        (``IPS_BUSY``/``IPS_ALERT`` report motion/fault) and which
        element is `ISS_ON` (settled open/closed) -- the same
        vector-state-plus-element pattern already used for camera
        exposure status (`indi_interface.py`'s `_refresh_camera_status`).

        Returns
        -------
        state : `EnclosureState`
            `UNKNOWN` if the device or property is unavailable or
            ambiguous, per the "Unknown Is Unsafe" invariant.
        """
        if not enclosure_device:
            return EnclosureState.UNKNOWN
        shutter_switch = enclosure_device.getSwitch(_SHUTTER_PROPERTY)
        if not shutter_switch:
            return EnclosureState.UNKNOWN

        vector_state = getattr(shutter_switch, "s", None)
        if vector_state == PyIndi.IPS_ALERT:
            return EnclosureState.FAULT

        open_on = False
        close_on = False
        for i in range(len(shutter_switch)):
            element = shutter_switch[i]
            if element.getName() == _OPEN_ELEMENT and element.getState() == PyIndi.ISS_ON:
                open_on = True
            elif element.getName() == _CLOSE_ELEMENT and element.getState() == PyIndi.ISS_ON:
                close_on = True

        if vector_state == PyIndi.IPS_BUSY:
            if open_on:
                return EnclosureState.OPENING
            if close_on:
                return EnclosureState.CLOSING
            return EnclosureState.UNKNOWN

        if open_on and not close_on:
            return EnclosureState.OPEN
        if close_on and not open_on:
            return EnclosureState.CLOSED
        return EnclosureState.UNKNOWN

    def open(self, enclosure_device: PyIndi.BaseDevice, timeout: float = 5.0) -> bool:
        """Command the shutter open, waiting for the driver to confirm.

        Returns
        -------
        bool
            True if the open command was sent and confirmed, False
            otherwise.
        """
        return self._set_shutter(enclosure_device, open_shutter=True, timeout=timeout)

    def close(self, enclosure_device: PyIndi.BaseDevice, timeout: float = 5.0) -> bool:
        """Command the shutter closed, waiting for the driver to confirm.

        Returns
        -------
        bool
            True if the close command was sent and confirmed, False
            otherwise.
        """
        return self._set_shutter(enclosure_device, open_shutter=False, timeout=timeout)

    def _set_shutter(
        self, enclosure_device: PyIndi.BaseDevice, *, open_shutter: bool, timeout: float
    ) -> bool:
        """Send the `DOME_SHUTTER` switch and confirm the requested element.

        Returns
        -------
        bool
            True if the command was sent and confirmed, False
            otherwise.
        """
        if not enclosure_device:
            return False
        shutter_switch = enclosure_device.getSwitch(_SHUTTER_PROPERTY)
        if not shutter_switch:
            return False

        target_element = _OPEN_ELEMENT if open_shutter else _CLOSE_ELEMENT
        for i in range(len(shutter_switch)):
            is_target = shutter_switch[i].getName() == target_element
            shutter_switch[i].s = PyIndi.ISS_ON if is_target else PyIndi.ISS_OFF
        self.client.sendNewSwitch(shutter_switch)
        return wait_for_switch_state(
            enclosure_device, _SHUTTER_PROPERTY, target_element, PyIndi.ISS_ON, timeout=timeout
        )
