"""Purpose: Keep the observatory source choice a launcher shares with the app.

Description: The "Observatory source" setting has two values: the real
telescope, or a simulated sky. The choice is kept in a small file next to the
user's settings file (``observatory_source.json`` in the settings folder). The
settings file itself is never changed.

A launcher is a program that starts the app (see `LAUNCHER_VARIABLE`). It
reads the choice to decide which world to start the app in. The app reads and
writes the same file when the user changes the source, and tells the launcher
by ending with `RESTART_EXIT_CODE`. This module holds the file helpers and the
names both sides use, so neither has to import the other. There is one copy of
this format.

The app never builds a world from the choice. It only reports it and saves a
new one.
"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from astrometricslib.foundation.paths import real_folders

#: The two values of the setting.
REAL = "real"
SIMULATED = "simulated"
SOURCES = (REAL, SIMULATED)
#: The source in force when the simulator failed to start: no telescope at all.
#: It is never a choice, only a state.
OFFLINE = "offline"

#: Name of the choice file, inside the settings folder.
CHOICE_FILE_NAME = "observatory_source.json"

#: Set by a launcher that runs the app as its child. The app then changes its
#: source by ending with `RESTART_EXIT_CODE`, and the launcher starts it again.
LAUNCHER_VARIABLE = "ASTROMETRICS_LAUNCHER"
#: The source a launcher started the app with (``real``, ``simulated`` or
#: ``offline``). Missing means ``real``.
ACTIVE_SOURCE_VARIABLE = "ASTROMETRICS_OBSERVATORY_SOURCE"
#: Why the chosen source could not start (text), set with ``offline``.
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


@dataclass(frozen=True)
class SourceChoice:
    """The saved choice: ``source`` is ``real`` or ``simulated``."""

    source: str = REAL


def choice_file_path(environ: Mapping[str, str] | None = None) -> Path:
    """Give the path of the choice file.

    Parameters
    ----------
    environ : `~collections.abc.Mapping` [`str`, `str`], optional
        The environment. Default: the process environment.

    Returns
    -------
    path : `pathlib.Path`
        ``<settings folder>/astrometrics/observatory_source.json``.
    """
    return (
        real_folders(os.environ if environ is None else environ).config_home
        / "astrometrics"
        / CHOICE_FILE_NAME
    )


def read_choice(environ: Mapping[str, str] | None = None) -> SourceChoice:
    """Read the saved choice.

    A missing or damaged file means the real telescope.

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
    source = str(data.get("source", REAL)) if isinstance(data, dict) else REAL
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
