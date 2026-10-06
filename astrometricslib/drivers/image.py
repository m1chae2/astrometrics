"""AstrometricsImage: The primary data container for astronomical images.

Wraps astropy.io.fits, providing standardized access to data and metadata.
"""

import logging
import os

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib.drivers.fits_access import collapse_to_2d
from astrometricslib.foundation.enums import FilterType
from astrometricslib.foundation.errors import NotFoundError, StorageError

logger = logging.getLogger(__name__)


class AstrometricsImage:
    """A unified interface for astronomical image data.

    Handles FITS loading, header extraction, and WCS transformations.
    """

    def __init__(self, path: str):  # ruff: ignore[missing-return-type-special-method]
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

        Some older files use the deprecated ``RADECSYS`` keyword or give
        ``DATE-OBS`` without ``MJD-OBS``. Astropy warns about these. This
        method returns a header with ``RADESYS`` and ``MJD-OBS`` filled in.
        It never writes to the file. A raw frame may sit on a network drive
        that backups and snapshots watch, and a read must not change it.

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
                with fits.open(self.path, memmap=False) as hdul:
                    if len(hdul) == 0:
                        return None
                    hdu = hdul[1] if hdul[0].data is None and len(hdul) > 1 else hdul[0]
                    header = hdu.header.copy()

            if "RADECSYS" in header:
                value = header["RADECSYS"]
                del header["RADECSYS"]
                header["RADESYS"] = value
                header["RADESYSa"] = value

            if "DATE-OBS" in header and "MJD-OBS" not in header:
                try:
                    header["MJD-OBS"] = (Time(header["DATE-OBS"]).mjd, "MJD of observation")
                except Exception as exc:
                    logger.debug("Could not derive MJD-OBS from DATE-OBS: %s", exc)

            return header
        except Exception as e:
            logger.debug("Failed to read FITS header %s: %s", self.path, e)
            return None

    def _load_header(self) -> None:
        """Load the FITS header into `self._header` if not already loaded.

        Raises
        ------
        NotFoundError
            Raised if `self.path` does not exist on disk.
        StorageError
            Raised if the FITS file at `self.path` contains no HDUs.
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
                        hdu = hdul[1] if hdul[0].data is None and len(hdul) > 1 else hdul[0]
                        self._header = hdu.header.copy()
                    else:
                        raise StorageError(f"FITS file {self.path} is empty")

            try:
                self._wcs = WCS(self._header)
            except Exception as wcs_err:
                # A solved colour stack carries NAXIS=3 (channel, y, x)
                # alongside a 2-axis WCS, and astropy refuses that
                # combination outright. Retrying at naxis=2 selects the
                # celestial axes, which is the whole of the WCS anyway --
                # a colour channel has no world coordinate. Without this
                # every DSLR stack silently came back with `wcs = None`
                # despite having been solved successfully.
                try:
                    self._wcs = WCS(self._header, naxis=2)
                except Exception:
                    logger.debug("Could not initialize WCS for %s: %s", self.path, wcs_err)
                    self._wcs = None
        except Exception as e:
            logger.error("Failed to load FITS header %s: %s", self.path, e)
            raise

    def _load_data(self):  # ruff: ignore[missing-return-type-private-function]
        """Load the FITS data into `self._data` if not already loaded."""
        if self._data is not None:
            return

        self._load_header()  # Ensure header/WCS are available if needed

        try:
            with fits.open(self.path, memmap=False) as hdul:
                hdu = hdul[1] if hdul[0].data is None and len(hdul) > 1 else hdul[0]
                try:
                    raw_data = hdu.data
                except Exception as read_err:
                    logger.warning("FITS data array corrupted or truncated in %s: %s", self.path, read_err)
                    raw_data = None

                if raw_data is not None:
                    self._data = collapse_to_2d(raw_data.astype(float))
                else:
                    self._data = np.zeros((0, 0))
        except Exception as e:
            logger.error("Failed to load FITS data %s: %s", self.path, e)
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
        from astropy.time import Time

        header = self.header
        date_str = header.get("DATE-OBS", header.get("DATE", ""))
        if not date_str:
            return None

        try:
            t = Time(date_str)
            return float(t.unix)
        except Exception as e:
            logger.debug("Failed to parse DATE-OBS '%s': %s", date_str, e)
            return None

    @property
    def filter_type(self) -> FilterType:
        """`FilterType`: The filter extracted from the FITS header.

        Read from the ``FILTER`` header using a standard
        string-to-enum mapping. `FilterType.NONE` if no known filter
        name is matched.
        """
        header = self.header
        filter_str = str(header.get("FILTER", "")).upper()

        mapping = {
            "SPECTROSCOPY": FilterType.SPEC,
            "SPEC": FilterType.SPEC,
            "H-ALPHA": FilterType.Ha,
            "HA": FilterType.Ha,
            "OIII": FilterType.OIII,
            "SII": FilterType.SII,
            "LUMINANCE": FilterType.L,
            "RED": FilterType.R,
            "GREEN": FilterType.G,
            "BLUE": FilterType.B,
            "L": FilterType.L,
            "R": FilterType.R,
            "G": FilterType.G,
            "B": FilterType.B,
            "NONE": FilterType.NONE,
        }

        # Sort keys by length descending to match most specific terms first
        sorted_keys = sorted(mapping.keys(), key=len, reverse=True)
        for key in sorted_keys:
            if key in filter_str:
                return mapping[key]

        return FilterType.NONE

    def get_pixel_coords(self, ra: float, dec: float) -> tuple[float, float] | None:
        """Convert world coordinates (RA, Dec) to pixel coordinates.

        Parameters
        ----------
        ra : `float`
            Right ascension in degrees.
        dec : `float`
            Declination in degrees.

        Returns
        -------
        pixel_coords : `tuple` of `float`, or `None`
            The (x, y) pixel coordinates, or `None` if this image has
            no WCS solution or the conversion failed.
        """
        if self.wcs:
            try:
                res = self.wcs.wcs_world2pix(ra, dec, 0)
                return float(res[0]), float(res[1])
            except Exception:
                return None
        return None

    def get_world_coords(self, x: float, y: float) -> tuple[float, float] | None:
        """Convert pixel coordinates (X, Y) to world coordinates (RA, Dec).

        Parameters
        ----------
        x : `float`
            Pixel X coordinate.
        y : `float`
            Pixel Y coordinate.

        Returns
        -------
        world_coords : `tuple` of `float`, or `None`
            The (ra, dec) world coordinates in degrees, or `None` if
            this image has no WCS solution or the conversion failed.
        """
        if self.wcs:
            try:
                res = self.wcs.wcs_pix2world(x, y, 0)
                return float(res[0]), float(res[1])
            except Exception:
                return None
        return None
