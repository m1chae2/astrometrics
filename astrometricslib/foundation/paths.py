r"""Purpose: Name the folders for working files and handle mounted drives.

Description: Two groups of paths live here.

* Where this program keeps its own files on the local disk: working files
  (cache), durable data (databases, the observatory library), and run state
  (logs). On Linux these follow the XDG convention (``~/.cache``,
  ``~/.local/share``, ``~/.local/state``); on many Linux systems ``/tmp`` is
  kept in RAM, so large scratch files there would use up memory. On Windows
  they are ``Cache``, ``Data`` and ``State`` inside
  ``%LOCALAPPDATA%\Astrometrics``, the per-user, per-computer folder Windows
  provides for this. An ``XDG_*`` variable, when set, wins on both systems.
* The two ways Linux spells the path of a mounted drive. Desktop Linux mounts
  a drive under ``/media/<user>/`` on some systems and ``/run/media/<user>/``
  on others. A configuration file written on one system and read on the other
  names a path that does not exist. The functions here treat the two spellings
  as the same place.
"""

import logging
import os
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

WINDOWS_APP_FOLDER = "Astrometrics"
"""The folder in ``%LOCALAPPDATA%`` for this program's files on Windows."""


def _on_windows() -> bool:
    """Say whether this program runs on Windows.

    A function, not a constant, so tests can choose the system.

    Returns
    -------
    windows : `bool`
        `True` on Windows.
    """
    return sys.platform == "win32"


def _windows_app_folder() -> str:
    r"""Give ``%LOCALAPPDATA%\Astrometrics``, without creating it.

    Returns
    -------
    folder : `str`
        The folder. ``%LOCALAPPDATA%`` is normally set; without it,
        ``~\AppData\Local`` is used, which is where Windows puts it.
    """
    local_app_data = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local"
    )
    return os.path.join(local_app_data, WINDOWS_APP_FOLDER)


def _user_folder(xdg_variable: str, linux_parts: tuple[str, ...], windows_name: str) -> str:
    r"""Give one of this program's own folders, without creating it.

    Parameters
    ----------
    xdg_variable : `str`
        The XDG variable that overrides the folder, such as
        ``"XDG_CACHE_HOME"``.
    linux_parts : `tuple` [`str`, ...]
        The default parent below the home folder on Linux, such as
        ``(".cache",)``.
    windows_name : `str`
        The subfolder of ``%LOCALAPPDATA%\Astrometrics`` on Windows.

    Returns
    -------
    folder : `str`
        ``$<xdg_variable>/astrometrics`` when the variable is set. Otherwise
        ``%LOCALAPPDATA%\Astrometrics\<windows_name>`` on Windows and
        ``~/<linux_parts>/astrometrics`` elsewhere.
    """
    xdg_root = os.environ.get(xdg_variable)
    if xdg_root:
        return os.path.join(xdg_root, "astrometrics")
    if _on_windows():
        return os.path.join(_windows_app_folder(), windows_name)
    return os.path.join(os.path.expanduser("~"), *linux_parts, "astrometrics")


def scratch_directory() -> str:
    r"""Give the folder for Python's temporary working files, creating it.

    This is ``~/.cache/astrometrics`` on Linux and
    ``%LOCALAPPDATA%\Astrometrics\Cache`` on Windows, on the local disk. It is
    used instead of the system temporary folder, which is often held in RAM.

    Returns
    -------
    scratch_path : `str`
        The absolute path of the folder, which exists when this returns.
    """
    scratch_path = _user_folder("XDG_CACHE_HOME", (".cache",), "Cache")
    os.makedirs(scratch_path, exist_ok=True)
    return scratch_path


def siril_work_directory() -> str:
    """Give the folder where Siril keeps its working files, creating it.

    This is ``~/.cache/astrometrics/siril`` on the local disk, inside the
    one folder that holds everything disposable. Siril runs in a Flatpak
    sandbox with access to the home folder, so it can use this path. The
    system temporary folder is not used because the sandbox cannot see it.

    Returns
    -------
    work_path : `str`
        The absolute path of the folder, which exists when this returns.
    """
    work_path = os.path.join(scratch_directory(), "siril")
    os.makedirs(work_path, exist_ok=True)
    return work_path


