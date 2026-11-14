r"""Purpose: Set up Siril on Windows from its portable download.

Description: Siril does the stacking. On Linux it comes from the system's
package manager. On Windows the app can set up Siril's portable version for
the user: it downloads one pinned release from Siril's own site, checks the
file against a recorded size and SHA-256 checksum, unpacks it into the app's
data folder (``%LOCALAPPDATA%\Astrometrics\Data\Siril\<version>``) and points
the ``siril_executable`` setting at its ``siril-cli.exe``. No administrator
rights are needed and nothing is installed system-wide. A download that does
not match the checksum is refused and deleted. To move to a newer Siril,
change `SIRIL_RELEASE` (the version, address, size and checksum) together.

The Windows setup (``build/windows/setup_windows.ps1``) runs this through
``python -m astrometricslib.scripts.set_up_siril``, the way it installs the
Python and Node packages. A user who already has Siril keeps it: the script
leaves a ``siril_executable`` that names an existing program alone.
"""

import hashlib
import logging
import os
import shutil
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import ConfigurationError, ExternalServiceError
from astrometricslib.foundation.offline import require_online
from astrometricslib.foundation.paths import data_directory, scratch_directory
from astrometricslib.foundation.platform_tools import on_windows, split_program_command

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SirilRelease:
    """One pinned Siril download.

    Attributes
    ----------
    version : `str`
        Siril's version number.
    url : `str`
        Where the portable zip is downloaded from (Siril's own site).
    size_bytes : `int`
        The zip's exact size.
    sha256 : `str`
        The zip's SHA-256 checksum, recorded when the release was pinned.
    program : `str`
        The command-line program inside the zip, relative to its top.
    """

    version: str
    url: str
    size_bytes: int
    sha256: str
    program: str


SIRIL_RELEASE = SirilRelease(
    version="1.4.5",
    url="https://free-astro.org/download/siril-1.4.5-ucrt64_win.zip",
    size_bytes=170_123_272,
    sha256="5e54c13c243e36343f0a7331998662cf349cefcd822158fc6515491c6e5528fd",
    program="siril/bin/siril-cli.exe",
)
"""The Siril the app sets up on Windows.

Pinned on 2026-11-14 from https://siril.org/download/ (released 2026-10-28).
The size and checksum are those of the file downloaded that day, which then
stacked real frames with this driver.
"""

DOWNLOAD_CHUNK_BYTES = 1024 * 1024
"""How much of the download is read at a time."""

DOWNLOAD_TIMEOUT_SECONDS = 60
"""Give up when the download server sends nothing for this long."""


def siril_install_folder(release: SirilRelease = SIRIL_RELEASE) -> Path:
    """Give the folder the app sets Siril up in, without creating it.

    Parameters
    ----------
    release : `SirilRelease`, optional
        Which Siril. Default: `SIRIL_RELEASE`.

    Returns
    -------
    folder : `pathlib.Path`
        ``<data folder>/Siril/<version>``.
    """
    return Path(data_directory()) / "Siril" / release.version


def installed_siril_program(release: SirilRelease = SIRIL_RELEASE) -> Path | None:
    """Give the Siril program the app set up, if it is there.

    Parameters
    ----------
    release : `SirilRelease`, optional
        Which Siril. Default: `SIRIL_RELEASE`.

    Returns
    -------
    program : `pathlib.Path` or `None`
        The ``siril-cli.exe`` the app unpacked, or `None` if there is none.
    """
    program = siril_install_folder(release) / release.program
    return program if program.is_file() else None


def siril_program_exists(setting: str) -> bool:
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


def _ignore_progress(percent: int, message: str) -> None:
    """Take a progress report and do nothing with it.

    Parameters
    ----------
    percent : `int`
        How far the work is.
    message : `str`
        What it is doing.
    """


