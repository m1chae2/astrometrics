"""Purpose: The Gaia XP driver, which fetches and caches Gaia DR3 XP spectra.

Description: `AstroqueryGaiaXpDriver` implements the `GaiaXpDriver`
interface (`drivers/interfaces/gaia_xp_driver.py`) with astroquery's Gaia
archive client.

Downloading a spectrum takes seconds and a spectroscopy run asks for the same
star once per frame, so every answer is kept on disk. The cache follows the
same location rule as the Gaia star catalog cache (`drivers/catalog_store.py`):
it lives in the ``catalogs`` folder of the library's data folder, here in the
sub-folder ``gaia_xp``. There is one compressed NumPy file (``.npz``) per
Gaia source id. Each file holds the wavelengths, the flux, the flux error, the
source id and the date the spectrum was downloaded. A Gaia DR3 spectrum never
changes, so a cached file is never refreshed. A source that the archive says
has no XP spectrum is cached too, as a file with empty arrays, so it is not
asked about again.

The driver never raises into the pipeline. A source with no XP spectrum and a
failed download both give `None`. A failed download (no network, a server
error, a reply that cannot be read, or no answer inside
`DOWNLOAD_TIMEOUT_SECONDS`) is logged once per driver. It is not cached, but
the driver then skips downloads for `RETRY_AFTER_FAILURE_SECONDS`, so a run
with the network down does not wait for a timeout on every star of every
frame.
"""

import logging
import os
import tempfile
import threading
import time
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from astrometricslib.drivers.interfaces.gaia_xp_driver import GaiaXpDriver
from astrometricslib.foundation.config import AppConfiguration, get_configuration
from astrometricslib.utilities.exceptions import ONLINE_QUERY_ERRORS

logger = logging.getLogger(__name__)

# The sub-folder of the library's ``catalogs`` folder that holds the cache.
CACHE_FOLDER_NAME = "gaia_xp"

# How long to wait for one download, in seconds. astroquery's Gaia client has
# no timeout setting for a spectrum download, so the call runs in a helper
# thread and is abandoned after this long. 60 s is a guess: a spectrum is a
# few tens of kilobytes, and the archive answered in a few seconds when this
# driver was written. The abandoned thread ends on its own when the archive
# answers or the connection drops.
DOWNLOAD_TIMEOUT_SECONDS = 60.0

# After a failed download, skip further downloads for this many seconds. A
# designed value: long enough that a run with the network down pays for one
# timeout, short enough that a run started while the archive is back is not
# kept waiting for long.
RETRY_AFTER_FAILURE_SECONDS = 600.0

# Gaia's sampled XP spectra are in nanometres; the library works in Angstroms.
ANGSTROM_PER_NANOMETRE = 10.0

# The fewest samples a downloaded spectrum must have to be believed. Gaia's
# sampled spectra have 343 (336 to 1020 nm in 2 nm steps).
MINIMUM_SAMPLE_COUNT = 10

# The errors a download can raise: the online-query errors, a reply that is
# not a valid zip file, and astroquery's own RuntimeError, KeyError and
# IndexError for a reply with no usable table.
_DOWNLOAD_ERRORS = (*ONLINE_QUERY_ERRORS, zipfile.BadZipFile, RuntimeError, KeyError, IndexError)


def gaia_xp_cache_path(config: AppConfiguration) -> Path:
    """Find where the Gaia XP spectrum cache lives on disk.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings, used to find the library's data folder.

    Returns
    -------
    path : `pathlib.Path`
        The cache folder. It may not exist yet, which only means nothing has
        been cached.
    """
    return config.get_library_path() / "catalogs" / CACHE_FOLDER_NAME


