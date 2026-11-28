r"""Purpose: Find the Siril program and build the command lines that start it.

Description: The ``siril_executable`` setting names Siril. It may be a native
program (``siril-cli``, or a full path such as
``C:\Program Files\Siril\bin\siril-cli.exe`` on Windows) or a Flatpak
(``flatpak run org.siril.Siril``). The setting is split into words the way the
system needs (`astrometricslib.foundation.system.split_program_command`, which
follows the shell on Linux and keeps Windows paths whole).

A Flatpak Siril runs in a sandbox that cannot see most folders. Every command
line built here gives it the host's files (``--filesystem=host``), the same
access the driver has always given it.

The functions here only build lists of words. They start nothing:
`session` starts the long pipe session of a stack, `script_run` starts a
short script, and `version` asks for the version. This module also finds
Siril's own Gaia star catalog on the disk (`find_local_gaia_catalog`), which
the plate solve and the colour calibration read.
"""

import os
import shlex

from astrometricslib.foundation.paths import data_directory
from astrometricslib.foundation.system import split_program_command

# The Flatpak name of Siril, as Flathub publishes it.
FLATPAK_SIRIL_APP_ID = "org.siril.Siril"

# The Flatpak option that lets the sandboxed Siril read and write the
# host's folders: the stacks, the work folder and the private settings file.
_HOST_FILES_OPTION = "--filesystem=host"


def is_flatpak(siril_executable: str) -> bool:
    """Say whether the Siril setting starts Siril from a Flatpak.

    Parameters
    ----------
    siril_executable : `str`
        The ``siril_executable`` setting.

    Returns
    -------
    flatpak : `bool`
        `True` if the program word is ``flatpak`` (or a path ending in it).
    """
    parts = split_program_command(siril_executable)
    return bool(parts) and "flatpak" in parts[0]


def flatpak_app_id(siril_executable: str) -> str:
    """Give the Flatpak name of the Siril the setting starts.

    Parameters
    ----------
    siril_executable : `str`
        A Flatpak setting, such as ``flatpak run org.siril.Siril``.

    Returns
    -------
    app_id : `str`
        The last word of the setting, which is the Flatpak name, or
        `FLATPAK_SIRIL_APP_ID` if the setting has no words.
    """
    parts = split_program_command(siril_executable)
    return parts[-1] if parts else FLATPAK_SIRIL_APP_ID


def _program_words(siril_executable: str) -> list[str]:
    """Split the setting and give a Flatpak Siril the host's files.

    Parameters
    ----------
    siril_executable : `str`
        The ``siril_executable`` setting.

    Returns
    -------
    parts : `list` [`str`]
        The words of the setting. For a Flatpak, ``--filesystem=host`` is
        added right after ``run``.
    """
    parts = split_program_command(siril_executable)
    if parts and "flatpak" in parts[0] and "run" in parts:
        parts.insert(parts.index("run") + 1, _HOST_FILES_OPTION)
    return parts


def pipe_session_arguments(
    siril_executable: str, command_pipe: str, output_pipe: str, settings_file: str
) -> list[str]:
    """Build the command line of a Siril that reads commands from a pipe.

    Siril's ``-p`` option makes it wait for commands on the pipe named by
    ``-r`` and write its answers to the pipe named by ``-w``. A Flatpak Siril
    is started through ``sh``, because the Flatpak's own start command is the
    window program, which does not take these options.

    Parameters
    ----------
    siril_executable : `str`
        The ``siril_executable`` setting.
    command_pipe : `str`
        The pipe Siril reads commands from.
    output_pipe : `str`
        The pipe Siril writes its answers to.
    settings_file : `str`
        The app's private Siril settings file (``-i``), see `settings`.

    Returns
    -------
    arguments : `list` [`str`]
        The command line, one word per entry.
    """
    parts = _program_words(siril_executable)
    if parts and "flatpak" in parts[0]:
        parts.insert(-1, "--command=sh")
        inner = (
            f"siril -i {shlex.quote(settings_file)} -p -r {shlex.quote(command_pipe)} "
            f"-w {shlex.quote(output_pipe)}"
        )
        return [*parts, "-c", inner]
    return [*parts, "-i", settings_file, "-p", "-r", command_pipe, "-w", output_pipe]


