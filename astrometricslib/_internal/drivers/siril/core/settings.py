r"""Purpose: The app's private Siril settings file, used by every Siril run.

Description: Siril reads its settings from a file when it starts and writes
them back when it stops. Some commands change the settings as they run: after
each plate solve, Siril stores the solved focal length and pixel size as the
new defaults (``update_default_scale``). If the app ran Siril with the user's
own settings file, a stack or a preview would quietly change the user's Siril.

So every Siril run the driver starts reads a private settings file instead
(Siril's ``-i`` option). `prepare_private_settings` makes it:

1. The first time, it copies the user's Siril settings file, so the app's
   Siril behaves like the user's (the same catalogs, swap folder and so on).
   The user's file is only read, never written.
2. Every time, it copies in the paths of the outside tools Siril runs:
   StarNet (``starnet_exe``) and GraXpert (``graxpert_path``). These are set
   by the user or by the GPU setup step after the first copy, so they are
   read again from the user's file.
3. Every time, it sets ``update_default_scale=false`` in the
   ``[astrometry]`` section, so a plate solve never changes the defaults.

The file is edited line by line, so every other line stays as Siril wrote it.
It lives in the app's data folder (``siril/siril_settings.ini``). A Flatpak
Siril can read it there because the driver gives the sandbox the host's files.

Siril's own settings live in ``config.<version>.ini`` (or ``config.ini`` for
an old version) in Siril's settings folder:

* Flatpak: ``~/.var/app/org.siril.Siril/config/siril``.
* Native Linux: ``$XDG_CONFIG_HOME/siril``, by default ``~/.config/siril``.
* Windows: ``%LOCALAPPDATA%\siril``.
"""

import logging
import os
import re
import sys
import tempfile
import threading

from astrometricslib._internal.drivers.siril.core.program import flatpak_app_id, is_flatpak
from astrometricslib.foundation.paths import data_directory

logger = logging.getLogger(__name__)

# The name of the private settings file in its folder.
PRIVATE_SETTINGS_FILE_NAME = "siril_settings.ini"

# The settings that name the outside tools Siril runs, as (section, key).
# They are copied from the user's file on every run.
COPIED_TOOL_SETTINGS = (("core", "starnet_exe"), ("core", "graxpert_path"))

# The setting that stops a plate solve from changing Siril's default focal
# length and pixel size, as (section, key, value).
FIXED_SCALE_SETTING = ("astrometry", "update_default_scale", "false")

# One thread at a time edits the file in this process. Separate processes
# are safe too: each writes a whole new file and swaps it in at once.
_settings_lock = threading.Lock()

_SECTION_PATTERN = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_VERSIONED_FILE_PATTERN = re.compile(r"config\.(\d+(?:\.\d+)*)\.ini")


def private_settings_folder() -> str:
    """Give the folder of the private settings file, creating it.

    Returns
    -------
    folder : `str`
        ``siril`` in the app's data folder.
    """
    folder = os.path.join(data_directory(), "siril")
    os.makedirs(folder, exist_ok=True)
    return folder


def user_settings_folder(siril_executable: str) -> str:
    """Give the folder where the user's own Siril keeps its settings.

    Parameters
    ----------
    siril_executable : `str`
        The ``siril_executable`` setting. A Flatpak Siril keeps its settings
        inside the Flatpak's own folder.

    Returns
    -------
    folder : `str`
        Siril's settings folder (see the module description). It may not
        exist.
    """
    if is_flatpak(siril_executable):
        return os.path.expanduser(f"~/.var/app/{flatpak_app_id(siril_executable)}/config/siril")
    if sys.platform == "win32":
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "siril")
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(config_home, "siril")


def find_user_settings_file(folder: str) -> str | None:
    """Find the user's Siril settings file in Siril's settings folder.

    Parameters
    ----------
    folder : `str`
        Siril's settings folder.

    Returns
    -------
    path : `str` or `None`
        The ``config.<version>.ini`` with the highest version, otherwise
        ``config.ini``, or `None` if there is neither. Siril's half-written
        copies (``config.1.4.ini.ABC123``) are never chosen.
    """
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    versioned = []
    for name in names:
        match = _VERSIONED_FILE_PATTERN.fullmatch(name)
        if match:
            versioned.append((tuple(int(part) for part in match.group(1).split(".")), name))
    if versioned:
        return os.path.join(folder, max(versioned)[1])
    plain = os.path.join(folder, "config.ini")
    return plain if os.path.isfile(plain) else None