class AstroqueryGaiaXpDriver(GaiaXpDriver):
    """Fetches Gaia DR3 XP spectra with astroquery and caches them on disk.

    Parameters
    ----------
    config : `AppConfiguration`, optional
        The application settings, used to find the cache folder. The current
        settings are loaded when this is left out.
    cache_directory : `pathlib.Path`, optional
        The cache folder to use instead of the one the settings give. Tests
        pass a temporary folder.
    """

    def __init__(self, config: AppConfiguration | None = None, cache_directory: Path | None = None) -> None:
        """Choose the cache folder and set up the failure handling.

        Parameters
        ----------
        config : `AppConfiguration`, optional
            The application settings, used to find the cache folder.
        cache_directory : `pathlib.Path`, optional
            The cache folder to use instead of the one the settings give.
        """
        if cache_directory is not None:
            self.cache_directory = Path(cache_directory)
        else:
            self.cache_directory = gaia_xp_cache_path(config or get_configuration())
        # One download at a time: astroquery writes a temporary folder in the
        # working directory, named from the clock, so two at once could clash.
        self._download_lock = threading.Lock()
        self._failure_logged = False
        self._skip_downloads_until = 0.0

    def cache_file(self, source_id: int) -> Path:
        """Give the path of one source's cache file.

        Parameters
        ----------
        source_id : `int`
            The Gaia DR3 source id.

        Returns
        -------
        path : `pathlib.Path`
            The ``.npz`` file, which may not exist.
        """
        return self.cache_directory / f"gaia_dr3_{int(source_id)}.npz"

    def download_date(self, source_id: int) -> str | None:
        """Say when a source's spectrum was downloaded.

        Parameters
        ----------
        source_id : `int`
            The Gaia DR3 source id.

        Returns
        -------
        downloaded_on : `str` or `None`
            The date as ``YYYY-MM-DD``, or `None` when the source is not in
            the cache.
        """
        try:
            with np.load(self.cache_file(source_id), allow_pickle=False) as stored:
                return str(stored["downloaded_on"])
        except OSError, ValueError, KeyError, zipfile.BadZipFile:
            return None

    def sampled_spectrum(self, source_id: int) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """Give the sampled XP spectrum of one Gaia DR3 source.

        The cache is read first. A source that is not cached is downloaded
        and the answer is cached.

        Parameters
        ----------
        source_id : `int`
            The Gaia DR3 source id.

        Returns
        -------
        spectrum : `tuple` or `None`
            The wavelengths in Angstroms, the flux in W m^-2 nm^-1 and its
            error, or `None` when the source has no XP spectrum or the
            download failed. This method does not raise.
        """
        source_id = int(source_id)
        cached = self._read_cache(source_id)
        if cached is not None:
            return cached if cached[0].size else None
        if time.monotonic() < self._skip_downloads_until:
            return None
        with self._download_lock:
            # A second caller waited on the lock: the first may have cached it.
            cached = self._read_cache(source_id)
            if cached is not None:
                return cached if cached[0].size else None
            try:
                spectrum = self._download(source_id)
            except _DOWNLOAD_ERRORS as download_error:
                self._note_failure(source_id, download_error)
                return None
            self._write_cache(source_id, spectrum)
        return spectrum

    def _read_cache(self, source_id: int) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """Read one source from the cache.

        Parameters
        ----------
        source_id : `int`
            The Gaia DR3 source id.

        Returns
        -------
        spectrum : `tuple` or `None`
            The three arrays (empty arrays for a source known to have no XP
            spectrum), or `None` when the source is not cached or its file
            cannot be read.
        """
        path = self.cache_file(source_id)
        if not path.exists():
            return None
        try:
            with np.load(path, allow_pickle=False) as stored:
                return (
                    np.asarray(stored["wavelength_angstrom"], dtype=float),
                    np.asarray(stored["flux"], dtype=float),
                    np.asarray(stored["flux_error"], dtype=float),
                )
        except OSError, ValueError, KeyError, zipfile.BadZipFile:
            logger.warning("Ignoring an unreadable Gaia XP cache file: %s", path)
            return None

    def _write_cache(
        self, source_id: int, spectrum: tuple[np.ndarray, np.ndarray, np.ndarray] | None
    ) -> None:
        """Save one source to the cache, with today's date.

        The file is written under a temporary name and then renamed, so a
        second process reading the cache never sees half a file.

        Parameters
        ----------
        source_id : `int`
            The Gaia DR3 source id.
        spectrum : `tuple` or `None`
            The three arrays, or `None` to record that the source has no XP
            spectrum.
        """
        wavelength, flux, flux_error = spectrum if spectrum is not None else (np.empty(0),) * 3
        try:
            self.cache_directory.mkdir(parents=True, exist_ok=True)
            handle, temporary_name = tempfile.mkstemp(dir=self.cache_directory, suffix=".tmp")
            with os.fdopen(handle, "wb") as temporary_file:
                np.savez_compressed(
                    temporary_file,
                    wavelength_angstrom=wavelength,
                    flux=flux,
                    flux_error=flux_error,
                    source_id=np.int64(source_id),
                    downloaded_on=np.array(date.today().isoformat()),
                )
            os.replace(temporary_name, self.cache_file(source_id))
        except OSError as write_error:
            logger.warning("Could not cache the Gaia XP spectrum of %s: %s", source_id, write_error)

    def _note_failure(self, source_id: int, download_error: Exception) -> None:
        """Record a failed download: log it once and pause further downloads.

        Parameters
        ----------
        source_id : `int`
            The source whose download failed.
        download_error : `Exception`
            What went wrong.
        """
        self._skip_downloads_until = time.monotonic() + RETRY_AFTER_FAILURE_SECONDS
        if not self._failure_logged:
            self._failure_logged = True
            logger.warning(
                "Gaia XP download failed for source %s (%s); skipping Gaia XP downloads for %.0f s.",
                source_id,
                download_error,
                RETRY_AFTER_FAILURE_SECONDS,
            )

    def _download(self, source_id: int) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """Download one source's sampled XP spectrum from the Gaia archive.

        Parameters
        ----------
        source_id : `int`
            The Gaia DR3 source id.

        Returns
        -------
        spectrum : `tuple` or `None`
            The wavelengths in Angstroms, the flux and its error, or `None`
            when the archive answered that the source has no XP spectrum.

        Raises
        ------
        RuntimeError
            If the archive did not answer within `DOWNLOAD_TIMEOUT_SECONDS`.
            Other errors come straight from astroquery.
        """
        answer: dict[str, Any] = {}

        def fetch() -> None:
            """Ask the archive and keep the answer or the error."""
            try:
                from astroquery.gaia import Gaia

                answer["files"] = Gaia.load_data(
                    ids=[source_id],
                    data_release="Gaia DR3",
                    retrieval_type="XP_SAMPLED",
                    data_structure="INDIVIDUAL",
                    format="csv",
                )
            except _DOWNLOAD_ERRORS as fetch_error:
                answer["error"] = fetch_error

        worker = threading.Thread(target=fetch, name=f"gaia-xp-{source_id}", daemon=True)
        worker.start()
        worker.join(DOWNLOAD_TIMEOUT_SECONDS)
        if worker.is_alive():
            raise RuntimeError(f"no answer from the Gaia archive in {DOWNLOAD_TIMEOUT_SECONDS:g} s")
        if "error" in answer:
            raise answer["error"]
        return _spectrum_from_files(answer.get("files"))


