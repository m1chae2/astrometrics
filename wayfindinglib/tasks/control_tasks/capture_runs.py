"""Purpose: Remember the capture runs the app started, so they can stop.

Description: `control.imaging.capture_image` takes a run of exposures.
`CaptureRuns` is the list of runs going right now. A run adds itself when
it starts and removes itself when it ends. A stop request sets a flag on
every listed run. The run looks at its flag between frames and during the
wait for each exposure, so a stop does not wait for a long exposure to
finish.

Only runs started by this app are listed. A run started by another
program, such as Ekos, is not listed here and is never stopped from here.
"""

from __future__ import annotations

import threading

__all__ = ["CaptureRuns"]


class CaptureRuns:
    """The capture runs this app has going, each with a stop flag."""

    def __init__(self) -> None:
        """Start with no runs going."""
        self._lock = threading.Lock()
        self._flags: list[threading.Event] = []

    def begin(self) -> threading.Event:
        """List a new run.

        Returns
        -------
        stop_flag : `threading.Event`
            The run's own flag. It is set when a stop is requested.
        """
        stop_flag = threading.Event()
        with self._lock:
            self._flags.append(stop_flag)
        return stop_flag

    def end(self, stop_flag: threading.Event) -> None:
        """Remove a run that has ended.

        Parameters
        ----------
        stop_flag : `threading.Event`
            The flag `begin` returned for the run.
        """
        with self._lock:
            if stop_flag in self._flags:
                self._flags.remove(stop_flag)

    def is_running(self) -> bool:
        """Say whether any run is going.

        Returns
        -------
        running : `bool`
            `True` if at least one run is listed.
        """
        with self._lock:
            return bool(self._flags)

    def request_stop(self) -> bool:
        """Ask every listed run to stop.

        Returns
        -------
        requested : `bool`
            `True` if at least one run was listed, `False` if none was
            going (nothing is changed then).
        """
        with self._lock:
            for stop_flag in self._flags:
                stop_flag.set()
            return bool(self._flags)
