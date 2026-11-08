"""Purpose: Report and change the "Observatory source" while the backend is up.

Description: The source is the real telescope or a simulated sky. A launcher
(see `backend.observatory_source`) starts the backend in the world the saved
choice names; the backend builds, starts and removes nothing for it. This
service

* answers what the source is, for the settings control and the status header:
  the launch identity (the ``[Launch]`` section of the settings the backend
  started with), the source the launcher started it with and the saved choice;
* saves a new choice and ends the backend with exit code 75 so the launcher
  starts it again on the other source; and
* refuses that change when no launcher runs the backend (nobody would start it
  again), or when work is running that the restart would cut off.
"""

import logging
import os
from collections.abc import Callable, Mapping
from typing import Any

from astrometricslib import get_configuration
from astrometricslib.drivers import LaunchIdentity
from astrometricslib.exceptions import ConfigurationError, InvalidArgumentError
from backend import observatory_source
from backend.launch_contract import (
    ACTIVE_SOURCE_VARIABLE,
    LAUNCH_ERROR_VARIABLE,
    LAUNCH_NOTES_VARIABLE,
    MISSING_PROGRAMS_VARIABLE,
    NO_TELESCOPE,
    SIMULATED,
    SOURCES,
    SourceChoice,
    normalize_source,
    read_choice,
    write_choice,
)

logger = logging.getLogger(__name__)

#: Why the source cannot change when no launcher runs the backend.
NOT_LAUNCHED_REASON = (
    "This backend was not started by the launcher, so the observatory source cannot change here."
)


