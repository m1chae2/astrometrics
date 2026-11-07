"""Purpose: Run the "Observatory source" setting while the backend is up.

Description: The source is the real telescope or a simulated sky
(see `backend.observatory_source`). This service

* starts the simulator's stand-in processes when the backend was launched
  with a simulator source, and stops them at shutdown;
* answers what the source is, for the settings control and the status header;
* saves a new choice and starts the backend again so the next start uses the
  other world; and
* refuses a settings save that would aim a simulator at a real device; and
* reports the launch identity (the ``[Launch]`` section of the settings the
  backend started with) as ``launch``. The backend only reports it. It never
  builds or removes anything because of it.

It never starts a simulator process when the backend uses the real telescope.
"""

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from astrometricslib import Target, get_configuration
from astrometricslib.drivers import LaunchIdentity
from astrometricslib.exceptions import ConfigurationError, InvalidArgumentError
from backend import observatory_source
from simulator import (
    GuardError,
    SimulatedObservatory,
    check_offline_settings_update,
    check_settings_update,
    missing_programs,
    read_library_copy_report,
    unavailable_reason,
)

logger = logging.getLogger(__name__)


def _sexagesimal(value: float, is_right_ascension: bool) -> str:
    """Write a coordinate the way the app's targets hold it.

    Parameters
    ----------
    value : `float`
        Hours for a right ascension, degrees for a declination.
    is_right_ascension : `bool`
        Which of the two it is.

    Returns
    -------
    text : `str`
        ``HH:MM:SS`` or ``+DD:MM:SS``.
    """
    sign = "-" if value < 0 else ("" if is_right_ascension else "+")
    total = round(abs(value) * 3600)
    return f"{sign}{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def library_copy_report(environ: dict[str, str]) -> dict[str, Any]:
    """Read the report of the simulator's library copy.

    Parameters
    ----------
    environ : `dict` [`str`, `str`]
        The environment, which names the simulator's folder.

    Returns
    -------
    report : `dict`
        ``seconds`` the copy took, ``bytes`` by database and the real folders
        used; empty when the real library is in use.
    """
    root = environ.get(observatory_source.ACTIVE_ROOT_VARIABLE)
    return read_library_copy_report(Path(root)) if root else {}


