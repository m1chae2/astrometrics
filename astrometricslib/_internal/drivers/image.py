"""AstrometricsImage: The primary data container for astronomical images.

Wraps astropy.io.fits, providing standardized access to data and metadata.
"""

import logging
import os

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib._internal.drivers.filter_detection import get_filter_type
from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS, choose_pixel_hdu, collapse_to_2d
from astrometricslib.foundation.enums import FilterType
from astrometricslib.foundation.errors import NotFoundError, StorageError
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)


def read_header_with_fixes(path: str) -> fits.Header | None:
    """Read a frame's header and fix old-style keywords in memory only.

    The header comes from the HDU that holds the image (`choose_pixel_hdu`),
    and the pixels are not read. Some older files use the deprecated
    ``RADECSYS`` keyword or give ``DATE-OBS`` without ``MJD-OBS``. Astropy
    warns about these. This function returns a header with ``RADESYS`` and
    ``MJD-OBS`` filled in. It never writes to the file. A raw frame may sit
    on a network drive that backups and snapshots watch, and a read must
    not change it.

    Parameters
    ----------
    path : `str`
        Path to the FITS file.

    Returns
    -------
    header : `astropy.io.fits.Header` or `None`
        The header, with the fixes applied, so callers can reuse it
        instead of opening the file again. `None` if reading the file
        failed.
    """
    try:
        import warnings

        from astropy.time import Time

        with warnings.catch_warnings():
            # Old files make astropy warn on every open. The fixes below
            # cover what the warnings are about.
            warnings.simplefilter("ignore")
            with fits.open(path, memmap=False) as hdul:
                if len(hdul) == 0:
                    return None
                hdu = choose_pixel_hdu(hdul)
                header = hdu.header.copy()

        if "RADECSYS" in header:
            value = header["RADECSYS"]
            del header["RADECSYS"]
            header["RADESYS"] = value
            header["RADESYSa"] = value

        if "DATE-OBS" in header and "MJD-OBS" not in header:
            try:
                header["MJD-OBS"] = (Time(header["DATE-OBS"]).mjd, "MJD of observation")
            except (ValueError, TypeError) as exc:
                logger.debug("Could not derive MJD-OBS from DATE-OBS: %s", exc)

        return header
    except FITS_READ_ERRORS as e:
        logger.debug("Failed to read FITS header %s: %s", path, e)
        return None


def timestamp_from_header(header: fits.Header) -> float | None:
    """Read the capture time from a header as a Unix timestamp.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The frame's header.

    Returns
    -------
    timestamp : `float` or `None`
        The time in ``DATE-OBS`` (or ``DATE``) in seconds since 1970, or
        `None` if the header gives no date that can be read.
    """
    from astropy.time import Time

    date_text = header.get("DATE-OBS", header.get("DATE", ""))
    if not date_text:
        return None
    try:
        return float(Time(date_text).unix)
    except (ValueError, TypeError) as error:
        logger.debug("Failed to parse DATE-OBS '%s': %s", date_text, error)
        return None


