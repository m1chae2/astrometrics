"""Purpose: Hold the names and choice file a launcher shares with the backend.

Description: The "Observatory source" setting has two values: the real
telescope, or a simulated sky. A launcher is a program that starts the backend
as its child (see `LAUNCHER_VARIABLE`); ``python -m simulator.launch`` is one.
It reads the saved choice to decide which world to start the backend in. The
backend reads and writes the same choice when the user changes the source,
and tells the launcher by ending with `RESTART_EXIT_CODE`.

The backend owns this contract. A launcher reaches it only through
`backend.public_interface`, which re-exports everything below, so neither side
imports the other's code. This module imports nothing but the standard
library, so a launcher can read it without starting the app.

The choice is kept in a small file next to the user's settings file
(``observatory_source.json`` in the settings folder). The settings file
itself is never changed. The backend never builds a world from the choice. It
only reports it and saves a new one.
"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ACTIVE_SOURCE_VARIABLE",
    "CHOICE_FILE_NAME",
    "LAUNCHER_VARIABLE",
    "LAUNCH_ERROR_VARIABLE",
    "LAUNCH_NOTES_VARIABLE",
    "MISSING_PROGRAMS_VARIABLE",
    "NO_TELESCOPE",
    "REAL",
    "REAL_CONFIG_HOME_VARIABLE",
    "REAL_CONFIG_PATH_VARIABLE",
    "REAL_DATA_HOME_VARIABLE",
    "REAL_STATE_HOME_VARIABLE",
    "RESTART_EXIT_CODE",
    "SIMULATED",
    "SOURCES",
    "SourceChoice",
    "choice_file_path",
    "normalize_source",
    "read_choice",
    "write_choice",
]

#: The two values of the setting.
REAL = "real"
SIMULATED = "simulated"
SOURCES = (REAL, SIMULATED)
#: The source in force when the simulator failed to start: no telescope at all.
#: It is never a choice the user makes, only a state. Older versions called it
#: ``offline``; `normalize_source` still reads that spelling. The new name
#: keeps it apart from a launch that is offline (nothing leaves this
#: computer), which is a different fact.
NO_TELESCOPE = "no_telescope"
#: The old spelling of `NO_TELESCOPE`, still accepted when reading.
_OLD_NO_TELESCOPE = "offline"

#: Name of the choice file, inside the settings folder.
CHOICE_FILE_NAME = "observatory_source.json"

#: Set by a launcher that runs the backend as its child. The backend then
#: changes its source by ending with `RESTART_EXIT_CODE`, and the launcher
#: starts it again.
LAUNCHER_VARIABLE = "ASTROMETRICS_LAUNCHER"
#: The source a launcher started the backend with (``real``, ``simulated`` or
#: ``no_telescope``). Missing means ``real``.
ACTIVE_SOURCE_VARIABLE = "ASTROMETRICS_OBSERVATORY_SOURCE"
#: Why the chosen source could not start (text), set with ``no_telescope``.
LAUNCH_ERROR_VARIABLE = "ASTROMETRICS_OBSERVATORY_SOURCE_ERROR"
#: Comma-separated programs the launcher found missing for the simulated
#: source. Empty or missing means none.
MISSING_PROGRAMS_VARIABLE = "ASTROMETRICS_LAUNCH_MISSING_PROGRAMS"
#: Newline-separated notes from the launcher about what the launch lacks and
#: can do without (for example a missing optional program). They are
#: statuses to show, not reasons to refuse a source. Empty or missing means
#: none.
LAUNCH_NOTES_VARIABLE = "ASTROMETRICS_LAUNCH_NOTES"
#: The exit code that means "start me again with the saved choice" (75 is
#: ``EX_TEMPFAIL`` in ``sysexits.h``). A launcher relaunches on this code only.
RESTART_EXIT_CODE = 75

#: Names of the variables in which a launcher keeps the user's real folders,
#: after it has moved the ``XDG_*`` variables to a world of its own. The
#: backend reads only the first, to find the choice file; the launcher side
#: (`simulator.real_folders`) reads all four.
REAL_CONFIG_HOME_VARIABLE = "ASTROMETRICS_REAL_CONFIG_HOME"
REAL_DATA_HOME_VARIABLE = "ASTROMETRICS_REAL_DATA_HOME"
REAL_CONFIG_PATH_VARIABLE = "ASTROMETRICS_REAL_CONFIG_PATH"
REAL_STATE_HOME_VARIABLE = "ASTROMETRICS_REAL_STATE_HOME"


@dataclass(frozen=True)
class SourceChoice:
    """The saved choice: ``source`` is ``real`` or ``simulated``."""

    source: str = REAL


def normalize_source(text: str | None) -> str:
    """Give the current spelling of a source name.

    Parameters
    ----------
    text : `str` or `None`
        A source name from the choice file or the launcher's environment. The
        old spelling ``offline`` is read as `NO_TELESCOPE`.

    Returns
    -------
    source : `str`
        The name with the old spelling replaced. Other text is returned
        unchanged, and `None` or empty text gives `REAL`.
    """
    if not text:
        return REAL
    return NO_TELESCOPE if text == _OLD_NO_TELESCOPE else text


def choice_file_path(environ: Mapping[str, str] | None = None) -> Path:
    """Give the path of the choice file.

    Parameters
    ----------
    environ : `~collections.abc.Mapping` [`str`, `str`], optional
        The environment. Default: the process environment.

    Returns
    -------
    path : `pathlib.Path`
        ``<settings folder>/astrometrics/observatory_source.json``. The
        settings folder is the real one: ``ASTROMETRICS_REAL_CONFIG_HOME``
        when a launcher moved the ``XDG_*`` folders, else ``XDG_CONFIG_HOME``,
        else ``~/.config``.
    """
    environment = os.environ if environ is None else environ
    config_home = Path(
        environment.get(REAL_CONFIG_HOME_VARIABLE)
        or environment.get("XDG_CONFIG_HOME")
        or Path.home() / ".config"
    )
    return config_home / "astrometrics" / CHOICE_FILE_NAME


def read_choice(environ: Mapping[str, str] | None = None) -> SourceChoice:
    """Read the saved choice.

    A missing or damaged file means the real telescope, and so does any value
    that is not a choice the user can make (`NO_TELESCOPE` is a state, never a
    choice).

    Parameters
    ----------
    environ : `~collections.abc.Mapping` [`str`, `str`], optional
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
    source = normalize_source(str(data.get("source", REAL))) if isinstance(data, dict) else REAL
    if source not in SOURCES:
        return SourceChoice()
    return SourceChoice(source)


def write_choice(choice: SourceChoice, environ: Mapping[str, str] | None = None) -> None:
    """Save the choice.

    Parameters
    ----------
    choice : `SourceChoice`
        The choice to save.
    environ : `~collections.abc.Mapping` [`str`, `str`], optional
        The environment. Default: the process environment.
    """
    path = choice_file_path(environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"source": choice.source}, indent=2), encoding="utf-8")
    temporary.replace(path)