def set_up_siril(
    config: AppConfiguration,
    progress: Callable[[int, str], None] | None = None,
    release: SirilRelease = SIRIL_RELEASE,
) -> Path:
    """Download the pinned portable Siril, check it, unpack it and use it.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings; ``siril_executable`` in ``[Processing.Siril]`` is set
        to the new program and saved.
    progress : `~collections.abc.Callable`, optional
        Called with a percentage and a short message as the work goes on.
    release : `SirilRelease`, optional
        Which Siril. Default: `SIRIL_RELEASE`.

    Returns
    -------
    program : `pathlib.Path`
        The ``siril-cli.exe`` now in use.

    Raises
    ------
    ConfigurationError
        If this is not Windows (Linux gets Siril from its package manager).
    ExternalServiceError
        If the download fails, or the file does not match the pinned size
        and checksum (it is deleted, nothing is unpacked).
    """
    if not on_windows():
        raise ConfigurationError(
            "The app sets up Siril only on Windows; install it with the package manager."
        )
    report = progress or _ignore_progress
    program = installed_siril_program(release)
    if program is None:
        require_online("the Siril download")
        archive = _download(release, report)
        try:
            _unpack(archive, siril_install_folder(release), report)
        finally:
            archive.unlink(missing_ok=True)
        program = installed_siril_program(release)
        if program is None:
            raise ExternalServiceError(f"The Siril download has no {release.program}.")
    with config.write_lock():
        if "Processing.Siril" not in config.app_config:
            config.app_config.add_section("Processing.Siril")
        config.app_config.set("Processing.Siril", "siril_executable", str(program))
        config.save_configuration()
    report(100, f"Siril {release.version} is ready")
    logger.info("Siril %s set up at %s", release.version, program)
    return program


def _download(release: SirilRelease, report: Callable[[int, str], None]) -> Path:
    """Download the zip into the local cache folder and check it.

    Parameters
    ----------
    release : `SirilRelease`
        Which Siril.
    report : `~collections.abc.Callable`
        Takes a percentage (0 to 90 here) and a message.

    Returns
    -------
    archive : `pathlib.Path`
        The checked zip file. The caller deletes it.

    Raises
    ------
    ExternalServiceError
        If the download fails or does not match the pinned size and checksum.
    """
    if not release.url.startswith("https://"):
        raise ExternalServiceError(f"The Siril download address must use https: {release.url}")
    descriptor, name = tempfile.mkstemp(prefix="siril-", suffix=".zip", dir=scratch_directory())
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
                report(int(90 * received / release.size_bytes), f"Downloading Siril {release.version}")
    except OSError as error:
        archive.unlink(missing_ok=True)
        raise ExternalServiceError(f"The Siril download failed: {error}") from error
    if received != release.size_bytes or digest.hexdigest() != release.sha256:
        archive.unlink(missing_ok=True)
        raise ExternalServiceError(
            f"The Siril download does not match the pinned file ({received} bytes); it was deleted."
        )
    return archive


def _unpack(archive: Path, folder: Path, report: Callable[[int, str], None]) -> None:
    """Unpack the zip into its folder, all at once or not at all.

    Parameters
    ----------
    archive : `pathlib.Path`
        The checked zip.
    folder : `pathlib.Path`
        Where Siril goes. It is replaced only when the unpacking finished.
    report : `~collections.abc.Callable`
        Takes a percentage and a message.

    Raises
    ------
    ExternalServiceError
        If the zip holds a path that would land outside `folder`.
    """
    report(92, "Unpacking Siril")
    partial = folder.with_name(folder.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    with zipfile.ZipFile(archive) as zip_file:
        root = partial.resolve()
        for member in zip_file.namelist():
            if not (root / member).resolve().is_relative_to(root):
                shutil.rmtree(partial, ignore_errors=True)
                raise ExternalServiceError(f"The Siril download holds an unsafe path: {member}")
        zip_file.extractall(partial)
    shutil.rmtree(folder, ignore_errors=True)
    partial.rename(folder)
