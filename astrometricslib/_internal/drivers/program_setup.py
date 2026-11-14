r"""Purpose: Set up the outside programs the pipeline uses, on Windows.

Description: The stacking pipeline runs two outside programs: Siril, which
does the stacking, and GraXpert, which removes the sky's gradient before the
preview picture is stretched. On Linux they come from the system's package
manager. On Windows the app sets them up for the user, each from one pinned
release on its own project's site: the file is downloaded, checked against a
recorded size and SHA-256 checksum, unpacked into the app's data folder
(``%LOCALAPPDATA%\Astrometrics\Data\<program>\<version>``) and the program's
setting is pointed at it. No administrator rights are needed and nothing is
installed system-wide. A download that does not match its checksum is refused
and deleted. To move to a newer version, change that program's
`ProgramRelease` (version, address, size and checksum) together.

Siril comes as a zip. GraXpert comes as a Windows Installer package (.msi),
which is unpacked into the folder with ``msiexec /a`` (an "administrative
install": a copy of the files, with nothing registered in Windows).

The Windows setup (``build/windows/setup_windows.ps1``) runs this through
``python -m astrometricslib.scripts.set_up_programs``, the way it installs
the Python and Node packages. A user who already has one of these programs
keeps it: a setting that names an existing program is left alone.
"""

import hashlib
import logging
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- runs msiexec to unpack a checked download
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import ConfigurationError, ExternalServiceError
from astrometricslib.foundation.offline import require_online
from astrometricslib.foundation.paths import data_directory, scratch_directory
from astrometricslib.foundation.platform_tools import on_windows, split_program_command

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProgramRelease:
    """One pinned download of an outside program.

    Attributes
    ----------
    name : `str`
        The program's name, such as ``"Siril"``; also its folder name.
    version : `str`
        The pinned version.
    url : `str`
        Where the file is downloaded from (the project's own site, https).
    size_bytes : `int`
        The file's exact size.
    sha256 : `str`
        The file's SHA-256 checksum, recorded when the release was pinned.
    archive : ``"zip"`` or ``"msi"``
        How the file is unpacked.
    program : `str`
        The program inside the unpacked folder, relative to its top.
    section : `str`
        The settings section of the program's setting.
    key : `str`
        The setting that names the program.
    """

    name: str
    version: str
    url: str
    size_bytes: int
    sha256: str
    archive: Literal["zip", "msi"]
    program: str
    section: str
    key: str


SIRIL_RELEASE = ProgramRelease(
    name="Siril",
    version="1.4.5",
    url="https://free-astro.org/download/siril-1.4.5-ucrt64_win.zip",
    size_bytes=170_123_272,
    sha256="5e54c13c243e36343f0a7331998662cf349cefcd822158fc6515491c6e5528fd",
    archive="zip",
    program="siril/bin/siril-cli.exe",
    section="Processing.Siril",
    key="siril_executable",
)
"""The Siril the app sets up on Windows.

Pinned on 2026-11-14 from https://siril.org/download/ (released 2026-10-28).
The size and checksum are those of the file downloaded that day, which then
stacked real frames with this driver.
"""

GRAXPERT_RELEASE = ProgramRelease(
    name="GraXpert",
    version="3.0.2",
    url="https://github.com/Steffenhir/GraXpert/releases/download/3.0.2/graxpert-windows-amd64.msi",
    size_bytes=160_124_928,
    sha256="767fac98f57d89185a0ac5d1c1aee43083f1310e77682168a9aa3a70a88c8bd6",
    archive="msi",
    program="GraXpert.exe",
    section="Processing.GraXpert",
    key="graxpert_executable",
)
r"""The GraXpert the app sets up on Windows.

Pinned on 2026-11-14 from the project's GitHub releases (3.0.2, released
2024-05-03, the newest release that is not a release candidate). The size and
checksum are those of the file downloaded that day, which then removed the
gradient of a real stack with the app's command. GraXpert downloads its AI
model (version 1.0.1) on first use, into ``%LOCALAPPDATA%\GraXpert``.
"""

