"""Purpose: Keep outgoing clients off the network when the launch says so.

Description: A launch can state ``offline = true`` in its ``[Launch]`` section
(see `astrometricslib.foundation.launch_identity`). Then nothing may leave
this computer: no name lookup, no online catalog, no online plate solver, no
download. Every client in the libraries and the backend that talks to another
computer calls `require_online` (an internet service) or `require_local_host`
(a telescope computer, reached by a host name) before it opens a connection.
A connection to this computer itself (``localhost``, ``127.x.x.x``, ``::1``)
stays allowed, because the stand-in programs of a simulator listen there.

The refusal is an `OfflineModeError`. It is a `ConnectionError`, so the code
that already handles "the service cannot be reached" (the tuple
``ONLINE_QUERY_ERRORS``, for example) treats it as the service being
unavailable. That is a status, not an error in the log.
"""

import ipaddress
import logging

from astrometricslib.foundation.config import get_configuration

logger = logging.getLogger(__name__)


class OfflineModeError(ConnectionError):
    """A client was refused: this launch sends nothing off the computer."""


def network_is_offline() -> bool:
    """Say whether this launch sends nothing off the computer.

    Returns
    -------
    offline : `bool`
        `True` when the settings in use have ``offline = true`` in their
        ``[Launch]`` section.
    """
    identity = get_configuration().get_launch_identity()
    return identity is not None and identity.offline


def is_loopback_host(host: str) -> bool:
    """Say whether a host name or address is this computer.

    No name is looked up, because a lookup is itself an outgoing request.

    Parameters
    ----------
    host : `str`
        A host name or an IP address.

    Returns
    -------
    loopback : `bool`
        `True` for ``localhost``, a name ending in ``.localhost``, and a
        loopback IPv4 or IPv6 address.
    """
    name = host.strip().strip("[]").lower()
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def require_online(service: str) -> None:
    """Refuse an internet request when the launch is offline.

    Parameters
    ----------
    service : `str`
        What the caller was about to reach, for the message (for example
        ``"SIMBAD"``).

    Raises
    ------
    OfflineModeError
        If the launch is offline.
    """
    if network_is_offline():
        raise OfflineModeError(f"{service} is not available: this launch sends nothing off the computer.")


def require_local_host(host: str, service: str) -> None:
    """Refuse a connection to another computer when the launch is offline.

    Parameters
    ----------
    host : `str`
        The host name or address the caller is about to connect to.
    service : `str`
        What the caller was about to reach, for the message (for example
        ``"the telescope computer"``).

    Raises
    ------
    OfflineModeError
        If the launch is offline and `host` is not this computer.
    """
    if network_is_offline() and not is_loopback_host(host):
        raise OfflineModeError(
            f"{service} ({host}) is not available: this launch sends nothing off the computer."
        )
