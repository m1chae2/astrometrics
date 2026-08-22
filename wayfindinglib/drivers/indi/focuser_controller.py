"""Focuser controller for INDI devices.

Handles absolute and relative focusing.
"""

import logging

from .pyindi_compatibility import PyIndi

logger = logging.getLogger(__name__)


class FocuserController:
    """Manages focuser operations via INDI."""

    def __init__(self, client):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.client = client

    def move_to(self, focuser, position: int) -> bool:  # ruff: ignore[missing-type-function-argument]
        """Move the focuser to an absolute position.

        Returns
        -------
        success : `bool`
            `True` if the absolute position command was sent.
        """
        absolute_position = focuser.getNumber("ABS_FOCUS_POSITION")
        if absolute_position:
            absolute_position[0].value = position
            self.client.sendNewNumber(absolute_position)
            return True
        return False

    def move_relative(self, focuser, steps: int) -> bool:  # ruff: ignore[missing-type-function-argument]
        """Move the focuser by a relative step count (positive or negative).

        Prefers REL_FOCUS_POSITION when available. Some drivers
        (including this project's INDI simulator) pair
        REL_FOCUS_POSITION with a FOCUS_MOTION direction switch -- and
        sometimes a Mode/FOCUS_MODE switch that must be set to
        "Relative" first -- rather than accepting a signed value
        directly. Falls back to reading + writing ABS_FOCUS_POSITION
        if no relative property exists.

        Returns
        -------
        success : `bool`
            `True` if the relative move command was sent.
        """
        if not focuser:
            return False

        relative_position = focuser.getNumber("REL_FOCUS_POSITION")
        if relative_position:
            motion = focuser.getSwitch("FOCUS_MOTION")
            if motion:
                return self._move_relative_via_direction_switch(focuser, motion, relative_position, steps)
            # No direction switch: assume REL_FOCUS_POSITION accepts a
            # signed value directly.
            relative_position[0].value = steps
            self.client.sendNewNumber(relative_position)
            return True

        return self._move_relative_via_absolute_fallback(focuser, steps)

    def _move_relative_via_direction_switch(self, focuser, motion, relative_position, steps: int) -> bool:  # ruff: ignore[missing-type-function-argument]
        """Handle the REL_FOCUS_POSITION + FOCUS_MOTION direction-switch case.

        This is the pattern used by drivers that pair a relative-position
        number property with a direction switch.

        Returns
        -------
        success : `bool`
            `True` if a matching direction switch was found and the
            move command was sent.
        """
        # Some simulators expose a Mode/FOCUS_MODE switch that must be set to
        # "Relative" before REL_FOCUS_POSITION commands are honored.
        focus_mode = focuser.getSwitch("Mode") or focuser.getSwitch("FOCUS_MODE")
        if focus_mode:
            relative_mode_switch = None
            for mode_switch_element in focus_mode:
                if "relative" in mode_switch_element.getName().lower():
                    relative_mode_switch = mode_switch_element
                    break
            if relative_mode_switch and relative_mode_switch.getState() != PyIndi.ISS_ON:
                for mode_switch_element in focus_mode:
                    mode_switch_element.setState(PyIndi.ISS_OFF)
                relative_mode_switch.setState(PyIndi.ISS_ON)
                self.client.sendNewSwitch(focus_mode)

        direction = "FOCUS_OUT" if steps > 0 else "FOCUS_IN"
        sim_style = any(
            "INWARD" in motion_switch_element.getName() or "OUTWARD" in motion_switch_element.getName()
            for motion_switch_element in motion
        )
        if sim_style:
            direction = "FOCUS_OUTWARD" if steps > 0 else "FOCUS_INWARD"

        found_direction = False
        for motion_switch_element in motion:
            if motion_switch_element.getName() == direction:
                motion_switch_element.setState(PyIndi.ISS_ON)
                found_direction = True
            else:
                motion_switch_element.setState(PyIndi.ISS_OFF)

        if not found_direction:
            return False

        self.client.sendNewSwitch(motion)
        relative_position[0].value = float(abs(steps))
        self.client.sendNewNumber(relative_position)
        return True

    def _move_relative_via_absolute_fallback(self, focuser, steps: int) -> bool:  # ruff: ignore[missing-type-function-argument]
        """Simulate a relative move.

        Does so by reading and writing ABS_FOCUS_POSITION.

        Returns
        -------
        success : `bool`
            `True` if the absolute position was read and updated.
        """
        absolute_position = focuser.getNumber("ABS_FOCUS_POSITION")
        if not absolute_position:
            return False
        current = absolute_position[0].value
        absolute_position[0].value = current + steps
        self.client.sendNewNumber(absolute_position)
        return True

    def get_position(self, focuser) -> int:  # ruff: ignore[missing-type-function-argument]
        """Return the current focuser position.

        Returns
        -------
        position : `int`
            The absolute focuser position, or ``0`` if unavailable.
        """
        absolute_position = focuser.getNumber("ABS_FOCUS_POSITION")
        if absolute_position:
            return int(absolute_position[0].value)
        return 0
