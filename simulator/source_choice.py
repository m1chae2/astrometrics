"""Purpose: Keep the observatory source choice and shared launch names.

Description: The "Observatory source" setting has two values: the real
telescope, or a simulated sky. The choice is kept in a small file next to
the user's settings file (``observatory_source.json`` in the real settings
folder). The real settings file itself is never changed.

The choice file helpers and the names shared with the backend live in the
core (`astrometricslib.foundation.source_choice`, public through
`astrometricslib.drivers`), so there is one copy of the format. This module
re-exports them for the launcher and adds the names only the layer uses.

The environment variables below are how a world tells the backend where it
is. The launcher sets them when it runs the backend as its child.
"""

from astrometricslib.drivers import (
    ACTIVE_SOURCE_VARIABLE,
    CHOICE_FILE_NAME,
    LAUNCH_ERROR_VARIABLE,
    LAUNCHER_VARIABLE,
    OFFLINE,
    REAL,
    RESTART_EXIT_CODE,
    SIMULATED,
    SOURCES,
    SourceChoice,
    choice_file_path,
    read_choice,
    write_choice,
)

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

#: Variables a launch sets, so the running backend knows its world.
ACTIVE_ROOT_VARIABLE = "ASTROMETRICS_SIMULATOR_ROOT"
ACTIVE_PORTS_VARIABLE = "ASTROMETRICS_SIMULATOR_PORTS"
REAL_CONFIG_HOME_VARIABLE = "ASTROMETRICS_REAL_CONFIG_HOME"
REAL_DATA_HOME_VARIABLE = "ASTROMETRICS_REAL_DATA_HOME"
REAL_CONFIG_PATH_VARIABLE = "ASTROMETRICS_REAL_CONFIG_PATH"