class ObservatorySourceService:
    """Own the simulator processes and the source setting.

    Parameters
    ----------
    observatory : `SimulatedObservatory`, optional
        The simulator this backend was launched with. `None` for the real
        telescope.
    environ : `dict` [`str`, `str`], optional
        The environment. Default: the process environment.
    restarter : `~collections.abc.Callable`, optional
        Starts the backend again. Default: `restart_backend`. It is called
        with the function that stops the backend's services first.
    launch_reader : `~collections.abc.Callable`, optional
        Gives the launch identity. Default: read it from the settings in use.
    """

    def __init__(
        self,
        observatory: SimulatedObservatory | None = None,
        environ: dict[str, str] | None = None,
        restarter: Callable[[Callable[[], None] | None], object] = observatory_source.restart_backend,
        launch_reader: Callable[[], LaunchIdentity | None] | None = None,
    ) -> None:
        """Keep the pieces; nothing starts yet."""
        self._observatory = observatory
        self._environ = os.environ if environ is None else environ
        self._restarter = restarter
        self._launch_reader = launch_reader or (lambda: get_configuration().get_launch_identity())
        self._start_error: str | None = None
        self._starting = False
        self._step = ""
        self._lock = threading.Lock()
        self._busy_probe: Callable[[], list[str]] = list
        self._shutdown_hook: Callable[[], None] | None = None
        self._ready_hook: Callable[[], object] | None = None
        self._commands: dict[str, Any] | None = None

    def set_ready_hook(self, hook: Callable[[], object]) -> None:
        """Say what to do once the simulator is up.

        The hook runs only for a simulator that started (never for the real
        telescope or the offline world, which have no simulator object). The
        container uses it to let the app command the simulated devices.

        Parameters
        ----------
        hook : `~collections.abc.Callable`
            Called with no arguments. A failure is logged, and the simulator
            still counts as ready.
        """
        self._ready_hook = hook

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
        """Say what stops all of the backend's services before a restart.

        Parameters
        ----------
        hook : `~collections.abc.Callable`
            Stops the threads and processes the backend started (the
            container's `shutdown_resources`). It also stops the simulator.
        """
        self._shutdown_hook = hook

    @property
    def active_source(self) -> str:
        """The source in force: ``real``, ``simulated`` or ``offline``."""
        return self._environ.get(observatory_source.ACTIVE_SOURCE_VARIABLE, observatory_source.REAL)

    def start(self, astrometrics: Any = None) -> None:
        """Start the simulator in the background (real: nothing to do).

        Parameters
        ----------
        astrometrics : `Astrometrics`, optional
            The image library of this world. The simulator adds the targets
            its telescope points at, because a frame is downloaded only
            when the mount points at a target the library knows.
        """
        if self._observatory is None:
            return
        self._starting = True
        self._step = "Starting telescope"
        threading.Thread(
            target=self._start_now, args=(astrometrics,), name="simulator-start", daemon=True
        ).start()

    def _start_now(self, astrometrics: Any) -> None:
        """Start the stand-ins and wait until the whole telescope is up.

        The steps are ``Starting telescope`` (INDI server, PHD2 and the
        driver, until the simulators are connected), ``Seeding targets`` and
        ``Ready``. A failure, or a telescope that is not up in time, is
        recorded as the reason and logged at error level once.

        Parameters
        ----------
        astrometrics : `Astrometrics` or `None`
            Where to add the telescope's targets.
        """
        try:
            self._observatory.start()
            self._observatory.wait_ready()
            self._step = "Seeding targets"
            if astrometrics is not None:
                self._seed_targets(astrometrics)
            self._run_ready_hook()
            self._step = "Ready"
        except Exception as error:  # the backend must keep running without the simulator
            logger.exception("SIMULATOR FAILED: the simulated observatory did not start")
            self._start_error = f"{type(error).__name__}: {error}"
            self._step = "Failed"
        finally:
            self._starting = False

    def _run_ready_hook(self) -> None:
        """Run the ready hook and record which commands it turned on.

        The hook returns the library's outcome (``applied`` and
        ``rejected``). Each rejected capability is logged once with its
        reason, and the lists go to `get_source` as ``commands``. A failure
        is logged and recorded as ``error``; the simulator stays ready.
        """
        if self._ready_hook is None:
            return
        try:
            outcome = self._ready_hook()
        except Exception as error:  # commands stay off; the night can still be watched
            logger.warning("The simulator is up, but the app could not take command of it.", exc_info=True)
            self._commands = {"allowed": [], "off": {}, "error": f"{type(error).__name__}: {error}"}
            return
        applied = getattr(outcome, "applied", None)
        rejected = getattr(outcome, "rejected", None)
        if applied is None or rejected is None:
            return
        allowed = sorted(getattr(capability, "value", str(capability)) for capability in applied)
        off = {
            getattr(capability, "value", str(capability)): str(reason)
            for capability, reason in rejected.items()
        }
        self._commands = {"allowed": allowed, "off": off, "error": None}
        logger.info("Simulator commands are on for: %s.", ", ".join(allowed) or "nothing")
        if off:
            logger.warning(
                "Simulator commands stay off for: %s.",
                "; ".join(f"{name} ({reason})" for name, reason in off.items()),
            )

    def is_settled(self) -> bool:
        """Say whether the observatory source has finished starting.

        Returns
        -------
        settled : `bool`
            `True` for the real telescope and for the offline world, and for
            a simulator once it is ready or has failed. `/api/ready` waits
            for it, so the app never opens on a half-built simulated world.
        """
        return not self._starting

    def _seed_targets(self, astrometrics: Any) -> None:
        """Add the targets the simulated telescope points at to the library.

        Parameters
        ----------
        astrometrics : `Astrometrics`
            The simulator world's image library. Existing targets stay as
            they are.
        """
        targets = self._observatory.pointed_targets()
        for name, right_ascension, declination in targets:
            if astrometrics.targets.get(name) is None:
                astrometrics.targets.add(
                    Target(
                        id=name, ra=_sexagesimal(right_ascension, True), dec=_sexagesimal(declination, False)
                    )
                )
        if targets:
            astrometrics.targets.save()

    def stop(self) -> None:
        """End the simulator's processes. Safe to call more than once."""
        if self._observatory is not None:
            self._observatory.stop()

    def remove_library_copy(self) -> None:
        """Unmount the library overlays and delete the simulator's copies.

        Call it last at shutdown, after every service that uses the library
        has stopped. Safe to call more than once.
        """
        if self._observatory is not None:
            self._observatory.remove_library_copy()

    def get_source(self) -> dict[str, Any]:
        """Describe the observatory source for the settings and the header.

        Returns
        -------
        description : `dict`
            ``source`` (in force), ``chosen`` (saved),
            ``label`` (the status chip text, empty for the real telescope),
            ``commands`` (for a simulator: ``allowed`` capabilities, ``off``
            capabilities with the reason each stays off, and ``error``; `None`
            until the simulator is up or for the real telescope),
            ``restart_pending``, ``starting`` (the simulator is still
            coming up), ``step`` (``Starting telescope``, ``Seeding targets``,
            ``Ready``, ``Failed`` or an empty text), ``ready`` (`True` when
            the app can be used), ``running``, ``error``,
            ``missing_programs``, ``available`` and ``unavailable_reason``
            (why the simulator cannot run here, for example in the packaged
            app), and ``failed`` and ``reason``. ``failed`` is `True` when
            the chosen simulator could not start and the app runs offline,
            with no telescope; ``reason`` says why. The label is then
            ``SIMULATOR FAILED``. ``launch`` is the launch identity from the
            ``[Launch]`` section of the settings in use: ``label`` (`str`),
            ``live_hardware``, ``disposable_library`` and ``offline``
            (`bool`), and ``locked_keys`` (`list` of ``Section.key`` `str`).
            It is `None` when the settings have no ``[Launch]`` section.
        """
        choice = observatory_source.read_choice(self._environ)
        active = self.active_source
        failed = active == observatory_source.OFFLINE
        if failed:
            label = "SIMULATOR FAILED"
        elif active == observatory_source.SIMULATED:
            label = "SIMULATOR"
        else:
            label = ""
        error = self._start_error or self._environ.get(observatory_source.LAUNCH_ERROR_VARIABLE) or None
        reason = unavailable_reason()
        return {
            "source": active,
            "chosen": choice.source,
            "label": label,
            "restart_pending": choice.source != active and not failed,
            "starting": self._starting,
            "step": self._step,
            "ready": not self._starting,
            "running": self._observatory.is_running() if self._observatory is not None else False,
            "error": error,
            "missing_programs": missing_programs(),
            "available": reason is None,
            "unavailable_reason": reason or "",
            "library_copy": library_copy_report(self._environ),
            "failed": failed or bool(self._start_error),
            "reason": error or "",
            "commands": self._commands,
            "launch": self._launch_description(),
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
        """Save a new source and start the backend again.

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
            If this run's world is fixed (a test run), the simulator's
            programs are not installed or it is the packaged app, or a job,
            a stack or a download is running (the restart would cut it off).
        """
        if source not in observatory_source.SOURCES:
            raise InvalidArgumentError(f"The observatory source must be one of {observatory_source.SOURCES}.")
        if observatory_source.world_is_pinned(
            self._environ
        ) and observatory_source.ACTIVE_SOURCE_VARIABLE not in (self._environ):
            raise ConfigurationError("This run fixes its own observatory, so the source cannot change.")
        busy = self._busy_probe()
        if busy:
            raise ConfigurationError(
                "Wait until this finishes before changing the observatory source: " + "; ".join(busy) + "."
            )
        if source != observatory_source.REAL:
            reason = unavailable_reason()
            if reason:
                raise ConfigurationError(reason)
        choice = observatory_source.SourceChoice(source)
        current = observatory_source.read_choice(self._environ)
        restarting = source != self.active_source
        if choice != current:
            observatory_source.write_choice(choice, self._environ)
        if restarting:
            self._restarter(self._shutdown_hook or self.stop)
        return {"restarting": restarting, "source": source}

    def check_settings_update(self, update: dict[str, Any]) -> None:
        """Refuse a settings save that aims the simulator at a real device.

        Does nothing for the real telescope.

        Parameters
        ----------
        update : `dict`
            The settings being saved, one dict per section.

        Raises
        ------
        ConfigurationError
            If a simulator source is active and the update names a real host,
            another port, or a folder outside the stand-in telescope computer.
        """
        if self.active_source == observatory_source.OFFLINE:
            root = Path(self._environ.get(observatory_source.ACTIVE_ROOT_VARIABLE, ""))
            try:
                check_offline_settings_update(update, root)
            except (GuardError, ValueError, TypeError, KeyError, OSError) as error:
                logger.warning("Refused a settings save in offline mode: %s", error)
                raise ConfigurationError(f"Refused while the simulator is offline: {error}") from error
            return
        if self._observatory is None:
            return
        try:
            check_settings_update(
                update,
                self._observatory.indi_port,
                self._observatory.phd2_port,
                self._observatory.standin_home,
                self._observatory.root,
            )
        except GuardError as error:
            logger.warning("Refused a settings save in %s mode: %s", self.active_source, error)
            raise ConfigurationError(f"Refused in simulator mode: {error}") from error
        except (ValueError, TypeError, KeyError, OSError) as error:
            logger.warning("Could not check a settings save in simulator mode: %s", error)
            raise ConfigurationError(
                f"Refused in simulator mode: the settings could not be checked ({error})"
            ) from error
