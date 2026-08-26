"""AstrometricsImage: The primary data container for astronomical images.

Wraps astropy.io.fits, providing standardized access to data and metadata.
"""

import logging
import os

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from .enums import FilterType

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

    def _auto_repair_fits_if_needed(self) -> fits.Header | None:
        """Repair deprecated headers or extra padding in the FITS file.

        If any deprecated headers (like RADECSYS) or missing MJD-OBS
        keywords, or extra padding warnings are detected, the file is
        overwritten with a clean FITS structure to fix these issues.

        Returns
        -------
        header : `astropy.io.fits.Header` or `None`
            The header read during the detection pass (repaired, if a
            repair was performed) so callers can reuse it instead of
            reopening the file themselves, or `None` if reading the
            file failed.
        """
        try:
            import warnings

            from astropy.time import Time

            needs_repair = False
            detected_header = None

            # Catch warnings to detect unexpected extra padding or
            # other FITS/WCS warnings
            with warnings.catch_warnings(record=True) as caught_warnings:
                warnings.simplefilter("always")
                with fits.open(self.path, memmap=False) as hdul:
                    if len(hdul) > 0:
                        hdu = hdul[1] if hdul[0].data is None and len(hdul) > 1 else hdul[0]
                        header = hdu.header
                        detected_header = header.copy()
                        if "RADECSYS" in header:
                            needs_repair = True
                        if "DATE-OBS" in header and "MJD-OBS" not in header:
                            needs_repair = True

                if caught_warnings:
                    for w in caught_warnings:
                        msg = str(w.message).lower()
                        if "extra padding" in msg or "fitsfixedwarning" in w.category.__name__.lower():
                            needs_repair = True
                            break

            if needs_repair:
                logger.info(f"Auto-repairing FITS file to fix deprecated headers/padding: {self.path}")
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    with fits.open(self.path, memmap=False) as hdul:
                        idx = 1 if len(hdul) > 1 and hdul[0].data is None else 0
                        data = hdul[idx].data
                        header = hdul[idx].header.copy()

                        # Fix RADECSYS
                        if "RADECSYS" in header:
                            val = header["RADECSYS"]
                            del header["RADECSYS"]
                            header["RADESYS"] = val
                            header["RADESYSa"] = val

                        # Fix DATE-OBS / MJD-OBS
                        if "DATE-OBS" in header and "MJD-OBS" not in header:
                            try:
                                t = Time(header["DATE-OBS"])
                                header["MJD-OBS"] = (t.mjd, "MJD of observation")
                            except Exception as exc:
                                logger.debug("Could not derive MJD-OBS from DATE-OBS: %s", exc)

                    # Rewrite the file back cleanly only after the
                    # read handle above is closed
                    fits.writeto(self.path, data, header, overwrite=True)
                    detected_header = header
                    del data

            return detected_header
        except Exception as e:
            logger.debug(f"Failed to auto-repair FITS file {self.path}: {e}")
            return None

    def _load_header(self):  # ruff: ignore[missing-return-type-private-function]
        """Load the FITS header into `self._header` if not already loaded.

        Raises
        ------
        FileNotFoundError
            Raised if `self.path` does not exist on disk.
        ValueError
            Raised if the FITS file at `self.path` contains no HDUs.
        """
        if self._header is not None:
            return

        if not os.path.exists(self.path):
            logger.error(f"FITS file not found: {self.path}")
            raise FileNotFoundError(f"Image not found at {self.path}")

        try:
            repaired_header = self._auto_repair_fits_if_needed()
            if repaired_header is not None:
                self._header = repaired_header
            else:
                with fits.open(self.path, memmap=False) as hdul:
                    if len(hdul) > 0:
                        # Some FITS files have data in hdul[1] if
                        # hdul[0] is just a header
                        hdu = hdul[1] if hdul[0].data is None and len(hdul) > 1 else hdul[0]
                        self._header = hdu.header.copy()
                    else:
                        raise ValueError(f"FITS file {self.path} is empty")

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
                    logger.debug(f"Could not initialize WCS for {self.path}: {wcs_err}")
                    self._wcs = None
        except Exception as e:
            logger.error(f"Failed to load FITS header {self.path}: {e}")
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
                    logger.warning(f"FITS data array corrupted or truncated in {self.path}: {read_err}")
                    raw_data = None

                if raw_data is not None:
                    data = raw_data.astype(float)
                    if data.ndim == 3:
                        if data.shape[0] in [1, 3, 4]:
                            data = np.mean(data, axis=0)
                        else:
                            data = np.mean(data, axis=-1)
                    self._data = data
                else:
                    self._data = np.zeros((0, 0))
        except Exception as e:
            logger.error(f"Failed to load FITS data {self.path}: {e}")
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
            logger.debug(f"Failed to parse DATE-OBS '{date_str}': {e}")
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
