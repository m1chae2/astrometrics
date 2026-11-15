"""Read the stored spectra and star positions that the spectroscopy tuners use.

Several spectroscopy tuners (star registration, the standard-star limit, the
emission-line detector and the absorption-feature detector) start from the
same stored data: the spectral stacks of the library's targets and the stars
with recorded spectra. This module reads that data once, in one shape, so the
tuners do not each repeat the reading.

A spectral stack belongs to a setup when its FITS header names the setup's
camera. Nothing here changes the library; it only reads.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from astrometricslib._internal.drivers.camera_profile_store import camera_identity
from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS, read_header
from astrometricslib.models.stellar_source import StellarObject

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpectralTargetStars:
    """One target's spectral stack and its stars.

    Attributes
    ----------
    target_id : `str`
        The target's id.
    stack_path : `str`
        The path of the target's spectral stack.
    image_shape : `tuple` [`int`, `int`]
        The stack's ``(height, width)`` in pixels.
    stars : `list` [`StellarObject`]
        Every star the library holds for the target.
    target : `Any`
        The target record itself.
    """

    target_id: str
    stack_path: str
    image_shape: tuple[int, int]
    stars: list[StellarObject]
    target: Any


@dataclass(frozen=True)
class StoredSpectrum:
    """One stored spectrum, with what a detector needs to run on it.

    Attributes
    ----------
    star_id : `str`
        The star's id.
    target_id : `str`
        The target the spectrum was read for.
    wavelength_angstrom : `numpy.ndarray`
        The wavelength grid, in increasing order, finite values only.
    intensity : `numpy.ndarray`
        The brightness at each wavelength.
    resolution_element_angstrom : `float` or `None`
        How much the instrument blurred the spectrum, when it was recorded.
    extraction_half_width_px : `float` or `None`
        How far the extraction box reached either side of the trail, in
        pixels, when it was recorded.
    is_emission_line_source : `bool`
        Whether the library called the spectrum an emission-line source.
    """

    star_id: str
    target_id: str
    wavelength_angstrom: np.ndarray
    intensity: np.ndarray
    resolution_element_angstrom: float | None
    extraction_half_width_px: float | None
    is_emission_line_source: bool

    @property
    def sample_spacing_angstrom(self) -> float:
        """Give the typical wavelength step between samples.

        Returns
        -------
        spacing : `float`
            The median step, in Angstroms.
        """
        return float(np.median(np.diff(self.wavelength_angstrom)))


def spectral_target_stars(
    astrometrics: Any, camera_name: str, configuration: Any = None
) -> list[SpectralTargetStars]:
    """Read every target of one camera that has a spectral stack.

    Parameters
    ----------
    astrometrics : `astrometricslib.Astrometrics`
        The library.
    camera_name : `str`
        The setup's camera, in any spelling.
    configuration : `Any`, optional
        The loaded config, which lets camera aliases count.

    Returns
    -------
    targets : `list` [`SpectralTargetStars`]
        One entry per target whose spectral stack exists, was taken with the
        camera and holds at least one star.
    """
    wanted_camera = camera_identity(camera_name, configuration)
    found: list[SpectralTargetStars] = []
    for target in astrometrics.targets.list():
        stack_path = target.spectral_stacking.stacked_image
        if not stack_path:
            continue
        try:
            header = read_header(stack_path)
        except FITS_READ_ERRORS, OSError:
            continue
        header_camera = header.get("INSTRUME")
        if not header_camera or camera_identity(str(header_camera), configuration) != wanted_camera:
            continue
        height, width = header.get("NAXIS2"), header.get("NAXIS1")
        if not height or not width:
            continue
        stars = astrometrics.targets.get_stars(target)
        if stars:
            found.append(SpectralTargetStars(target.id, stack_path, (int(height), int(width)), stars, target))
    return found


def stored_spectra(targets: Sequence[SpectralTargetStars]) -> list[StoredSpectrum]:
    """Collect the stored spectra of the stars of some targets.

    Parameters
    ----------
    targets : `Sequence` [`SpectralTargetStars`]
        The targets from `spectral_target_stars`.

    Returns
    -------
    spectra : `list` [`StoredSpectrum`]
        One entry per star with a spectrum of at least two finite samples.
        A star that belongs to several targets is listed once.
    """
    spectra: list[StoredSpectrum] = []
    seen: set[str] = set()
    for entry in targets:
        for star in entry.stars:
            if not star.has_spectra or star.id in seen:
                continue
            record = star.spectroscopy
            wavelength = np.asarray(record.wavelengths_angstrom, dtype=float)
            intensity = np.asarray(record.intensities, dtype=float)
            if wavelength.size != intensity.size:
                continue
            valid = np.isfinite(wavelength) & np.isfinite(intensity)
            if valid.sum() < 2:
                continue
            order = np.argsort(wavelength[valid])
            diagnostics = record.extraction_diagnostics
            half_width = None
            if diagnostics is not None and diagnostics.aperture_half_width_median_px is not None:
                half_width = float(diagnostics.aperture_half_width_median_px)
            elif record.extraction_radius is not None:
                half_width = float(record.extraction_radius)
            seen.add(star.id)
            spectra.append(
                StoredSpectrum(
                    star_id=star.id,
                    target_id=entry.target_id,
                    wavelength_angstrom=wavelength[valid][order],
                    intensity=intensity[valid][order],
                    resolution_element_angstrom=record.resolution_element_angstrom,
                    extraction_half_width_px=half_width,
                    is_emission_line_source=bool(record.is_emission_line_source),
                )
            )
    return spectra


def quantile_of(values: Sequence[float], fraction: float) -> float:
    """Take a quantile of a list of numbers.

    Parameters
    ----------
    values : `Sequence` [`float`]
        The numbers; at least one.
    fraction : `float`
        The quantile, from 0 to 1.

    Returns
    -------
    value : `float`
        The quantile, interpolated linearly.
    """
    return float(np.quantile(np.asarray(values, dtype=float), fraction))


__all__ = [
    "SpectralTargetStars",
    "StoredSpectrum",
    "quantile_of",
    "spectral_target_stars",
    "stored_spectra",
]
