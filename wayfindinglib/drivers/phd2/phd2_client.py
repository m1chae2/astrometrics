"""Purpose: Transport layer for PHD2's event server.

Description: PHD2 (the guiding program) offers one TCP port. A program that
connects there receives PHD2's events (one JSON object per line) and may send
commands as JSON-RPC requests on the same connection. This module has two
ways to use it:

- `PHD2Client.events` reads the event stream only. It reconnects on a dropped
  connection with the same cooldown discipline
  wayfindinglib.drivers.indi.connection_manager.ConnectionManager already uses
  for INDI reconnects. The guiding telemetry reads this way.
- `PHD2Client.open_session` opens one connection that also sends commands
  (connect the equipment, loop, guide, stop, and questions about PHD2's state)
  and waits for events such as the end of a settle. It is the one PHD2 client
  of this library: the telescope operator, the simulator and the contract
  recorder all use it.

Both refuse a host beyond this computer when the launch is offline.
"""

import json
import logging
import socket
import threading
import time
from collections.abc import Callable, Iterator
from types import TracebackType
from typing import Any

from astrometricslib.drivers import require_local_host
from astrometricslib.exceptions import ExternalServiceError

__all__ = ["DEFAULT_PHD2_PORT", "PHD2Client", "PHD2Session"]

logger = logging.getLogger(__name__)

DEFAULT_PHD2_PORT = 4400

# Matches ConnectionManager.connection_cooldown's INDI reconnect discipline,
# so both hardware integrations back off from a dead endpoint the same way.
_RECONNECT_COOLDOWN_SECONDS = 5.0

# Longest a connect or a read may wait before the stop flag is checked again.
# PHD2 sends nothing while it is idle, so without a limit a read on a quiet
# connection would never return and a stop would never be noticed.
_SOCKET_TIMEOUT_SECONDS = 1.0

# Bytes asked for in one socket read.
_READ_CHUNK_BYTES = 4096

# Longest a command may wait for PHD2's answer unless the caller says more.
# Most answers come at once; the ones that take a while (connecting the
# equipment) pass their own limit.
_DEFAULT_COMMAND_TIMEOUT_SECONDS = 30.0

# Longest the session keeps of the events it has received. A guiding run sends
# about one event a second, so this holds a few hours.
_SESSION_EVENT_LIMIT = 20000


