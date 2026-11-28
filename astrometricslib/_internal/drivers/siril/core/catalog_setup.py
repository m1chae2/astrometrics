"""Purpose: Set up Siril's Gaia astrometry catalog, checked against a pin.

Description: Siril's plate solver (`siril.astrometry.plate_solve`) reads the
Gaia stars from one file on the disk, ``siril_cat_healpix8_astro.dat``
(about 1.5 GB). Siril's own catalog installer makes the same file. This module
makes it from the pinned release: it downloads the compressed file from
Zenodo (the record Siril's installer uses), decompresses it as it arrives,
and checks the size and the SHA-256 checksum of the decompressed file against
the recorded ones. A file that does not match is refused and deleted, and the
catalog file is only put in place when it matches. The file goes in the folder
that `find_local_gaia_catalog` looks in first, so the solver finds it without
a setting.

`check_catalog` reports, without changing anything, whether the catalog is
installed and matches the pin. `set_up_catalog` installs it. They are used by
``python -m astrometricslib.scripts.set_up_siril_catalog`` (``--check`` for
the first), which ``build/linux/setup_siril_catalog.sh`` and the Windows setup
run. This module never starts Siril.

To move to a newer catalog, change `GAIA_ASTROMETRY_CATALOG` (version, address,
size and checksum) together.
"""

import bz2
import hashlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from astrometricslib._internal.drivers import outside_services
from astrometricslib._internal.drivers.siril.core.program import find_local_gaia_catalog, setup_catalog_folder
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.foundation.offline import require_online

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CatalogRelease:
    """One pinned download of a star catalog.

    Attributes
    ----------
    name : `str`
        The catalog's name, for messages.
    version : `str`
        The pinned version.
    url : `str`
        Where the bzip2-compressed file is downloaded from (https).
    file_name : `str`
        The name of the decompressed file.
    size_bytes : `int`
        The exact size of the decompressed file.
    sha256 : `str`
        The SHA-256 checksum of the decompressed file.
    """

    name: str
    version: str
    url: str
    file_name: str
    size_bytes: int
    sha256: str


GAIA_ASTROMETRY_CATALOG = CatalogRelease(
    name="Siril Gaia DR3 astrometry catalog",
    version="1.0.0",
    url="https://zenodo.org/records/14692304/files/siril_cat_healpix8_astro.dat.bz2",
    file_name="siril_cat_healpix8_astro.dat",
    size_bytes=1_521_132_640,
    sha256="2fa40c93fe115235d35c5050757f2ef60a326a6f3030f87be1598c016fcb2388",
)
"""The catalog Siril's plate solver reads.

Pinned on 2026-11-28 from Zenodo record 14692304 (the one Siril's own catalog
installer downloads). The size and checksum are those of the decompressed
file that solved the real frames of register entry AST-23.
"""

DOWNLOAD_CHUNK_BYTES = 1024 * 1024
"""How much of the download is read and decompressed at a time."""

DOWNLOAD_TIMEOUT_SECONDS = 60
"""Longest wait for the server on one read."""


@dataclass(frozen=True)
class CatalogStatus:
    """What `check_catalog` found.

    Attributes
    ----------
    path : `str` or `None`
        The catalog file, or `None` if none is installed.
    size_matches : `bool`
        Whether the file has the pinned size.
    checksum_matches : `bool`
        Whether the file has the pinned SHA-256 checksum. Always `False` when
        the size differs: the checksum is then not computed.
    """

    path: str | None
    size_matches: bool
    checksum_matches: bool

    @property
    def ready(self) -> bool:
        """Say whether the catalog is installed and matches the pin.

        Returns
        -------
        ready : `bool`
            `True` when the size and the checksum both match.
        """
        return self.path is not None and self.size_matches and self.checksum_matches


def file_sha256(path: str) -> str:
    """Compute the SHA-256 checksum of a file.

    Parameters
    ----------
    path : `str`
        The file. It is only read.

    Returns
    -------
    checksum : `str`
        The checksum, in lower-case hexadecimal.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while chunk := source.read(DOWNLOAD_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def check_catalog(
    release: CatalogRelease = GAIA_ASTROMETRY_CATALOG, search_folders: list[str] | None = None
) -> CatalogStatus:
    """Report whether the catalog is installed and matches the pin.

    Nothing is changed or downloaded. The checksum needs a read of the whole
    file, which takes a few seconds.

    Parameters
    ----------
    release : `CatalogRelease`, optional
        Which catalog. Default `GAIA_ASTROMETRY_CATALOG`.
    search_folders : `list` [`str`], optional
        Folders to look in, as for `find_local_gaia_catalog`.

    Returns
    -------
    status : `CatalogStatus`
        What was found.
    """
    path = find_local_gaia_catalog(search_folders)
    if path is None:
        return CatalogStatus(None, False, False)
    if os.path.getsize(path) != release.size_bytes:
        return CatalogStatus(path, False, False)
    return CatalogStatus(path, True, file_sha256(path) == release.sha256)


def _ignore_progress(percent: int, message: str) -> None:
    """Take a progress report and do nothing with it.

    Parameters
    ----------
    percent : `int`
        How far the work is.
    message : `str`
        What it is doing.
    """


def set_up_catalog(
    release: CatalogRelease = GAIA_ASTROMETRY_CATALOG,
    folder: str | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> Path:
    """Download the pinned catalog, check it and put it in place.

    Nothing is downloaded when the catalog is already installed and matches
    the pin.

    Parameters
    ----------
    release : `CatalogRelease`, optional
        Which catalog. Default `GAIA_ASTROMETRY_CATALOG`.
    folder : `str`, optional
        Where to put the file. Default `setup_catalog_folder`.
    progress : `~collections.abc.Callable`, optional
        Called with a percentage and a short message as the work goes on.

    Returns
    -------
    catalog : `pathlib.Path`
        The catalog file now in use.

    Raises
    ------
    ExternalServiceError
        If the address is not https, the download fails, or the decompressed
        file does not match the pinned size and checksum (it is deleted and
        no catalog is put in place).
    """
    report = progress or _ignore_progress
    target_folder = Path(folder if folder is not None else setup_catalog_folder())
    target = target_folder / release.file_name
    status = check_catalog(release, [str(target_folder)] if folder is not None else None)
    if status.ready and status.path is not None:
        report(100, f"{release.name} is ready")
        return Path(status.path)
    if not release.url.startswith("https://"):
        raise ExternalServiceError(f"The {release.name} download address must use https: {release.url}")
    require_online(f"the {release.name} download")
    target_folder.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    digest = hashlib.sha256()
    written = 0
    decompressor = bz2.BZ2Decompressor()
    try:
        with (
            outside_services.open_download(release.url, timeout_seconds=DOWNLOAD_TIMEOUT_SECONDS) as response,
            partial.open("wb") as output,
        ):
            while chunk := response.read(DOWNLOAD_CHUNK_BYTES):
                data = decompressor.decompress(chunk)
                output.write(data)
                digest.update(data)
                written += len(data)
                if written > release.size_bytes:
                    break
                report(int(95 * written / release.size_bytes), f"Downloading {release.name}")
    except (OSError, EOFError) as error:
        partial.unlink(missing_ok=True)
        raise ExternalServiceError(f"The {release.name} download failed: {error}") from error
    if written != release.size_bytes or digest.hexdigest() != release.sha256:
        partial.unlink(missing_ok=True)
        raise ExternalServiceError(
            f"The {release.name} download does not match the pinned file ({written} bytes); it was deleted."
        )
    os.replace(partial, target)
    report(100, f"{release.name} is ready")
    logger.info("%s set up at %s", release.name, target)
    return target
