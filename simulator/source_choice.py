"""Purpose: Keep the observatory source choice and shared launch names.

Description: The "Observatory source" setting has two values: the real
telescope, or a simulated sky. The choice is kept in a small file next to
the user's settings file (``observatory_source.json`` in the real settings
folder). The real settings file itself is never changed.

The launcher (`simulator.launch`) reads the choice to decide which world to
build before it starts the backend. The backend reads and writes the same
file when the user changes the source, and tells the launcher by ending with
`RESTART_EXIT_CODE`. This module holds the file helpers and the names both
sides use, so neither has to import the other.

The environment variables below are how a world tells the backend where it
is. The launcher sets them when it runs the backend as its child; they are
the same ones the backend's own start-up path sets when it builds a world
itself (see `backend.observatory_source`).
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from astrometricslib.drivers import real_folders

__all__ = [
    "ACTIVE_PORTS_VARIABLE",
    "ACTIVE_ROOT_VARIABLE",
    "ACTIVE_SOURCE_VARIABLE",
    "CHOICE_FILE_NAME",
    "LAUNCHER_VARIABLE",
    "LAUNCH_ERROR_VARIABLE",
    "OFFLINE",
    "REAL",
    "REAL_CONFIG_HOME_VARIABLE",
    "REAL_CONFIG_PATH_VARIABLE",
    "REAL_DATA_HOME_VARIABLE",
    "RESTART_EXIT_CODE",
    "SIMULATED",
    "SOURCES",
    "SourceChoice",
    "choice_file_path",
    "read_choice",
    "write_choice",
]

#: The two values of the setting.
REAL = "real"
SIMULATED = "simulated"
SOURCES = (REAL, SIMULATED)
#: The source in force when the simulator failed to start: no telescope at all.
#: It is never a choice, only a state.
OFFLINE = "offline"

#: Name of the choice file, inside the real settings folder.
CHOICE_FILE_NAME = "observatory_source.json"

#: Variables a launch sets, so the running backend knows its world.
ACTIVE_SOURCE_VARIABLE = "ASTROMETRICS_OBSERVATORY_SOURCE"
ACTIVE_ROOT_VARIABLE = "ASTROMETRICS_SIMULATOR_ROOT"
ACTIVE_PORTS_VARIABLE = "ASTROMETRICS_SIMULATOR_PORTS"
LAUNCH_ERROR_VARIABLE = "ASTROMETRICS_OBSERVATORY_SOURCE_ERROR"
REAL_CONFIG_HOME_VARIABLE = "ASTROMETRICS_REAL_CONFIG_HOME"
REAL_DATA_HOME_VARIABLE = "ASTROMETRICS_REAL_DATA_HOME"
REAL_CONFIG_PATH_VARIABLE = "ASTROMETRICS_REAL_CONFIG_PATH"

#: Set by a launcher that runs the backend as its child. The backend then
#: builds and starts nothing itself, and ends with `RESTART_EXIT_CODE`
#: instead of starting itself again.
LAUNCHER_VARIABLE = "ASTROMETRICS_LAUNCHER"
#: The exit code that means "start me again with the saved choice" (75 is
#: ``EX_TEMPFAIL`` in ``sysexits.h``). A launcher relaunches on this code only.
RESTART_EXIT_CODE = 75


@dataclass(frozen=True)
class SourceChoice:
    """The saved choice.

    Attributes
    ----------
    source : `str`
        ``real`` or ``simulated``.
    """

    source: str = REAL


def choice_file_path(environ: dict[str, str] | None = None) -> Path:
    """Give the path of the choice file.

    Parameters
    ----------
    environ : `dict` [`str`, `str`], optional
        The environment. Default: the process environment.

    Returns
    -------
    path : `pathlib.Path`
        ``<real settings folder>/astrometrics/observatory_source.json``.
    """
    return (
        real_folders(os.environ if environ is None else environ).config_home
        / "astrometrics"
        / CHOICE_FILE_NAME
    )


def read_choice(environ: dict[str, str] | None = None) -> SourceChoice:
    """Read the saved choice.

    A missing or damaged file means the real telescope.

    Parameters
    ----------
    environ : `dict` [`str`, `str`], optional
        The environment. Default: the process environment.

    Returns
    -------
    choice : `SourceChoice`
        The saved choice.
    """
    try:
        data = json.loads(choice_file_path(environ).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return SourceChoice()
    source = str(data.get("source", REAL)) if isinstance(data, dict) else REAL
    if source not in SOURCES:
        return SourceChoice()
    return SourceChoice(source)


def write_choice(choice: SourceChoice, environ: dict[str, str] | None = None) -> None:
    """Save the choice.

    Parameters
    ----------
    choice : `SourceChoice`
        The choice to save.
    environ : `dict` [`str`, `str`], optional
        The environment. Default: the process environment.
    """
    path = choice_file_path(environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"source": choice.source}, indent=2), encoding="utf-8")
    temporary.replace(path)