def _legacy_windows_data_folder() -> str:
    r"""Give the data folder older Windows installs used.

    Returns
    -------
    folder : `str`
        ``~\.local\share\astrometrics``, the Linux default, which older
        versions also used on Windows.
    """
    return os.path.join(os.path.expanduser("~"), ".local", "share", "astrometrics")


def default_data_path(use_environment: bool = True) -> str:
    r"""Give the folder for this program's durable data, without creating it.

    On Windows a data folder left at the old place (see
    `_legacy_windows_data_folder`) is used until the backend has moved it
    (`move_legacy_windows_data`), so no program looks at an empty new
    folder.

    Parameters
    ----------
    use_environment : `bool`, optional
        If `False`, ``XDG_DATA_HOME`` is ignored and the system's default is
        given. Default: `True`.

    Returns
    -------
    data_path : `str`
        ``$XDG_DATA_HOME/astrometrics`` if ``XDG_DATA_HOME`` is set (and
        used). Otherwise ``%LOCALAPPDATA%\Astrometrics\Data`` on Windows and
        ``~/.local/share/astrometrics`` elsewhere.
    """
    if use_environment and os.environ.get("XDG_DATA_HOME"):
        return _user_folder("XDG_DATA_HOME", (".local", "share"), "Data")
    if not _on_windows():
        return os.path.join(os.path.expanduser("~"), ".local", "share", "astrometrics")
    data_path = os.path.join(_windows_app_folder(), "Data")
    legacy_path = _legacy_windows_data_folder()
    if not os.path.exists(data_path) and os.path.isdir(legacy_path):
        return legacy_path
    return data_path


def move_legacy_windows_data() -> bool:
    """Move the data folder of an older Windows install to its new place, once.

    Only the backend calls this, first thing at start, before it opens any
    database. Every other program (scripts, MCP servers, a dry run) only
    reads `default_data_path`, which keeps naming the old folder until the
    move is done. Moving it from any program would pull the folder from
    under a backend that is running from it, and that backend would then
    make a new, empty one at the old place.

    The folder is renamed in one step, so it is either moved whole or not at
    all. The rename fails while another program has a file in it open
    (Windows does not allow it) or when the two places are on different
    drives; the old folder is then kept and used, and the next start tries
    again.

    Returns
    -------
    moved : `bool`
        `True` if the folder was moved by this call.
    """
    if not _on_windows() or os.environ.get("XDG_DATA_HOME"):
        return False
    new_path = os.path.join(_windows_app_folder(), "Data")
    legacy_path = _legacy_windows_data_folder()
    if os.path.exists(new_path) or not os.path.isdir(legacy_path):
        return False
    os.makedirs(os.path.dirname(new_path), exist_ok=True)
    try:
        os.rename(legacy_path, new_path)
    except OSError as error:
        # Expected while another program has a file there open: a status,
        # not a failure. The data stays usable where it is.
        logger.info("Kept the data folder at %s for now; moving it failed: %s", legacy_path, error)
        return False
    logger.info("Moved the data folder from %s to %s", legacy_path, new_path)
    return True


def data_directory() -> str:
    r"""Give the folder for this program's durable local state, creating it.

    This is ``~/.local/share/astrometrics`` on Linux and
    ``%LOCALAPPDATA%\Astrometrics\Data`` on Windows. Unlike the cache
    folder, nothing here may be deleted to free space. On Windows a data
    folder at the old place is used until the backend has moved it
    (`move_legacy_windows_data`).

    Returns
    -------
    data_path : `str`
        The folder `default_data_path` names. It exists when this returns.
    """
    data_path = default_data_path()
    os.makedirs(data_path, exist_ok=True)
    return data_path


def config_directory() -> str:
    """Give the folder for this program's settings file, without creating it.

    This is ``~/.config/astrometrics``. It holds
    ``astrometrics.config.toml``. The folder is not created here, so that
    only saving the settings creates it.

    Returns
    -------
    config_path : `str`
        ``$XDG_CONFIG_HOME/astrometrics`` if ``XDG_CONFIG_HOME`` is set,
        otherwise ``~/.config/astrometrics``.
    """
    config_root = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(config_root, "astrometrics")


