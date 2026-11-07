"""Remember the last good network address of the telescope computer.

Looking up a ``.local`` (mDNS) name can fail for a while even though the
telescope computer is up and other links to it (such as a VNC session that
already holds its address) keep working. The app looks the name up many times
a minute, so one failed lookup used to look like a dead telescope. This module
keeps the address that the name last resolved to, so both the INDI client and
the StellarMate ssh driver can keep using the machine while only the name
lookup is broken.
"""

import concurrent.futures
import ipaddress
import logging
import socket
import threading

logger = logging.getLogger(__name__)

_LOOKUP_THREAD = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="HostLookup")
"""One helper thread for name lookups, so lookups run one at a time."""

_MAX_PENDING_LOOKUPS = 4
"""Most lookups that may be running or waiting for the helper thread.

Design estimate. The helper thread runs one lookup at a time, and one stuck
``.local`` lookup can take about 5 s. The app asks many times a minute, so
without a cap the waiting lookups would pile up behind it. With the cap, a
request made while 4 are already pending is treated as a failed lookup and
returns the saved address, if there is one.
"""

_PENDING_LOOKUPS = threading.BoundedSemaphore(_MAX_PENDING_LOOKUPS)
"""Counts the pending lookups; a slot is freed when a lookup finishes."""


def is_address_literal(host: str) -> bool:
    """Tell whether a host string is already an IP address.

    Parameters
    ----------
    host : `str`
        A host name or an IPv4 or IPv6 address.

    Returns
    -------
    is_address : `bool`
        `True` if `host` is an IP address, so no name lookup is needed.
    """
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


class KnownHostAddress:
    """Look up a host name and fall back to the last address it resolved to.

    One instance serves one host name. A successful lookup stores the address.
    A failed lookup returns the stored address, if there is one, so callers can
    go on talking to the machine. Each change of state is logged once: the
    first fallback to the stored address (info), a failed lookup with nothing
    stored (warning), and the name resolving again (info).
    """

    def __init__(self, hostname: str) -> None:
        """Start with no stored address.

        Parameters
        ----------
        hostname : `str`
            Host name from the configuration (or an IP address).
        """
        self._lock = threading.Lock()
        self.hostname = hostname
        self.cached_address: str | None = None
        self._lookup_is_failing = False

    def set_hostname(self, hostname: str) -> None:
        """Use a new host name, and forget the address stored for the old one.

        Parameters
        ----------
        hostname : `str`
            Host name from the configuration. Nothing changes if it is the
            same as the current one.
        """
        with self._lock:
            if hostname != self.hostname:
                self.hostname = hostname
                self.cached_address = None
                self._lookup_is_failing = False

    def resolve(self, port: int, timeout: float = 1.0) -> str | None:
        """Return the address to connect to.

        Parameters
        ----------
        port : `int`
            Port the caller will connect to (used only for the lookup).
        timeout : `float`, optional
            Seconds to wait for the lookup. A lookup still running is left to
            finish in the background and counts as a failure for now.

        Returns
        -------
        address : `str` or `None`
            The host name itself if it resolves (or an IP address given
            instead of a name), otherwise the last address the name resolved
            to, or `None` if the name never resolved.
        """
        with self._lock:
            hostname = self.hostname
        if is_address_literal(hostname):
            return hostname

        name_resolves = False
        resolved = None
        if _PENDING_LOOKUPS.acquire(blocking=False):
            try:
                lookup = _LOOKUP_THREAD.submit(socket.getaddrinfo, hostname, port, type=socket.SOCK_STREAM)
            except BaseException:
                _PENDING_LOOKUPS.release()
                raise
            lookup.add_done_callback(lambda _finished: _PENDING_LOOKUPS.release())
            try:
                results = lookup.result(timeout=timeout)
                name_resolves = True
                resolved = _pick_address(results)
            except concurrent.futures.TimeoutError, OSError:
                pass  # Counts as a failed lookup; the saved address is used below.
        else:
            logger.debug("Too many name lookups are pending; treating '%s' as not resolved now.", hostname)

        with self._lock:
            if hostname != self.hostname:
                return None  # The configuration changed during the lookup.
            if name_resolves:
                if self._lookup_is_failing and self.cached_address is not None:
                    logger.info("Host name '%s' resolves again; no longer using the saved address.", hostname)
                self._lookup_is_failing = False
                if resolved is not None:
                    self.cached_address = resolved
                return hostname
            if not self._lookup_is_failing:
                self._lookup_is_failing = True
                if self.cached_address is not None:
                    logger.info(
                        "Cannot look up host name '%s'; using its last known address %s.",
                        hostname,
                        self.cached_address,
                    )
                else:
                    logger.warning(
                        "Cannot look up host name '%s', and no earlier address is known.", hostname
                    )
            return self.cached_address

    def fallback_address(self, port: int, timeout: float = 1.0) -> str | None:
        """Return the stored address only when the name does not resolve now.

        Parameters
        ----------
        port : `int`
            Port the caller will connect to.
        timeout : `float`, optional
            Seconds to wait for the lookup.

        Returns
        -------
        address : `str` or `None`
            The stored address when the lookup failed, otherwise `None`
            (the name works, or nothing is stored).
        """
        address = self.resolve(port, timeout)
        if address is None or address == self.hostname:
            return None
        return address


def _pick_address(results: list) -> str | None:
    """Choose one address from `socket.getaddrinfo` results.

    Parameters
    ----------
    results : `list`
        Tuples as returned by `socket.getaddrinfo`.

    Returns
    -------
    address : `str` or `None`
        The first IPv4 address, else the first address, else `None`.
    """
    addresses = [str(entry[4][0]) for entry in results if entry and len(entry) > 4 and entry[4]]
    for address in addresses:
        if "." in address and ":" not in address:
            return address
    return addresses[0] if addresses else None
