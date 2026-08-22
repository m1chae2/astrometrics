"""Utilities for waiting on asynchronous INDI property confirmations.

INDI's client-server protocol is asynchronous: sending a new property value
(e.g. via sendNewSwitch) submits a request, and the driver validates and
applies it in its own time, pushing the confirmed value back to the client.
A command function returning True does not by itself guarantee the device
has applied the change -- callers that need to know must poll until the
property confirms, rather than assume success after a fixed sleep.
"""

import time


def wait_for_switch_state(
    device,  # ruff: ignore[missing-type-function-argument]
    property_name: str,
    element_name: str,
    expected_state,  # ruff: ignore[missing-type-function-argument]
    timeout: float = 5.0,
    fallback_name: str | None = None,
    poll_interval: float = 0.2,
) -> bool:
    """Poll a device's switch property until an element reaches expected_state.

    Re-fetches the property from the device on every iteration, since a
    single cached snapshot can be stale relative to the driver's
    asynchronous updates.

    Parameters
    ----------
    device : PyIndi.BaseDevice
        The device to poll.
    property_name : str
        Primary switch vector property name to look up.
    element_name : str
        Name of the switch element within the vector to check.
    expected_state : PyIndi.ISState
        The state (ISS_ON/ISS_OFF) to wait for.
    timeout : float
        Maximum seconds to poll before giving up.
    fallback_name : Optional[str]
        Alternate property name to try if property_name isn't found.
    poll_interval : float
        Seconds to sleep between polls.

    Returns
    -------
    bool
        True if expected_state was observed within timeout, else False.
    """
    deadline = time.time() + timeout
    while True:
        property_vector = device.getSwitch(property_name)
        if not property_vector and fallback_name:
            property_vector = device.getSwitch(fallback_name)
        if property_vector:
            for i in range(len(property_vector)):
                if property_vector[i].getName() == element_name:
                    if property_vector[i].getState() == expected_state:
                        return True
                    break
        if time.time() >= deadline:
            return False
        time.sleep(poll_interval)