class PHD2Client:
    """TCP client for PHD2: an event stream, or a command session.

    Parameters
    ----------
    host : `str`, optional
        Hostname PHD2's event server is listening on (default
        `"localhost"`).
    port : `int`, optional
        Port PHD2's event server is listening on (default `4400`).
    socket_factory : `Callable[[], socket.socket]`, optional
        Factory used to create the underlying socket. Overridable so
        tests can connect to a local test-only server without needing a
        real PHD2 process. Defaults to a plain `AF_INET`/`SOCK_STREAM`
        socket.
    reconnect_cooldown_seconds : `float`, optional
        Minimum time between connection attempts (default
        `_RECONNECT_COOLDOWN_SECONDS`). Overridable so tests can exercise
        reconnect behavior without waiting the production cooldown.
    socket_timeout_seconds : `float`, optional
        Longest a connect or a read waits before the stop flag is checked
        again (default `_SOCKET_TIMEOUT_SECONDS`).
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = DEFAULT_PHD2_PORT,
        socket_factory: Callable[[], socket.socket] | None = None,
        reconnect_cooldown_seconds: float = _RECONNECT_COOLDOWN_SECONDS,
        socket_timeout_seconds: float = _SOCKET_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize the client without opening a connection yet."""
        self.host = host
        self.port = port
        self._socket_factory = socket_factory or (lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM))
        self._reconnect_cooldown_seconds = reconnect_cooldown_seconds
        self._socket_timeout_seconds = socket_timeout_seconds
        self._last_connection_attempt = 0.0
        # True after a failed connect has been logged, so repeats stay quiet.
        self._connect_failure_reported = False

    def _can_attempt_connect(self) -> bool:
        """Return whether the reconnect cooldown has elapsed.

        Returns
        -------
        can_attempt : `bool`
            `True` if at least `_RECONNECT_COOLDOWN_SECONDS` have passed
            since the last connection attempt, in which case this call
            also records the current attempt's timestamp.
        """
        now = time.time()
        if (now - self._last_connection_attempt) >= self._reconnect_cooldown_seconds:
            self._last_connection_attempt = now
            return True
        return False

    def _connect(self) -> socket.socket:
        """Open a new connection to PHD2's event server.

        Returns
        -------
        connected_socket : `socket.socket`
            The connected socket, ready for line-delimited JSON reads. Its
            connect and reads give up after `socket_timeout_seconds`.

        Raises
        ------
        OSError
            If the connection cannot be made, including a connect that
            takes longer than `socket_timeout_seconds`.
        """
        require_local_host(self.host, "PHD2")
        sock = self._socket_factory()
        sock.settimeout(self._socket_timeout_seconds)
        try:
            sock.connect((self.host, self.port))
        except OSError:
            sock.close()
            raise
        return sock

    def events(self, stop_event: Any | None = None) -> Iterator[dict[str, Any]]:
        """Yield parsed JSON event dicts from PHD2's event stream.

        Reconnects on a dropped connection, subject to the reconnect
        cooldown, until `stop_event` is set. A set stop is noticed within
        about `socket_timeout_seconds`, even on a connection that sends
        nothing.

        Parameters
        ----------
        stop_event : `threading.Event`, optional
            When set, the generator stops yielding and returns. If `None`
            (default), runs until an unrecoverable error occurs.

        Yields
        ------
        event : `dict`
            One decoded JSON message per PHD2 event.
        """

        def stopped() -> bool:
            """Report whether the caller has asked the stream to stop.

            Returns
            -------
            stopped : `bool`
                `True` once `stop_event` is set.
            """
            return stop_event is not None and stop_event.is_set()

        while not stopped():
            if not self._can_attempt_connect():
                time.sleep(0.1)
                continue
            try:
                sock = self._connect()
            except OSError as connect_error:
                # PHD2 may simply not be running. Say so once, at warning
                # level, and keep the repeats out of the job logs (one line
                # every few seconds would bury the lines that matter).
                if self._connect_failure_reported:
                    logger.debug("PHD2 connection to %s:%s still failing", self.host, self.port)
                else:
                    self._connect_failure_reported = True
                    logger.warning(
                        "PHD2 connection to %s:%s failed: %s. Retrying every %.0f s without more warnings.",
                        self.host,
                        self.port,
                        connect_error,
                        self._reconnect_cooldown_seconds,
                    )
                continue

            if self._connect_failure_reported:
                logger.info("PHD2 connection to %s:%s restored", self.host, self.port)
                self._connect_failure_reported = False

            try:
                pending = b""
                while not stopped():
                    try:
                        chunk = sock.recv(_READ_CHUNK_BYTES)
                    except TimeoutError:
                        # Nothing arrived in time; check the stop flag again.
                        continue
                    if not chunk:
                        # PHD2 closed the connection; reconnect later.
                        break
                    pending += chunk
                    while b"\n" in pending:
                        raw_line, pending = pending.split(b"\n", 1)
                        stripped_line = raw_line.decode("utf-8", errors="replace").strip()
                        if not stripped_line:
                            continue
                        try:
                            event = json.loads(stripped_line)
                        except json.JSONDecodeError:
                            logger.warning("Skipping malformed PHD2 event line: %r", stripped_line)
                            continue
                        yield event
                        if stopped():
                            return
            except OSError as read_error:
                logger.warning("PHD2 connection to %s:%s dropped: %s", self.host, self.port, read_error)
            finally:
                sock.close()

    def open_session(self, timeout_seconds: float = 10.0) -> PHD2Session:
        """Open one connection that can send commands and read events.

        Parameters
        ----------
        timeout_seconds : `float`, optional
            Longest to wait for the connection (default 10 s).

        Returns
        -------
        session : `PHD2Session`
            The open connection. Close it (or use it in a ``with`` block).

        Raises
        ------
        OSError
            If nothing accepts the connection. A PHD2 that is not running is
            a status for the caller to report, not an error to log here.
        """
        require_local_host(self.host, "PHD2")
        sock = self._socket_factory()
        sock.settimeout(timeout_seconds)
        try:
            sock.connect((self.host, self.port))
        except OSError:
            sock.close()
            raise
        sock.settimeout(None)
        return PHD2Session(sock, f"{self.host}:{self.port}")