def state_directory() -> str:
    r"""Give the folder for this program's run logs and state, creating it.

    This is ``~/.local/state/astrometrics`` on Linux and
    ``%LOCALAPPDATA%\Astrometrics\State`` on Windows. It holds records of
    what the program did, such as log files. They are worth keeping for a
    while but can be rebuilt by running the program again.

    Returns
    -------
    state_path : `str`
        ``$XDG_STATE_HOME/astrometrics`` if ``XDG_STATE_HOME`` is set,
        otherwise the folder above. It exists when this returns.
    """
    state_path = _user_folder("XDG_STATE_HOME", (".local", "state"), "State")
    os.makedirs(state_path, exist_ok=True)
    return state_path


def locks_directory() -> str:
    """Give the folder for lock files, creating it.

    Lock files stop two programs from using one device or resource at the
    same time. They hold no data and are safe to delete when nothing runs.
    They live in the cache folder on the local disk, so every program on
    this computer finds the same locks whichever library it uses.

    Returns
    -------
    locks_path : `str`
        ``<scratch folder>/locks``, which exists when this returns.
    """
    locks_path = os.path.join(scratch_directory(), "locks")
    os.makedirs(locks_path, exist_ok=True)
    return locks_path


def catalog_cache_directory() -> str:
    """Give the folder for downloaded catalog data that can be fetched again.

    Returns
    -------
    catalog_path : `str`
        ``<scratch folder>/catalogs``, which exists when this returns.
    """
    catalog_path = os.path.join(scratch_directory(), "catalogs")
    os.makedirs(catalog_path, exist_ok=True)
    return catalog_path


def backup_directory() -> str:
    """Give the local folder that holds the database snapshots.

    This is ``backup`` inside the scratch folder. It is not uploaded
    anywhere.

    Returns
    -------
    backup_path : `str`
        The absolute path of the folder, which exists when this returns.
    """
    backup_path = os.path.join(scratch_directory(), "backup")
    os.makedirs(backup_path, exist_ok=True)
    return backup_path


def mount_path_variants(path: str) -> set[str]:
    """List the ways one drive's path can be written on Linux.

    Parameters
    ----------
    path : `str`
        An absolute path.

    Returns
    -------
    variants : `set` [`str`]
        The path and its other ``/media`` or ``/run/media`` spelling, with
        no trailing slash.
    """
    normalised = os.path.normpath(path)
    variants = {normalised}
    if normalised.startswith("/run/media/"):
        variants.add(normalised.replace("/run/media/", "/media/", 1))
    elif normalised.startswith("/media/"):
        variants.add(normalised.replace("/media/", "/run/media/", 1))
    return variants


def resolve_mounted_path(path: str) -> str:
    """Give the spelling of a path that exists on this computer.

    Parameters
    ----------
    path : `str`
        A candidate filesystem path.

    Returns
    -------
    resolved : `str`
        `path` if it exists or has no other spelling that exists. Otherwise
        the other ``/media`` or ``/run/media`` spelling.
    """
    if not path or os.path.exists(path):
        return path
    for variant in sorted(mount_path_variants(path)):
        if variant != os.path.normpath(path) and os.path.exists(variant):
            return variant
    return path


def is_path_inside(root: str, path: str) -> bool:
    """Say whether a resolved path is `root` or lies below it.

    Both paths may be spelled either way (``/media`` or ``/run/media``).

    Parameters
    ----------
    root : `str`
        A resolved directory path.
    path : `str`
        A resolved path to test.

    Returns
    -------
    inside : `bool`
        `True` if `path` is `root` or inside it. `False` if it is not, or if
        the two cannot be compared (for example, paths on different drives).
    """
    for path_variant in mount_path_variants(path):
        for root_variant in mount_path_variants(root):
            try:
                if os.path.commonpath([root_variant, path_variant]) == root_variant:
                    return True
            except ValueError:
                continue
    return False