def script_arguments(
    siril_executable: str, directory: str, script_path: str, settings_file: str, offline: bool = False
) -> list[str]:
    """Build the command line of a Siril that runs one script file and exits.

    Parameters
    ----------
    siril_executable : `str`
        The ``siril_executable`` setting.
    directory : `str`
        The folder Siril starts in (``-d``).
    script_path : `str`
        The script file (``-s``).
    settings_file : `str`
        The app's private Siril settings file (``-i``), see `settings`.
    offline : `bool`, optional
        Whether Siril starts in offline mode (``-o``), so it never uses the
        network. Default `False`.

    Returns
    -------
    arguments : `list` [`str`]
        The command line, one word per entry. The script path is the last
        word.
    """
    return [
        *_program_words(siril_executable),
        *(["-o"] if offline else []),
        "-i",
        settings_file,
        "-d",
        directory,
        "-s",
        script_path,
    ]


def version_arguments(siril_executable: str) -> list[str]:
    """Build the command line that asks Siril for its version.

    A Flatpak Siril is asked through its command-line program
    (``siril-cli``), because the window program needs a display.

    Parameters
    ----------
    siril_executable : `str`
        The ``siril_executable`` setting.

    Returns
    -------
    arguments : `list` [`str`]
        The command line, one word per entry.
    """
    parts = split_program_command(siril_executable)
    if parts and "flatpak" in parts[0]:
        parts.insert(-1, "--command=siril-cli")
    return [*parts, "--version"]


def gui_arguments(siril_executable: str, file_path: str) -> list[str]:
    """Build the command line that opens a file in the Siril window.

    The window is the user's own Siril, so it keeps the user's own Siril
    settings: it is not given the private settings file.

    Parameters
    ----------
    siril_executable : `str`
        The ``siril_executable`` setting.
    file_path : `str`
        The file to open. An empty string opens Siril alone.

    Returns
    -------
    arguments : `list` [`str`]
        The command line, one word per entry.
    """
    return [*split_program_command(siril_executable), file_path]


# Siril's local Gaia catalog file, as its Catalog_Installer script names it.
_LOCAL_GAIA_CATALOG_NAME = "siril_cat_healpix8_astro.dat"


def setup_catalog_folder() -> str:
    """Give the folder the app's setup puts Siril's Gaia catalog in.

    Returns
    -------
    folder : `str`
        ``siril`` in the app's data folder, the folder of the private
        settings file. It is not created here.
    """
    return os.path.join(data_directory(), "siril")


def find_local_gaia_catalog(search_folders: list[str] | None = None) -> str | None:
    """Find the Gaia star catalog that Siril can read from the disk.

    The app's setup (`catalog_setup`) puts it in `setup_catalog_folder`.
    Siril's own Catalog_Installer script puts it in Siril's data folder. A
    Flatpak Siril keeps that folder under ``~/.var/app``, a normal install
    under ``~/.local/share``. Reading the catalog from the disk takes about
    10 seconds for a whole PCC run and needs no network.

    Parameters
    ----------
    search_folders : `list` [`str`], optional
        Folders to look in, in order. By default, the three places above.

    Returns
    -------
    path : `str` or `None`
        Path of the catalog file, or `None` if it is not installed.
    """
    if search_folders is None:
        search_folders = [
            setup_catalog_folder(),
            os.path.expanduser("~/.local/share/siril"),
            os.path.expanduser(f"~/.var/app/{FLATPAK_SIRIL_APP_ID}/data/siril"),
        ]
    for folder in search_folders:
        candidate = os.path.join(folder, _LOCAL_GAIA_CATALOG_NAME)
        if os.path.isfile(candidate):
            return candidate
    return None