def read_setting(lines: list[str], section: str, key: str) -> str | None:
    """Read one setting from the lines of a Siril settings file.

    Parameters
    ----------
    lines : `list` [`str`]
        The file's lines, without line ends.
    section : `str`
        The section name, without brackets.
    key : `str`
        The setting's name.

    Returns
    -------
    value : `str` or `None`
        The text after ``=``, exactly as written, or `None` if the setting
        is not in the section.
    """
    current = None
    for line in lines:
        header = _SECTION_PATTERN.match(line)
        if header:
            current = header.group(1).strip()
            continue
        if current == section and "=" in line:
            name, value = line.split("=", 1)
            if name.strip() == key:
                return value
    return None


def write_setting(lines: list[str], section: str, key: str, value: str) -> list[str]:
    """Set one setting in the lines of a Siril settings file.

    Parameters
    ----------
    lines : `list` [`str`]
        The file's lines, without line ends. Not changed.
    section : `str`
        The section name, without brackets.
    key : `str`
        The setting's name.
    value : `str`
        The text to write after ``=``.

    Returns
    -------
    new_lines : `list` [`str`]
        The lines with the setting replaced where it was, or added at the end
        of its section, or added with a new section at the end of the file.
    """
    new_lines = list(lines)
    current = None
    section_end = None
    for index, line in enumerate(new_lines):
        header = _SECTION_PATTERN.match(line)
        if header:
            if current == section:
                break
            current = header.group(1).strip()
            if current == section:
                section_end = index + 1
            continue
        if current == section:
            if "=" in line and line.split("=", 1)[0].strip() == key:
                new_lines[index] = f"{key}={value}"
                return new_lines
            if line.strip():
                section_end = index + 1
    if section_end is None:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        return [*new_lines, f"[{section}]", f"{key}={value}"]
    new_lines.insert(section_end, f"{key}={value}")
    return new_lines


def _read_lines(path: str) -> list[str]:
    """Read a text file as lines without line ends.

    Parameters
    ----------
    path : `str`
        The file.

    Returns
    -------
    lines : `list` [`str`]
        The lines, or an empty list if the file cannot be read.
    """
    try:
        with open(path, encoding="utf-8") as settings_file:
            return settings_file.read().splitlines()
    except (OSError, UnicodeDecodeError) as error:
        logger.debug("Could not read the Siril settings file '%s': %s", path, error)
        return []


def _write_lines(path: str, lines: list[str]) -> None:
    """Write a settings file whole, swapping the new file in at once.

    Parameters
    ----------
    path : `str`
        The file to write.
    lines : `list` [`str`]
        Its lines, without line ends.
    """
    handle, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".siril_settings_")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as settings_file:
            settings_file.write("\n".join(lines) + "\n")
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.remove(temporary)
        raise


def prepare_private_settings(
    siril_executable: str, private_folder: str | None = None, user_folder: str | None = None
) -> str:
    """Make or refresh the private Siril settings file and give its path.

    Parameters
    ----------
    siril_executable : `str`
        The ``siril_executable`` setting, which says where the user's own
        Siril settings are.
    private_folder : `str`, optional
        The folder of the private file. Default: `private_settings_folder`.
    user_folder : `str`, optional
        The user's Siril settings folder, only read. Default:
        `user_settings_folder`.

    Returns
    -------
    path : `str`
        The private settings file, which exists when this returns. See the
        module description for what it holds.
    """
    folder = private_folder if private_folder is not None else private_settings_folder()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, PRIVATE_SETTINGS_FILE_NAME)
    user_file = find_user_settings_file(
        user_folder if user_folder is not None else user_settings_folder(siril_executable)
    )
    user_lines = _read_lines(user_file) if user_file else []
    with _settings_lock:
        if os.path.isfile(path):
            old_lines = _read_lines(path)
        else:
            old_lines = []
            logger.info(
                "Made the app's private Siril settings file %s from %s.",
                path,
                user_file or "Siril's built-in defaults",
            )
        lines = old_lines or list(user_lines)
        for section, key in COPIED_TOOL_SETTINGS:
            value = read_setting(user_lines, section, key)
            if value is not None and value.strip() and read_setting(lines, section, key) != value:
                lines = write_setting(lines, section, key, value)
        section, key, value = FIXED_SCALE_SETTING
        if read_setting(lines, section, key) != value:
            lines = write_setting(lines, section, key, value)
        if lines != old_lines or not os.path.isfile(path):
            _write_lines(path, lines)
    return path