PINNED_RELEASES: tuple[ProgramRelease, ...] = (SIRIL_RELEASE, GRAXPERT_RELEASE)
"""Every program the Windows setup sets up, in order."""

DOWNLOAD_CHUNK_BYTES = 1024 * 1024
"""How much of the download is read at a time."""

DOWNLOAD_TIMEOUT_SECONDS = 60
"""Give up when the download server sends nothing for this long."""

MSIEXEC_TIMEOUT_SECONDS = 600
"""Longest wait for ``msiexec`` to unpack an installer package."""


def install_folder(release: ProgramRelease) -> Path:
    """Give the folder the app sets a program up in, without creating it.

    Parameters
    ----------
    release : `ProgramRelease`
        Which program.

    Returns
    -------
    folder : `pathlib.Path`
        ``<data folder>/<name>/<version>``.
    """
    return Path(data_directory()) / release.name / release.version


def installed_program(release: ProgramRelease) -> Path | None:
    """Give the program the app set up, if it is there.

    Parameters
    ----------
    release : `ProgramRelease`
        Which program.

    Returns
    -------
    program : `pathlib.Path` or `None`
        The program the app unpacked, or `None` if there is none.
    """
    program = install_folder(release) / release.program
    return program if program.is_file() else None


def program_exists(setting: str) -> bool:
    """Say whether a program setting names a program that can be found.

    Parameters
    ----------
    setting : `str`
        The setting, such as ``siril-cli`` or a full path.

    Returns
    -------
    found : `bool`
        `True` if its first word is an existing file or a program on the
        search path.
    """
    parts = split_program_command(setting)
    return bool(parts) and (os.path.isfile(parts[0]) or shutil.which(parts[0]) is not None)


def current_setting(config: AppConfiguration, release: ProgramRelease) -> str:
    """Read the setting that names a program.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings.
    release : `ProgramRelease`
        Which program.

    Returns
    -------
    setting : `str`
        The setting, or an empty string when it is missing.
    """
    return str(config.get_value(release.section, release.key, fallback="") or "")


def _ignore_progress(percent: int, message: str) -> None:
    """Take a progress report and do nothing with it.

    Parameters
    ----------
    percent : `int`
        How far the work is.
    message : `str`
        What it is doing.
    """


def set_up_program(
    config: AppConfiguration,
    release: ProgramRelease,
    progress: Callable[[int, str], None] | None = None,
) -> Path:
    """Download a pinned program, check it, unpack it and use it.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings; the program's setting is set to the new program and
        saved.
    release : `ProgramRelease`
        Which program.
    progress : `~collections.abc.Callable`, optional
        Called with a percentage and a short message as the work goes on.

    Returns
    -------
    program : `pathlib.Path`
        The program now in use.

    Raises
    ------
    ConfigurationError
        If this is not Windows (Linux uses the package manager).
    ExternalServiceError
        If the download fails, does not match the pinned size and checksum
        (it is deleted, nothing is unpacked), or cannot be unpacked.
    """
    if not on_windows():
        raise ConfigurationError(
            f"The app sets up {release.name} only on Windows; install it with the package manager."
        )
    report = progress or _ignore_progress
    program = installed_program(release)
    if program is None:
        require_online(f"the {release.name} download")
        archive = _download(release, report)
        try:
            _unpack(archive, release, install_folder(release), report)
        finally:
            archive.unlink(missing_ok=True)
        program = installed_program(release)
        if program is None:
            raise ExternalServiceError(f"The {release.name} download has no {release.program}.")
    with config.write_lock():
        if release.section not in config.app_config:
            config.app_config.add_section(release.section)
        config.app_config.set(release.section, release.key, str(program))
        config.save_configuration()
    report(100, f"{release.name} {release.version} is ready")
    logger.info("%s %s set up at %s", release.name, release.version, program)
    return program