class PHD2Session:
    """One open connection to PHD2: commands go out, events come in.

    A background thread reads every line PHD2 sends. A line with an ``Event``
    key is kept in the session's event list. A line with an ``id`` key is the
    answer to a command and goes to the command that is waiting for it.

    Parameters
    ----------
    connection : `socket.socket`
        A connected socket. The session owns it and closes it.
    label : `str`, optional
        ``host:port`` for messages.
    """

    def __init__(self, connection: socket.socket, label: str = "PHD2") -> None:
        """Keep the socket and start the reader thread."""
        self._socket = connection
        self.label = label
        self._events: list[dict[str, Any]] = []
        self._events_dropped = 0
        self._responses: dict[int, dict[str, Any]] = {}
        self._condition = threading.Condition()
        self._send_lock = threading.Lock()
        self._next_request_id = 1
        self._closed = False
        threading.Thread(target=self._read_loop, daemon=True, name="PHD2Session").start()

    def __enter__(self) -> PHD2Session:
        """Return the session for a ``with`` block.

        Returns
        -------
        session : `PHD2Session`
            This session.
        """
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the connection at the end of a ``with`` block."""
        self.close()

    def _read_loop(self) -> None:
        """Split the stream into lines; file each as an event or an answer."""
        pending = b""
        while True:
            try:
                chunk = self._socket.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            pending += chunk
            while b"\n" in pending:
                raw_line, pending = pending.split(b"\n", 1)
                text = raw_line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed PHD2 line: %r", text)
                    continue
                if not isinstance(message, dict):
                    continue
                with self._condition:
                    if "Event" in message:
                        self._events.append(message)
                        if len(self._events) > _SESSION_EVENT_LIMIT:
                            del self._events[0]
                            self._events_dropped += 1
                    elif isinstance(message.get("id"), int):
                        self._responses[message["id"]] = message
                    self._condition.notify_all()
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def close(self) -> None:
        """Close the connection. Safe to call more than once."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._socket.close()

    @property
    def is_open(self) -> bool:
        """Whether the connection is still open."""
        with self._condition:
            return not self._closed

    @property
    def event_count(self) -> int:
        """How many events the session has received so far.

        Pass this value as ``after`` to `wait_for_event` to wait for events
        that arrive from now on.
        """
        with self._condition:
            return self._events_dropped + len(self._events)

    def events_since(self, after: int = 0) -> list[dict[str, Any]]:
        """Return the events received after the first `after` ones.

        Parameters
        ----------
        after : `int`, optional
            An earlier `event_count`.

        Returns
        -------
        events : `list` [`dict`]
            A copy, oldest first.
        """
        with self._condition:
            return list(self._events[max(0, after - self._events_dropped) :])

    def version(self) -> str | None:
        """Return the PHD2 version from the ``Version`` event sent on connect.

        Returns
        -------
        version : `str` or `None`
            For example ``"2.6.14"``, or `None` when no such event arrived.
        """
        for event in self.events_since(0):
            if event.get("Event") == "Version":
                return str(event.get("PHDVersion"))
        return None

    def call(
        self,
        method: str,
        params: object = None,
        timeout_seconds: float = _DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> Any:
        """Send one command and return its result.

        Parameters
        ----------
        method : `str`
            The PHD2 method name, for example ``"get_app_state"``.
        params : `object`, optional
            The ``params`` value (a list or a dict), or `None` for none.
        timeout_seconds : `float`, optional
            Longest to wait for the answer.

        Returns
        -------
        result : `object`
            The ``result`` value of the answer (PHD2 sends 0 for a command
            with nothing to report).

        Raises
        ------
        ExternalServiceError
            If PHD2 answers with an error, does not answer in time, or the
            connection is closed.
        """
        with self._send_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            request: dict[str, Any] = {"method": method, "id": request_id}
            if params is not None:
                request["params"] = params
            try:
                self._socket.sendall((json.dumps(request) + "\r\n").encode("utf-8"))
            except OSError as error:
                raise ExternalServiceError(f"PHD2 ({self.label}) did not take {method}: {error}") from error
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while request_id not in self._responses:
                if self._closed:
                    raise ExternalServiceError(f"PHD2 ({self.label}) closed the connection during {method}.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ExternalServiceError(
                        f"PHD2 ({self.label}) did not answer {method} in {timeout_seconds:.0f} s."
                    )
                self._condition.wait(remaining)
            response = self._responses.pop(request_id)
        if "error" in response:
            error_value = response["error"]
            message = error_value.get("message") if isinstance(error_value, dict) else error_value
            raise ExternalServiceError(f"PHD2 ({self.label}) refused {method}: {message}")
        return response.get("result")

    def wait_for_event(
        self, event_name: str, after: int, timeout_seconds: float, **match: object
    ) -> dict[str, Any] | None:
        """Wait for an event that arrives after the first `after` events.

        Parameters
        ----------
        event_name : `str`
            The ``Event`` value to wait for.
        after : `int`
            Only events at this index or later count (see `event_count`).
        timeout_seconds : `float`
            Longest to wait.
        **match : `object`
            Extra fields the event must have, with these values.

        Returns
        -------
        event : `dict` or `None`
            The event, or `None` on a timeout or when the connection closed.
        """
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while True:
                first = max(0, after - self._events_dropped)
                for event in self._events[first:]:
                    if event.get("Event") == event_name and all(
                        event.get(key) == value for key, value in match.items()
                    ):
                        return event
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._closed:
                    return None
                self._condition.wait(min(remaining, 1.0))

    # ------------------------------------------------------------------
    # Questions about PHD2's state
    # ------------------------------------------------------------------

    def get_app_state(self) -> str:
        """Ask for PHD2's state.

        Returns
        -------
        state : `str`
            ``Stopped``, ``Selected``, ``Calibrating``, ``Guiding``,
            ``LostLock``, ``Paused`` or ``Looping``.
        """
        return str(self.call("get_app_state"))

    def get_connected(self) -> bool:
        """Ask whether PHD2 has connected its equipment.

        Returns
        -------
        connected : `bool`
            `True` when the camera and the mount are connected.
        """
        return bool(self.call("get_connected"))

    def get_calibrated(self) -> bool:
        """Ask whether PHD2 holds a guiding calibration.

        Returns
        -------
        calibrated : `bool`
            `True` when a calibration exists.
        """
        return bool(self.call("get_calibrated"))

    def get_profile(self) -> dict[str, Any]:
        """Ask which equipment profile PHD2 runs.

        Returns
        -------
        profile : `dict`
            ``id`` and ``name`` of the profile.
        """
        return dict(self.call("get_profile"))

    def get_current_equipment(self) -> dict[str, Any]:
        """Ask which devices PHD2 uses.

        Returns
        -------
        equipment : `dict`
            One entry (``name`` and ``connected``) for each of ``camera``,
            ``mount``, ``aux_mount``, ``AO`` and ``rotator`` that is set.
        """
        return dict(self.call("get_current_equipment"))

    def get_exposure(self) -> int:
        """Ask for the guide exposure.

        Returns
        -------
        milliseconds : `int`
            The exposure time of one guide frame.
        """
        return int(self.call("get_exposure"))

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def set_exposure(self, milliseconds: int) -> None:
        """Set the guide exposure.

        Parameters
        ----------
        milliseconds : `int`
            The exposure time of one guide frame.
        """
        self.call("set_exposure", [int(milliseconds)])

    def set_connected(self, connected: bool = True, timeout_seconds: float = 120.0) -> None:
        """Connect or disconnect PHD2's equipment.

        Parameters
        ----------
        connected : `bool`, optional
            `True` (default) connects the profile's devices.
        timeout_seconds : `float`, optional
            Longest to wait; connecting can take a while.
        """
        self.call("set_connected", [bool(connected)], timeout_seconds)

    def loop(self) -> None:
        """Start the guide camera's exposures without guiding."""
        self.call("loop")

    def find_star(self, timeout_seconds: float = 60.0) -> list[float]:
        """Choose the guide star PHD2 finds automatically.

        Parameters
        ----------
        timeout_seconds : `float`, optional
            Longest to wait.

        Returns
        -------
        position : `list` [`float`]
            The star's lock position in pixels (x, y).
        """
        return [float(value) for value in self.call("find_star", timeout_seconds=timeout_seconds)]

    def guide(
        self,
        settle_pixels: float = 1.5,
        settle_seconds: float = 6.0,
        settle_timeout_seconds: float = 120.0,
        recalibrate: bool = False,
    ) -> None:
        """Start guiding: choose a star, calibrate when needed, then guide.

        PHD2 calibrates first when it holds no calibration, or when
        `recalibrate` is set. The command returns at once. Progress arrives as
        events, and the end of the settling as a ``SettleDone`` event: wait
        for it with `wait_for_event`.

        Parameters
        ----------
        settle_pixels : `float`, optional
            The star must stay within this many pixels of the lock position.
        settle_seconds : `float`, optional
            It must stay there this long to count as settled.
        settle_timeout_seconds : `float`, optional
            PHD2 gives up settling after this long.
        recalibrate : `bool`, optional
            `True` forces a new calibration.
        """
        settle = {"pixels": settle_pixels, "time": settle_seconds, "timeout": settle_timeout_seconds}
        self.call("guide", [settle, bool(recalibrate)])

    def dither(
        self,
        amount_pixels: float,
        right_ascension_only: bool = False,
        settle_pixels: float = 1.5,
        settle_seconds: float = 6.0,
        settle_timeout_seconds: float = 60.0,
    ) -> None:
        """Move the lock position at random and settle again.

        Parameters
        ----------
        amount_pixels : `float`
            The size of the dither.
        right_ascension_only : `bool`, optional
            `True` dithers along right ascension only.
        settle_pixels, settle_seconds : `float`, optional
            The settle limits, as for `guide`.
        settle_timeout_seconds : `float`, optional
            PHD2 gives up settling after this long.
        """
        settle = {"pixels": settle_pixels, "time": settle_seconds, "timeout": settle_timeout_seconds}
        self.call("dither", [amount_pixels, bool(right_ascension_only), settle])

    def stop_capture(self, timeout_seconds: float = 30.0) -> None:
        """Stop guiding and looping.

        Parameters
        ----------
        timeout_seconds : `float`, optional
            Longest to wait for the answer.
        """
        self.call("stop_capture", timeout_seconds=timeout_seconds)

    def shutdown(self) -> None:
        """Ask PHD2 to quit. The connection ends with it."""
        self.call("shutdown", timeout_seconds=10.0)
