"""Purpose: Give the simulator the launcher contract and its own launch names.

Description: The "Observatory source" setting has two values: the real
telescope, or a simulated sky. The choice is kept in a small file next to
the user's settings file (``observatory_source.json`` in the real settings
folder). The real settings file itself is never changed.

The backend owns the contract: the choice file helpers and the names shared
with the backend are defined in `backend.launch_contract` and public through
`backend.public_interface`, the one backend module a launcher may import. This
module re-exports them for the rest of the layer and adds the two names only
the layer uses.

The environment variables below are how a world tells the backend where it
is. The launcher sets them when it runs the backend as its child.
"""

from backend.public_interface import (
    ACTIVE_SOURCE_VARIABLE,
    CHOICE_FILE_NAME,
    LAUNCH_ERROR_VARIABLE,
    LAUNCH_NOTES_VARIABLE,
    LAUNCHER_VARIABLE,
    MISSING_PROGRAMS_VARIABLE,
    NO_TELESCOPE,
    REAL,
    REAL_CONFIG_HOME_VARIABLE,
    REAL_CONFIG_PATH_VARIABLE,
    REAL_DATA_HOME_VARIABLE,
    REAL_STATE_HOME_VARIABLE,
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
    "read_choice",
    "write_choice",
]

#: Variables a launch sets, so the running backend knows its world.
ACTIVE_ROOT_VARIABLE = "ASTROMETRICS_SIMULATOR_ROOT"
ACTIVE_PORTS_VARIABLE = "ASTROMETRICS_SIMULATOR_PORTS"
