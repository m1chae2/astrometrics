"""Manages the lifecycle and health of the INDI server connection."""

import logging
import socket
import time

from astrometricslib.drivers import OfflineModeError, require_local_host
from wayfindinglib.drivers.host_resolver import KnownHostAddress

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_PROBE_FAILURES = 3
"""Failed handshakes in a row before a connected server counts as lost.

Design estimate. Handshakes run at least 5 s apart, so 3 in a row means the
server stayed silent for about 15 s or more. That rides out a short stall of
the network or of the server, and a hung server is still replaced in well
under a minute. A failure of the name lookup alone never counts.
"""


class ConnectionManager:
    """Handle the INDI server connection lifecycle and responsiveness."""

    def __init__(self, hostname: str, port: int = 7624) -> None:
        self.known_address = KnownHostAddress(hostname)
        self.port = port
        self.base_connection_cooldown = 5.0  # Seconds between attempts while connected or newly failed
        self.max_connection_cooldown = 30.0  # Longest wait between attempts to a host that stays down
        self.connection_cooldown = self.base_connection_cooldown  # Seconds between connection attempts
        self.consecutive_failures = 0
        self.last_connection_attempt = 0
        self.last_responsive_check = 0
        self.last_responsive_value = False
        self.consecutive_probe_failures = 0
        self.connect_address: str | None = None  # Address chosen by the last `is_host_resolvable`

    @property
    def hostname(self) -> str:
        """Host name (or address) the manager talks to."""
        return self.known_address.hostname

    @hostname.setter
    def hostname(self, hostname: str) -> None:
        """Use a new host name; this also forgets the saved address."""
        self.known_address.set_hostname(hostname)

    def adopt_state_from(self, previous: ConnectionManager) -> None:
        """Take over the retry timing and saved address of an older manager.

        Used when the INDI client is replaced during an outage. The new
        client keeps the old one's cooldown, failure count and the last
        address the host name resolved to, so the swap does not make the
        app forget where the telescope computer was.

        Parameters
        ----------
        previous : `ConnectionManager`
            The manager of the client being replaced.
        """
        self.last_connection_attempt = previous.last_connection_attempt
        self.connection_cooldown = previous.connection_cooldown
        self.consecutive_failures = previous.consecutive_failures
        self.known_address = previous.known_address

    def probe_failures_exhausted(self) -> bool:
        """Tell whether enough handshakes failed in a row.

        Returns
        -------
        lost : `bool`
            `True` after `MAX_CONSECUTIVE_PROBE_FAILURES` failed handshakes in
            a row. A failed name lookup is not counted.
        """
        return self.consecutive_probe_failures >= MAX_CONSECUTIVE_PROBE_FAILURES

    def reset_probe_failures(self) -> None:
        """Forget earlier failed handshakes (after a reconnect)."""
        self.consecutive_probe_failures = 0

    def is_server_responsive(self) -> bool:
        """Check if the INDI server is reachable and responsive.

        Sends a handshake to verify. Caches result for 2 seconds to
        prevent blocking frequent status polls.

        Returns
        -------
        responsive : `bool`
            `True` if the server responded to the handshake.
        """
        now = time.time()
        if (now - self.last_responsive_check) < 5.0:
            return self.last_responsive_value

        self.last_responsive_check = now
        try:
            require_local_host(self.known_address.hostname, "The INDI server")
        except OfflineModeError:
            self.last_responsive_value = False
            return False
        address = self.known_address.resolve(self.port)
        if address is None:
            # Only the name lookup failed and no earlier address is known.
            # That says nothing about the server, so it is not counted.
            self.last_responsive_value = False
            return False
        try:
            with socket.create_connection((address, self.port), timeout=2.0) as sock:
                # Send a basic command to elicit a response.
                sock.sendall(b"<getProperties version='1.7'/>")
                data = sock.recv(1024)
            self.last_responsive_value = len(data) > 0
        except OSError:
            # OSError covers a refused connection and a timeout.
            self.last_responsive_value = False
        if self.last_responsive_value:
            self.consecutive_probe_failures = 0
        else:
            self.consecutive_probe_failures += 1
        return self.last_responsive_value

    def is_host_resolvable(self, host: str, port: int, timeout: float = 1.0) -> bool:
        """Check, without waiting long, that the server name can be looked up.

        If the name does not resolve but did earlier, the last known
        address is used instead and left in `connect_address`.

        Looking up a ``.local`` name that nothing answers takes about 5 seconds
        inside the INDI client's own connect call, and that call holds the one
        worker that serves every INDI request, so a switched-off telescope
        stalled unrelated calls (such as the device list) for seconds. The
        lookup is done here in a helper thread instead and given `timeout`
        seconds; a lookup that is still running is left to finish in the
        background and the attempt is skipped.

        Parameters
        ----------
        host : `str`
            Server host name or address.
        port : `int`
            Server port.
        timeout : `float`, optional
            Seconds to wait for the lookup. Defaults to 1.

        Returns
        -------
        resolvable : `bool`
            `True` if the name resolved within `timeout`, or an earlier
            address is known for it.
        """
        try:
            require_local_host(host, "The INDI server")
        except OfflineModeError:
            # Nothing leaves this computer in this launch: a status.
            self.connect_address = None
            return False
        self.known_address.set_hostname(host)
        self.connect_address = self.known_address.resolve(port, timeout)
        return self.connect_address is not None

    def record_connection_result(self, connected: bool) -> None:
        """Remember whether a connection attempt worked, and adjust the wait.

        A failed attempt to a host that is switched off can block for about
        5 seconds (looking up a ``.local`` name that does not answer), so
        retrying every 5 seconds kept the connection thread busy nearly all
        the time. Each failure in a row doubles the wait, up to
        `max_connection_cooldown`; a success puts it back to the base value.

        Parameters
        ----------
        connected : `bool`
            Whether the attempt ended with a connected server.
        """
        if connected:
            self.consecutive_probe_failures = 0
            self.consecutive_failures = 0
            self.connection_cooldown = self.base_connection_cooldown
            return
        self.consecutive_failures += 1
        self.connection_cooldown = min(
            self.max_connection_cooldown,
            self.base_connection_cooldown * 2 ** (self.consecutive_failures - 1),
        )

    def can_attempt_reconnect(self) -> bool:
        """Determine if enough time has passed to attempt a reconnection.

        Returns
        -------
        can_reconnect : `bool`
            `True` if the cooldown has elapsed (also updates the
            last-attempt timestamp as a side effect).
        """
        now = time.time()
        if (now - self.last_connection_attempt) >= self.connection_cooldown:
            self.last_connection_attempt = now
            return True
        return False