def _download(release: ProgramRelease, report: Callable[[int, str], None]) -> Path:
    """Download the file into the local cache folder and check it.

    Parameters
    ----------
    release : `ProgramRelease`
        Which program.
    report : `~collections.abc.Callable`
        Takes a percentage (0 to 90 here) and a message.

    Returns
    -------
    archive : `pathlib.Path`
        The checked file. The caller deletes it.

    Raises
    ------
    ExternalServiceError
        If the download fails or does not match the pinned size and checksum.
    """
    if not release.url.startswith("https://"):
        raise ExternalServiceError(f"The {release.name} download address must use https: {release.url}")
    descriptor, name = tempfile.mkstemp(
        prefix=f"{release.name.lower()}-", suffix=f".{release.archive}", dir=scratch_directory()
    )
    os.close(descriptor)
    archive = Path(name)
    digest = hashlib.sha256()
    received = 0
    try:
        with (
            urllib.request.urlopen(release.url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response,  # ruff: ignore[suspicious-url-open-usage] -- https only, checked above
            archive.open("wb") as target,
        ):
            while chunk := response.read(DOWNLOAD_CHUNK_BYTES):
                target.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if received > release.size_bytes:
                    break
                report(
                    int(90 * received / release.size_bytes), f"Downloading {release.name} {release.version}"
                )
    except OSError as error:
        archive.unlink(missing_ok=True)
        raise ExternalServiceError(f"The {release.name} download failed: {error}") from error
    if received != release.size_bytes or digest.hexdigest() != release.sha256:
        archive.unlink(missing_ok=True)
        raise ExternalServiceError(
            f"The {release.name} download does not match the pinned file ({received} bytes); it was deleted."
        )
    return archive


def _unpack(archive: Path, release: ProgramRelease, folder: Path, report: Callable[[int, str], None]) -> None:
    """Unpack the checked file into its folder, all at once or not at all.

    Parameters
    ----------
    archive : `pathlib.Path`
        The checked file.
    release : `ProgramRelease`
        Which program, for how to unpack it.
    folder : `pathlib.Path`
        Where the program goes. It is replaced only when the unpacking
        finished.
    report : `~collections.abc.Callable`
        Takes a percentage and a message. An `ExternalServiceError` from
        `_unpack_zip` or `_unpack_msi` comes through, after the half-made
        folder is removed.
    """
    report(92, f"Unpacking {release.name}")
    partial = folder.with_name(folder.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    try:
        if release.archive == "zip":
            _unpack_zip(archive, partial)
        else:
            _unpack_msi(archive, partial)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    shutil.rmtree(folder, ignore_errors=True)
    partial.rename(folder)


def _unpack_zip(archive: Path, partial: Path) -> None:
    """Unpack a zip, refusing any path that would land outside the folder.

    Parameters
    ----------
    archive : `pathlib.Path`
        The checked zip.
    partial : `pathlib.Path`
        The empty folder to fill.

    Raises
    ------
    ExternalServiceError
        If the zip holds a path outside `partial`.
    """
    with zipfile.ZipFile(archive) as zip_file:
        root = partial.resolve()
        for member in zip_file.namelist():
            if not (root / member).resolve().is_relative_to(root):
                raise ExternalServiceError(f"The download holds an unsafe path: {member}")
        zip_file.extractall(partial)


def _unpack_msi(archive: Path, partial: Path) -> None:
    """Copy the files of a Windows Installer package into a folder.

    ``msiexec /a`` makes an "administrative install": the package's files
    are copied into the folder and nothing is registered in Windows, so no
    administrator rights are needed. ``/qn`` shows no window.

    Parameters
    ----------
    archive : `pathlib.Path`
        The checked package.
    partial : `pathlib.Path`
        The empty folder to fill.

    Raises
    ------
    ExternalServiceError
        If ``msiexec`` fails or runs too long.
    """
    # One command line, not a list: msiexec reads TARGETDIR="..." itself, and
    # a list would put the quotes around the whole argument instead.
    command = f'msiexec /a "{archive}" /qn TARGETDIR="{partial}"'
    try:
        completed = subprocess.run(command, capture_output=True, timeout=MSIEXEC_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ExternalServiceError(f"Unpacking {archive.name} failed: {error}") from error
    if completed.returncode != 0:
        raise ExternalServiceError(
            f"Unpacking {archive.name} failed: msiexec exited with {completed.returncode}."
        )