@dataclass(frozen=True)
class LibraryFolders:
    """The library folders that one settings file names.

    ``config_file`` is the settings file that was read. ``library``,
    ``frames``, ``stacks`` and ``wayfinding`` are the image library, its
    frames, the folder for stacks and other derived files, and the wayfinding
    library, as the settings file names them (or their defaults). All are
    `pathlib.Path` objects.
    """

    config_file: Path
    library: Path
    frames: Path
    stacks: Path
    wayfinding: Path


def resolve_library_setting(text: str, project_root: Path, search_package: bool = False) -> Path:
    """Turn the text of a folder setting into an absolute path.

    This is the one rule for the folder settings of ``[Image Library]`` and
    ``[Wayfinding Library]``. `AppConfiguration` and `library_folders` both
    use it, so a program that wants to know which folders the app writes to
    gets the app's own answer.

    Parameters
    ----------
    text : `str`
        The setting, not empty.
    project_root : `pathlib.Path`
        The project folder, against which a relative path is taken.
    search_package : `bool`, optional
        If `True`, a relative path is first looked for inside the
        ``astrometricslib`` folder, where an older install kept the library,
        and used there when it exists. Default: `False`.

    Returns
    -------
    path : `pathlib.Path`
        The absolute path. Nothing is created.
    """
    path = Path(text)
    if path.is_absolute():
        return path.absolute()
    if search_package:
        in_package = project_root / "astrometricslib" / path
        if in_package.exists():
            return in_package.absolute()
    return (project_root / path).absolute()


def _setting_text(settings: Mapping, section: str, key: str) -> str:
    """Read one folder setting from parsed settings.

    Parameters
    ----------
    settings : `~collections.abc.Mapping`
        The parsed settings.
    section, key : `str`
        Where the folder is named.

    Returns
    -------
    text : `str`
        The setting with spaces trimmed, or an empty string when the section
        or the key is missing.
    """
    value = settings.get(section, {})
    return str(value.get(key, "")).strip() if isinstance(value, Mapping) else ""


def library_folders(
    config_file: str | os.PathLike, default_data_home: str | os.PathLike | None = None
) -> LibraryFolders:
    """Find the library folders that one settings file names.

    It reads the file (read only) and creates nothing. It does not look at
    the environment, so it works for any settings file: the one in use, the
    user's real one, or one that belongs to a throwaway library. It follows
    the same rules as `AppConfiguration.get_library_path`,
    `~AppConfiguration.get_frames_path` and
    `~AppConfiguration.get_stacks_path` (through `resolve_library_setting`),
    so it names the folders the app would write to.

    Parameters
    ----------
    config_file : `str` or `os.PathLike`
        The settings file.
    default_data_home : `str` or `os.PathLike`, optional
        The folder whose ``astrometrics`` subfolder holds the libraries when
        the file does not name them. Default: the data folder itself
        (`default_data_path`).

    Returns
    -------
    folders : `LibraryFolders`
        The folders. A missing or damaged settings file gives the defaults.
    """
    path = Path(config_file)
    try:
        settings = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        settings = {}
    root = Path(__file__).resolve().parents[2]
    if default_data_home is not None:
        default_library = Path(default_data_home) / "astrometrics"
    else:
        # The system's default, not $XDG_DATA_HOME: this reads only the file.
        default_library = Path(default_data_path(use_environment=False))

    text = _setting_text(settings, "Image Library", "path")
    library = resolve_library_setting(text, root, search_package=True) if text else default_library

    text = _setting_text(settings, "Image Library", "frames_path")
    if text:
        frames = resolve_library_setting(text, root, search_package=True)
        if not frames.exists():
            frames = Path(resolve_mounted_path(str(frames)))
    else:
        frames = library / "frames"

    text = _setting_text(settings, "Image Library", "stacks_path")
    stacks = resolve_library_setting(text, root) if text else frames

    text = _setting_text(settings, "Wayfinding Library", "path")
    wayfinding = resolve_library_setting(text, root) if text else default_library / "wayfinding"
    return LibraryFolders(path, library, frames, stacks, wayfinding)