class AstrometricsImage:
    """A unified interface for astronomical image data.

    Handles FITS loading, header extraction, and WCS transformations.
    """

    def __init__(self, path: str) -> None:
        """Initialize the high-level interfaceImage with a path to a FITS file.

        Data is lazy-loaded upon first access.

        Parameters
        ----------
        path : `str`
            Path to the FITS file.
        """
        self.path = path
        self._data: np.ndarray | None = None
        self._header: fits.Header | None = None
        self._wcs: WCS | None = None
        self._loaded = False

    def _read_header_with_fixes(self) -> fits.Header | None:
        """Read the header and fix old-style keywords in memory only.

        See `read_header_with_fixes`, which does the work.

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            The fixed header, or `None` if reading the file failed.
        """
        return read_header_with_fixes(self.path)

    def _load_header(self) -> None:
        """Load the FITS header into `self._header` if not already loaded.

        Raises
        ------
        NotFoundError
            Raised if `self.path` does not exist on disk.
        StorageError
            Raised if the FITS file at `self.path` contains no HDUs.
        FITS_READ_ERRORS
            Any of the errors in `fits_access.FITS_READ_ERRORS`, re-raised
            after logging, if the file cannot be read or its header is
            damaged.
        """
        if self._header is not None:
            return

        if not os.path.exists(self.path):
            logger.error("FITS file not found: %s", self.path)
            raise NotFoundError(f"Image not found at {self.path}")

        try:
            fixed_header = self._read_header_with_fixes()
            if fixed_header is not None:
                self._header = fixed_header
            else:
                with fits.open(self.path, memmap=False) as hdul:
                    if len(hdul) > 0:
                        # Some FITS files have data in hdul[1] if
                        # hdul[0] is just a header
                        hdu = choose_pixel_hdu(hdul)
                        self._header = hdu.header.copy()
                    else:
                        raise StorageError(f"FITS file {self.path} is empty")

            try:
                self._wcs = WCS(self._header)
            except DATA_ERRORS as wcs_err:
                # A solved colour stack carries NAXIS=3 (channel, y, x)
                # alongside a 2-axis WCS, and astropy refuses that
                # combination outright. Retrying at naxis=2 selects the
                # celestial axes, which is the whole of the WCS anyway --
                # a colour channel has no world coordinate. Without this
                # every DSLR stack silently came back with `wcs = None`
                # despite having been solved successfully.
                try:
                    self._wcs = WCS(self._header, naxis=2)
                except DATA_ERRORS:
                    logger.debug("Could not initialize WCS for %s: %s", self.path, wcs_err)
                    self._wcs = None
        except FITS_READ_ERRORS:
            logger.exception("Failed to load FITS header %s", self.path)
            raise

    def _load_data(self) -> None:
        """Load the FITS data into `self._data` if not already loaded."""
        if self._data is not None:
            return

        self._load_header()  # Ensure header/WCS are available if needed

        try:
            with fits.open(self.path, memmap=False) as hdul:
                hdu = choose_pixel_hdu(hdul)
                try:
                    raw_data = hdu.data
                except FITS_READ_ERRORS as read_err:
                    logger.warning("FITS data array corrupted or truncated in %s: %s", self.path, read_err)
                    raw_data = None

                if raw_data is not None:
                    self._data = collapse_to_2d(raw_data.astype(float))
                else:
                    self._data = np.zeros((0, 0))
        except FITS_READ_ERRORS:
            logger.exception("Failed to load FITS data %s", self.path)
            self._data = np.zeros((0, 0))

    @property
    def data(self) -> np.ndarray:
        """`numpy.ndarray`: The image data as a numpy array."""
        self._load_data()
        return self._data

    @property
    def header(self) -> fits.Header:
        """`astropy.io.fits.Header`: The FITS header."""
        self._load_header()
        return self._header

    @property
    def wcs(self) -> WCS | None:
        """`astropy.wcs.WCS` or `None`: The WCS coordinate transform.

        `None` if WCS could not be initialized.
        """
        if self._wcs is not None:
            return self._wcs
        self._load_header()
        return self._wcs

    @wcs.setter
    def wcs(self, value: WCS | None) -> None:
        """Set or override the WCS coordinate transform."""
        self._wcs = value

    @property
    def shape(self) -> tuple[int, int]:
        """`tuple` of `int`: The (height, width) of the image."""
        return self.data.shape

    @property
    def timestamp(self) -> float | None:
        """`float` or `None`: The observation Unix timestamp.

        Extracted from the FITS header (``DATE-OBS`` or ``DATE``).
        `None` if no date could be parsed.
        """
        return timestamp_from_header(self.header)

    @property
    def filter_type(self) -> FilterType:
        """`FilterType`: The filter extracted from the FITS header.

        Read from the ``FILTER`` header using a standard
        string-to-enum mapping. `FilterType.NONE` if no known filter
        name is matched.
        """
        return get_filter_type(self.header)
