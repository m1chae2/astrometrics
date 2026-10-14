"""Purpose: Estimate a star's spectral type from a few line strengths.

Description: The template classifier (`spectral_classifier`) compares the
whole shape of a spectrum with reference spectra. At the resolution of a
slitless grism (about 100 to 150 Angstroms in the green and red) that shape
is mostly the continuum slope, and the slope mixes the star's temperature
with interstellar reddening and with small errors in the instrument response.

This module makes an independent estimate that ignores the slope. It measures
the depth of a few lines and bands against the continuum right beside each
one. The continuum is the same local quadratic curve the absorption-feature
detector uses (see `spectral_feature_detector.measure_local_dip_depth`), so a
smooth tilt of the spectrum cancels out of every index.

The indices, with the wavelength window of each core (the wavelengths averaged
to get the depth):

* ``h_beta``: H-beta at 4861 A, core 4836-4886 A or wider (see below).
  Strongest at A0, weaker toward both hotter and cooler stars.
* ``h_alpha``: H-alpha at 6563 A, core 6538-6588 A or wider. The same shape
  as H-beta. It turns negative in late K and M stars, where molecular bands
  in the neighbouring continuum make the line look like a bump.
* ``mg_b``: the Mg b triplet at 5175 A, core 5155-5195 A or wider. Rises
  through G and K stars.
* ``na_d``: the Na D doublet at 5893 A, core 5873-5913 A or wider. Rises
  through K and M stars.
* ``tio_6200``: the titanium oxide band at 6150-6250 A. Appears in M stars.
* ``tio_7100``: the titanium oxide band at 7050-7150 A. Appears in M stars.

The four line cores are widened to half of the instrument's resolution
element on each side when that is more than the listed half-width, so the core
covers the whole blurred line. The two band cores keep their listed width.
Samples inside the atmospheric absorption bands (see `atmospheric_mask`) and
inside caller-supplied windows (for example emission lines) are left out. An
index whose core touches one of those windows is not measured.

The same indices are measured on every bundled main-sequence reference
spectrum, blurred to the instrument's resolution. A star's type is the
reference nearest to it in index space, where each index is divided by its
spread (standard deviation) across the references so that no one index
dominates.

This is a cross-check. It does not replace the template classifier's
decision. At this resolution neighbouring subtypes have nearly the same
indices, so the estimate is good to a few subtypes at best, and a hot B star
and a mid F star can look alike in lines alone. The measured accuracy is in
`test/processing/test_spectral_line_indices.py`.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from astrometricslib.models.spectral_cross_checks import LineIndexClassification
from astrometricslib.models.stellar_source import ladder_steps_between
from astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_mask import (
    ATMOSPHERIC_BANDS_ANGSTROM,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
    ResolutionProfile,
)
from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import (
    REFERENCE_SPECTRAL_TYPES,
    blurred_reference_spectrum,
)
from astrometricslib.pipelines.spectroscopy.processing.spectral_feature_detector import (
    measure_local_dip_depth,
)

__all__ = [
    "LINE_INDICES",
    "LineIndex",
    "classify_by_line_indices",
    "measure_line_indices",
    "reference_index_table",
]


@dataclass(frozen=True)
class LineIndex:
    """One line or band whose depth is used as an index.

    Attributes
    ----------
    name : `str`
        A short snake_case name, unique among the indices.
    center_angstrom : `float`
        The wavelength of the line or band centre, in Angstroms.
    half_width_angstrom : `float`
        The least half-width of the core, in Angstroms.
    widens_with_resolution : `bool`
        Whether the core is widened to half the resolution element on each
        side when that is larger. `True` for the lines, `False` for the bands.
    """

    name: str
    center_angstrom: float
    half_width_angstrom: float
    widens_with_resolution: bool


# The half-widths of the four lines are the ones the feature detector uses
# (see `spectral_feature_detector.NAMED_FEATURES`). The band cores are the
# 6150-6250 A and 7050-7150 A windows of the titanium oxide bands.
LINE_INDICES: tuple[LineIndex, ...] = (
    LineIndex("h_beta", 4861.0, 25.0, True),
    LineIndex("h_alpha", 6563.0, 25.0, True),
    LineIndex("mg_b", 5175.0, 20.0, True),
    LineIndex("na_d", 5893.0, 20.0, True),
    LineIndex("tio_6200", 6200.0, 50.0, False),
    LineIndex("tio_7100", 7100.0, 50.0, False),
)

# The core of a line index is at least this fraction of the resolution
# element wide on each side. 0.5 makes the full core one resolution element
# across, which covers the whole blurred line. Chosen by judgement: on the
# bundled references blurred to the stored line-spread profile, it raised the
# leave-one-out share within two subtypes from 41% (listed half-widths only)
# to 59%. It was compared with 0, 0.25, 0.75 and 1.0 on that one measure.
CORE_RESOLUTION_FRACTION = 0.5

# The fewest indices that must be measurable for a type to be named. Chosen
# by judgement: with two, a Balmer pair alone cannot tell the hot side of A
# from the cool side.
MINIMUM_INDEX_COUNT = 3

# The reference tables already built, by blur. The key is the resolution
# element rounded to a whole Angstrom, or the profile's wavelengths and
# values when a profile is used.
_table_cache: dict[tuple, tuple[tuple[str, ...], np.ndarray]] = {}


def _resolution_at(
    wavelength_angstrom: float, resolution_element_angstrom: float, profile: ResolutionProfile | None
) -> float:
    """Give the resolution element at one wavelength.

    Parameters
    ----------
    wavelength_angstrom : `float`
        The wavelength, in Angstroms.
    resolution_element_angstrom : `float`
        The single resolution element, used when there is no profile.
    profile : `ResolutionProfile`, optional
        The resolution element along the spectrum.

    Returns
    -------
    resolution_element : `float`
        The resolution element at that wavelength, in Angstroms.
    """
    if profile is None:
        return resolution_element_angstrom
    return float(profile.at(np.array([wavelength_angstrom]))[0])


def measure_line_indices(
    wavelength_angstrom: np.ndarray,
    flux: np.ndarray,
    resolution_element_angstrom: float = FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
    resolution_profile: ResolutionProfile | None = None,
    excluded_windows_angstrom: Sequence[tuple[float, float]] = (),
    exclude_atmospheric_bands: bool = True,
) -> dict[str, float | None]:
    """Measure every line index of a spectrum.

    Parameters
    ----------
    wavelength_angstrom : `np.ndarray`
        The spectrum's wavelengths, in Angstroms.
    flux : `np.ndarray`
        The spectrum's brightness. Only its shape matters, so any units work.
    resolution_element_angstrom : `float`, optional
        The instrument's resolution element, in Angstroms. It sets how far
        the continuum bands start from each line. Used for every wavelength
        when there is no `resolution_profile`.
    resolution_profile : `ResolutionProfile`, optional
        The resolution element along the spectrum. When given, each index uses
        the value at its own wavelength.
    excluded_windows_angstrom : `Sequence`, optional
        Wavelength windows, each a (low, high) pair, to leave out. An index
        whose core overlaps a window is not measured.
    exclude_atmospheric_bands : `bool`, optional
        Leave out the wavelengths where Earth's air absorbs light.

    Returns
    -------
    indices : `dict` [`str`, `float` or `None`]
        The depth of each index in `LINE_INDICES`, as a fraction of the local
        continuum (0.1 means 10% dimmer; negative means a bump). `None` for an
        index that could not be measured.
    """
    wavelengths = np.asarray(wavelength_angstrom, dtype=float)
    fluxes = np.asarray(flux, dtype=float)
    usable = np.isfinite(wavelengths) & np.isfinite(fluxes) & (fluxes > 0)
    wavelengths, fluxes = wavelengths[usable], fluxes[usable]
    order = np.argsort(wavelengths)
    wavelengths, fluxes = wavelengths[order], fluxes[order]

    left_out_windows = [(float(low), float(high)) for low, high in excluded_windows_angstrom]
    if exclude_atmospheric_bands:
        left_out_windows += [(float(start), float(end)) for _name, start, end in ATMOSPHERIC_BANDS_ANGSTROM]
    keep = np.ones(wavelengths.shape, dtype=bool)
    for low, high in left_out_windows:
        keep &= (wavelengths < low) | (wavelengths > high)
    wavelengths, fluxes = wavelengths[keep], fluxes[keep]

    indices: dict[str, float | None] = {}
    for line in LINE_INDICES:
        resolution = _resolution_at(line.center_angstrom, resolution_element_angstrom, resolution_profile)
        half_width = line.half_width_angstrom
        if line.widens_with_resolution:
            half_width = max(half_width, CORE_RESOLUTION_FRACTION * resolution)
        core_low, core_high = line.center_angstrom - half_width, line.center_angstrom + half_width
        core_is_blocked = any(low <= core_high and high >= core_low for low, high in left_out_windows)
        indices[line.name] = (
            None
            if core_is_blocked or wavelengths.size == 0
            else measure_local_dip_depth(wavelengths, fluxes, line.center_angstrom, half_width, resolution)
        )
    return indices


def reference_index_table(
    resolution_element_angstrom: float = FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
    resolution_profile: ResolutionProfile | None = None,
) -> tuple[tuple[str, ...], np.ndarray]:
    """Measure the line indices of every main-sequence reference spectrum.

    Each reference is blurred to the instrument's resolution first, then
    measured the same way as an observed spectrum.

    Parameters
    ----------
    resolution_element_angstrom : `float`, optional
        The resolution element to blur to, in Angstroms.
    resolution_profile : `ResolutionProfile`, optional
        How the resolution element changes with wavelength. Used instead of
        the single width when given.

    Returns
    -------
    types, table : `tuple` [`tuple` [`str`], `np.ndarray`]
        The reference labels, in the order of `REFERENCE_SPECTRAL_TYPES`, and
        an array with one row per reference and one column per index in
        `LINE_INDICES`. An index that could not be measured is NaN.
    """
    if resolution_profile is not None:
        key: tuple = (
            "profile",
            tuple(np.round(resolution_profile.wavelength_angstrom, 1)),
            tuple(np.round(resolution_profile.resolution_element_angstrom, 1)),
        )
    else:
        key = ("single", round(resolution_element_angstrom))
    if key not in _table_cache:
        rows = []
        for spectral_type in REFERENCE_SPECTRAL_TYPES:
            wavelength, flux = blurred_reference_spectrum(
                spectral_type, resolution_element_angstrom, resolution_profile
            )
            measured = measure_line_indices(wavelength, flux, resolution_element_angstrom, resolution_profile)
            rows.append([np.nan if value is None else value for value in measured.values()])
        _table_cache[key] = (REFERENCE_SPECTRAL_TYPES, np.array(rows, dtype=float))
    return _table_cache[key]


def classify_by_line_indices(
    wavelength_angstrom: np.ndarray,
    flux: np.ndarray,
    resolution_element_angstrom: float = FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
    resolution_profile: ResolutionProfile | None = None,
    excluded_windows_angstrom: Sequence[tuple[float, float]] = (),
    template_fit_type: str | None = None,
    reference_types: Iterable[str] | None = None,
) -> LineIndexClassification | None:
    """Find the reference whose line strengths are closest to a spectrum's.

    Parameters
    ----------
    wavelength_angstrom : `np.ndarray`
        The spectrum's wavelengths, in Angstroms.
    flux : `np.ndarray`
        The spectrum's brightness. Any units work, and a smooth tilt does not
        matter.
    resolution_element_angstrom : `float`, optional
        The instrument's resolution element, in Angstroms.
    resolution_profile : `ResolutionProfile`, optional
        The resolution element along the spectrum.
    excluded_windows_angstrom : `Sequence`, optional
        Windows to leave out, for example around emission lines.
    template_fit_type : `str`, optional
        The type the template classifier reported. When given, the result
        records how far the index type is from it.
    reference_types : `Iterable` [`str`], optional
        The references to compare with. Defaults to the whole main-sequence
        ladder. The spread of each index is taken over these references only.

    Returns
    -------
    classification : `LineIndexClassification` or `None`
        The nearest reference in index space. `None` when fewer than
        `MINIMUM_INDEX_COUNT` indices could be measured, or no reference
        had enough of them.
    """
    types, table = reference_index_table(resolution_element_angstrom, resolution_profile)
    if reference_types is not None:
        wanted = set(reference_types)
        selected = [position for position, label in enumerate(types) if label in wanted]
    else:
        selected = list(range(len(types)))
    if not selected:
        return None
    labels = [types[position] for position in selected]
    reference_values = table[selected]

    measured = measure_line_indices(
        wavelength_angstrom,
        flux,
        resolution_element_angstrom,
        resolution_profile,
        excluded_windows_angstrom,
    )
    observed = np.array([np.nan if value is None else value for value in measured.values()], dtype=float)
    if int(np.isfinite(observed).sum()) < MINIMUM_INDEX_COUNT:
        return None

    # Each index is divided by how much it varies across the references, so
    # an index with a small range (Na D) counts as much as one with a large
    # range (H-beta). An index that does not vary at all is left undivided.
    with np.errstate(all="ignore"):
        spread = np.nanstd(reference_values, axis=0)
    spread = np.where(np.isfinite(spread) & (spread > 0), spread, 1.0)
    differences = (reference_values - observed) / spread
    usable = np.isfinite(differences)
    counts = usable.sum(axis=1)
    squared = np.where(usable, differences, 0.0) ** 2
    with np.errstate(all="ignore"):
        distances = np.where(counts >= MINIMUM_INDEX_COUNT, np.sqrt(squared.sum(axis=1) / counts), np.inf)
    if not np.isfinite(distances).any():
        return None
    best = int(np.argmin(distances))
    steps = ladder_steps_between(template_fit_type, labels[best])
    return LineIndexClassification(
        best_type=labels[best],
        distance=float(distances[best]),
        indices={name: float(value) for name, value in measured.items() if value is not None},
        template_fit_type=template_fit_type or "",
        steps_from_template_fit=None if steps is None else abs(float(steps)),
    )