def _spectrum_from_files(files: object) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Turn the archive's reply into three arrays.

    Parameters
    ----------
    files : `object`
        The reply of ``Gaia.load_data``: a dictionary from file name to a list
        of tables. An empty dictionary means the source has no such spectrum.

    Returns
    -------
    spectrum : `tuple` or `None`
        The wavelengths in Angstroms (increasing), the flux and its error,
        with samples that are not finite dropped. `None` when the reply is
        empty.

    Raises
    ------
    ValueError
        If the reply is not a dictionary, or its table lacks the columns
        ``wavelength``, ``flux`` and ``flux_error``, or has too few samples.
    """
    if not isinstance(files, dict):
        raise ValueError("the Gaia archive reply is not a dictionary of files")
    if not files:
        return None
    tables = next(iter(files.values()))
    table = tables[0]
    try:
        wavelength = np.asarray(table["wavelength"], dtype=float) * ANGSTROM_PER_NANOMETRE
        flux = np.asarray(table["flux"], dtype=float)
        flux_error = np.asarray(table["flux_error"], dtype=float)
    except (KeyError, TypeError) as column_error:
        raise ValueError("the Gaia XP table lacks a wavelength, flux or flux_error column") from column_error
    usable = np.isfinite(wavelength) & np.isfinite(flux) & np.isfinite(flux_error)
    if usable.sum() < MINIMUM_SAMPLE_COUNT:
        raise ValueError("the Gaia XP table has too few usable samples")
    order = np.argsort(wavelength[usable])
    return wavelength[usable][order], flux[usable][order], flux_error[usable][order]
