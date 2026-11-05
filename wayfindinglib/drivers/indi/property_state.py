"""Purpose: Read the state of an INDI property vector in one place.

Description: A PyIndi 2.x property object (for example the
`PropertyNumber` that `getNumber` returns) reports its state with
`getState()`. It has no `s` attribute. Code that reads `.s` gets `None`
every time and never notices. These helpers read the state the supported
way and turn it into words for status displays.
"""

import logging
from typing import Any

from .pyindi_compatibility import INDI_ERRORS, PyIndi

logger = logging.getLogger(__name__)


def get_property_state(property_vector: Any) -> int | None:
    """Read the state (idle, ok, busy, alert) of an INDI property.

    Parameters
    ----------
    property_vector : `Any`
        A PyIndi property object, such as the result of `getNumber`.

    Returns
    -------
    state : `int` | `None`
        The `IPS_*` state value, or `None` when the object has no
        `getState` method or the call fails. This runs on a poll loop, so
        a missing state is logged at debug level only.
    """
    getter = getattr(property_vector, "getState", None)
    if getter is None:
        logger.debug("INDI property %r has no getState(); its state is unknown", property_vector)
        return None
    try:
        return getter()
    except INDI_ERRORS as state_error:
        logger.debug("Could not read INDI property state: %s", state_error)
        return None


def property_state_name(state: int | None) -> str:
    """Turn an INDI property state value into a readable word.

    Parameters
    ----------
    state : `int` | `None`
        An `IPS_*` value, or `None` when the state is unknown.

    Returns
    -------
    name : `str`
        "Idle", "Ok", "Busy" or "Alert", or "Unknown" for `None` or any
        other value.
    """
    names = {
        PyIndi.IPS_IDLE: "Idle",
        PyIndi.IPS_OK: "Ok",
        PyIndi.IPS_BUSY: "Busy",
        PyIndi.IPS_ALERT: "Alert",
    }
    if state is None:
        return "Unknown"
    return names.get(state, "Unknown")