class ObservatorySourceService:
    """Report the launch and the source setting, and save a new choice.

    Parameters
    ----------
    environ : `~collections.abc.Mapping` [`str`, `str`], optional
        The environment. Default: the process environment.
    restarter : `~collections.abc.Callable`, optional
        Ends the backend for the launcher. Default: `restart_backend`. It is
        called with the function that stops the backend's services first.
    launch_reader : `~collections.abc.Callable`, optional
        Gives the launch identity. Default: read it from the settings in use.
    """

    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
        restarter: Callable[[Callable[[], None] | None], object] = observatory_source.restart_backend,
        launch_reader: Callable[[], LaunchIdentity | None] | None = None,
    ) -> None:
        """Keep the pieces; nothing starts."""
        self._environ = os.environ if environ is None else environ
        self._restarter = restarter
        self._launch_reader = launch_reader or (lambda: get_configuration().get_launch_identity())
        self._busy_probe: Callable[[], list[str]] = list
        self._shutdown_hook: Callable[[], None] | None = None

    def set_busy_probe(self, probe: Callable[[], list[str]]) -> None:
        """Say how to find out what the backend is busy with.

        Parameters
        ----------
        probe : `~collections.abc.Callable`
            Returns a short text for each running job, stack or download;
            an empty list when the backend is idle.
        """
        self._busy_probe = probe

    def set_shutdown_hook(self, hook: Callable[[], None]) -> None:
        """Say what stops all of the backend's services before it ends.

        Parameters
        ----------
        hook : `~collections.abc.Callable`
            Stops the threads and processes the backend started (the
            container's `shutdown_resources`).
        """
        self._shutdown_hook = hook

    @property
    def active_source(self) -> str:
        """The launcher's source: ``real``, ``simulated``, ``no_telescope``."""
        return normalize_source(self._environ.get(ACTIVE_SOURCE_VARIABLE))

    def _missing_programs(self) -> list[str]:
        """Give the programs the launcher found missing.

        Returns
        -------
        missing : `list` [`str`]
            Program names; empty when the launcher reported none.
        """
        return [name for name in self._environ.get(MISSING_PROGRAMS_VARIABLE, "").split(",") if name]

    def _launch_notes(self) -> list[str]:
        """Give the notes the launcher passed about what the launch lacks.

        Returns
        -------
        notes : `list` [`str`]
            Status texts (for example a missing optional program). They never
            stop a source change.
        """
        return [line for line in self._environ.get(LAUNCH_NOTES_VARIABLE, "").splitlines() if line.strip()]

    def _unavailable_reason(self) -> str | None:
        """Say why the source cannot change here, if it cannot.

        Returns
        -------
        reason : `str` or `None`
            `None` when a launcher runs the backend and the simulator's
            programs are present.
        """
        if not observatory_source.launched_by_launcher(self._environ):
            return NOT_LAUNCHED_REASON
        missing = self._missing_programs()
        if missing:
            return f"The simulator needs these programs: {', '.join(missing)}."
        return None

    def get_source(self) -> dict[str, Any]:
        """Describe the observatory source for the settings and the header.

        Returns
        -------
        description : `dict`
            ``source`` (the one the launcher started the backend with, or
            ``real``), ``chosen`` (saved), ``label`` (the launch label
            for the chip, empty without a launch), ``restart_pending``
            (the saved choice differs from ``source``), ``starting`` (always
            `False`: the launcher finishes its start before the backend
            runs) and ``ready`` (always `True`), ``step`` (empty),
            ``running`` (`False`), ``error`` and ``reason`` (why the chosen
            simulator could not start, from the launcher; empty otherwise),
            ``failed`` (`True` when it could not start and the backend runs
            with no telescope), ``missing_programs`` (the simulator's
            programs the launcher did not find), ``notes`` (the launcher's
            status notes, for example an optional program that is missing;
            they never stop a source change), ``available`` and
            ``unavailable_reason`` (whether the source can change from
            here, and why not),
            ``library_copy`` (empty), ``commands`` (`None`) and ``launch``.
            ``launch`` is the launch identity from the ``[Launch]`` section
            of the settings in use: ``label`` (`str`), ``live_hardware``,
            ``disposable_library`` and ``offline`` (`bool`), and
            ``locked_keys`` (`list` of ``Section.key`` `str`). It is `None`
            when the settings have no ``[Launch]`` section.
        """
        choice = read_choice(self._environ)
        active = self.active_source
        failed = active == NO_TELESCOPE
        launch = self._launch_description()
        error = self._environ.get(LAUNCH_ERROR_VARIABLE) or None
        reason = self._unavailable_reason()
        return {
            "source": active,
            "chosen": choice.source,
            "label": str(launch["label"]) if launch else "",
            "restart_pending": choice.source != active and not failed,
            "starting": False,
            "step": "",
            "ready": True,
            "running": False,
            "error": error,
            "missing_programs": self._missing_programs(),
            "notes": self._launch_notes(),
            "available": reason is None,
            "unavailable_reason": reason or "",
            "library_copy": {},
            "failed": failed,
            "reason": error or "",
            "commands": None,
            "launch": launch,
        }

    def _launch_description(self) -> dict[str, Any] | None:
        """Describe the launch identity for ``get_source``.

        Returns
        -------
        launch : `dict` or `None`
            `LaunchIdentity.as_dict`, or `None` without a ``[Launch]`` section.
        """
        identity = self._launch_reader()
        return None if identity is None else identity.as_dict()

    def set_source(self, source: str) -> dict[str, Any]:
        """Save a new source and end the backend for the launcher.

        Parameters
        ----------
        source : `str`
            ``real`` or ``simulated``.

        Returns
        -------
        result : `dict`
            ``restarting`` (`bool`) and ``source``.

        Raises
        ------
        InvalidArgumentError
            If the source is unknown (the app has no replay source).
        ConfigurationError
            If no launcher runs the backend, the simulator's programs are
            missing, or a job, a stack or a download is running (the restart
            would cut it off).
        """
        if source not in SOURCES:
            raise InvalidArgumentError(f"The observatory source must be one of {SOURCES}.")
        if not observatory_source.launched_by_launcher(self._environ):
            raise ConfigurationError(NOT_LAUNCHED_REASON)
        busy = self._busy_probe()
        if busy:
            raise ConfigurationError(
                "Wait until this finishes before changing the observatory source: " + "; ".join(busy) + "."
            )
        if source == SIMULATED:
            missing = self._missing_programs()
            if missing:
                raise ConfigurationError(f"The simulator needs these programs: {', '.join(missing)}.")
        restarting = source != self.active_source
        if source != read_choice(self._environ).source:
            write_choice(SourceChoice(source), self._environ)
        if restarting:
            self._restarter(self._shutdown_hook)
        return {"restarting": restarting, "source": source}
