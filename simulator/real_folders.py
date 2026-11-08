"""Purpose: Find the user's real folders, which a simulator must never touch.

Description: A launcher moves the ``XDG_*`` variables and the settings path to
a world of its own and keeps the real ones in ``ASTROMETRICS_REAL_*``
variables (the names are part of the launcher contract,
`backend.public_interface`). `real_folders` reads those first, so it names the
real folders both before and after the move. `RealFolders.protected_paths`
lists the folders the guards refuse to use or write in.

The library folders come from the real settings file through
`astrometricslib.drivers.library_folders`, which follows the same rules as the
app's own settings (`AppConfiguration.get_library_path`, ``get_frames_path``
and ``get_stacks_path``), so the simulator protects exactly the folders the app
writes to. This module is the one definition of "the real folders".
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from astrometricslib.drivers import library_folders
from backend.public_interface import (
    REAL_CONFIG_HOME_VARIABLE,
    REAL_CONFIG_PATH_VARIABLE,
    REAL_DATA_HOME_VARIABLE,
    REAL_STATE_HOME_VARIABLE,
)

__all__ = ["RealFolders", "real_folders"]


@dataclass(frozen=True)
class RealFolders:
    """The user's real settings file and library folders.

    Attributes
    ----------
    config_home, data_home, state_home : `pathlib.Path`
        The folders that hold ``astrometrics/`` for settings, for data and
        for state (the run logs).
    config_file : `pathlib.Path`
        The real settings file.
    library, frames, stacks, wayfinding : `pathlib.Path`
        The image library, its frames, the folder for stacks and other
        derived files, and the wayfinding library, as the real settings file
        names them (or their defaults).
    """

    config_home: Path
    data_home: Path
    config_file: Path
    library: Path
    frames: Path
    stacks: Path
    wayfinding: Path
    state_home: Path

    @property
    def logs(self) -> Path:
        """Give the real folder of the job and run logs.

        Returns
        -------
        logs : `pathlib.Path`
            ``<state_home>/astrometrics/logs``. Nothing is created.
        """
        return self.state_home / "astrometrics" / "logs"

    def protected_paths(self) -> tuple[Path, ...]:
        """Give the folders that a simulator must never use or write in.

        Returns
        -------
        paths : `tuple` [`pathlib.Path`]
            The library, frames, stacks and wayfinding folders, and the
            real ``astrometrics`` data and settings folders, each resolved.
        """
        found = (
            self.library,
            self.frames,
            self.stacks,
            self.wayfinding,
            self.data_home / "astrometrics",
            self.config_home / "astrometrics",
        )
        return tuple(dict.fromkeys(path.resolve() for path in found))


def real_folders(environ: Mapping[str, str] | None = None) -> RealFolders:
    """Find the user's real settings file and library folders.

    A launcher may move the ``XDG_*`` variables and the settings path to a
    world of its own, and keep the real ones in ``ASTROMETRICS_REAL_*``
    variables. This function honours those first, so it names the real
    folders both before and after the move. It reads the real settings file
    (read only) and creates nothing.

    Parameters
    ----------
    environ : `~collections.abc.Mapping` [`str`, `str`], optional
        The environment. Default: the process environment.

    Returns
    -------
    folders : `RealFolders`
        The real folders. A missing or damaged settings file gives the
        defaults.
    """
    environment = os.environ if environ is None else environ
    home = Path.home()
    config_home = Path(
        environment.get(REAL_CONFIG_HOME_VARIABLE) or environment.get("XDG_CONFIG_HOME") or home / ".config"
    )
    data_home = Path(
        environment.get(REAL_DATA_HOME_VARIABLE)
        or environment.get("XDG_DATA_HOME")
        or home / ".local" / "share"
    )
    state_home = Path(
        environment.get(REAL_STATE_HOME_VARIABLE)
        or environment.get("XDG_STATE_HOME")
        or home / ".local" / "state"
    )
    named_file = (
        environment.get(REAL_CONFIG_PATH_VARIABLE)
        or environment.get("ASTROMETRICS_CONFIG_PATH")
        or environment.get("ASTROMETRICS_CONFIG")
    )
    config_file = (
        Path(named_file) if named_file else config_home / "astrometrics" / "astrometrics.config.toml"
    )
    named = library_folders(config_file, default_data_home=data_home)
    return RealFolders(
        config_home,
        data_home,
        config_file,
        named.library,
        named.frames,
        named.stacks,
        named.wayfinding,
        state_home,
    )
