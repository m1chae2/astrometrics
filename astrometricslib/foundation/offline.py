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
import os

from astrometricslib.foundation.config import get_configuration

logger = logging.getLogger(__name__)

#: Environment variable a launcher sets to ``1`` or ``true`` for a world that
#: must send nothing off the computer. It works even when the settings file
#: cannot be read, so it is a second switch behind ``offline = true``.
OFFLINE_VARIABLE = "ASTROMETRICS_OFFLINE"


class OfflineModeError(ConnectionError):
    """A client was refused: this launch sends nothing off the computer."""


def network_is_offline() -> bool:
    """Say whether this launch sends nothing off the computer.

    Returns
    -------
    offline : `bool`
        `True` when ``ASTROMETRICS_OFFLINE`` is ``1`` or ``true``, or when the
        settings in use have ``offline = true`` (or a value that is not
        clearly false) in their ``[Launch]`` section. A ``[Launch]`` section
        that cannot be read also counts as offline. No ``[Launch]`` section
        is an ordinary, online run.
    """
    if os.environ.get(OFFLINE_VARIABLE, "").strip().lower() in ("1", "true"):
        return True
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
        loopback IPv4 or IPv6 address. The address may be in brackets, may
        carry a zone (``%lo``), and may be an IPv4-mapped IPv6 address such as
        ``::ffff:127.0.0.1``, which is judged by the IPv4 address inside it.
    """
    name = host.strip().strip("[]").split("%")[0].lower()
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


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
