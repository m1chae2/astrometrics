r"""Measure how often the emission-line detector invents or misses a line.

The emission-line detector fits a smooth continuum plus a box-shaped hump for
each named nebula line, and calls a line detected when it is at least 5
error bars above zero. Its thresholds were set on one nebula (M 57) and its
false-line rate was never measured. Two measurements:

1. **False lines.** The detector is run on every stored spectrum, as the
   pipeline runs it on every star. Most are ordinary stars, which have no
   emission lines to find, and they carry the instrument's real response
   errors and correlated noise, so a "detected" line in one is a false line.
   Some of the stars may truly emit (the stored spectra are not all typed), so
   the rate is an upper bound.

2. **Recovery.** A line of known strength is added to those spectra, and the
   share the detector reports as detected is counted for each strength. The
   strength is the line's height as a fraction of the spectrum's median
   brightness.

It reads the catalog database and writes nothing.

    python -m astrometricslib.scripts.validate_emission_line_detector
"""

import json
import os
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.special import erf

from astrometricslib import Astrometrics
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
)
from astrometricslib.pipelines.spectroscopy.processing.emission_line_detector import (
    DETECTED_SIGNIFICANCE,
    NEBULA_LINE_GROUPS,
    VERDICT_DETECTED,
    detect_emission_lines,
    is_emission_line_source,
    line_half_width_angstrom,
)

# The lines the recovery test injects: a strong red line and a strong blue one.
INJECTED_LINES = ("H-alpha + [N II]", "[O III] 4959+5007")

# Line heights tried, as a fraction of the spectrum's median brightness.
STRENGTHS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.6)

# SIMBAD object types of stars that may truly show emission lines; their
# spectra are left out of the false-line count.
_EMITTING_TYPES = ("Em*", "Be*", "HII", "PN", "Ae*", "TT*", "Or*", "WR*", "No*", "CV*", "Sy*", "Mi*")

_QUERY = """
    SELECT id,
           json_extract(data_json, '$.spectroscopy.wavelengthsAngstrom'),
           json_extract(data_json, '$.spectroscopy.responseCorrectedIntensities'),
           json_extract(data_json, '$.spectroscopy.intensities'),
           json_extract(data_json, '$.spectroscopy.resolutionElementAngstrom'),
           json_extract(data_json, '$.spectroscopy.rectangle'),
           json_extract(data_json, '$.simbadObjectTypes'),
           json_extract(data_json, '$.stellarSpectralType')
    FROM stellar_objects
    WHERE has_spectra = 1 AND id NOT LIKE '%::spectroscopy'
"""

# The stellar type the pipeline gives a glowing-gas target.
_EXTENDED_TARGET_TYPE = "Cluster"


@dataclass(frozen=True)
class StoredSpectrum:
    """What the check needs from one stored spectrum.

    Attributes
    ----------
    star_id : `str`
        The star's id.
    wavelength : `numpy.ndarray`
        Wavelengths in Angstroms.
    intensity : `numpy.ndarray`
        The brightness the pipeline would pass to the detector.
    resolution : `float`
        The resolution element in Angstroms (the fallback when unmeasured).
    half_width : `float`
        The half-width each line spreads to, as the pipeline works it out from
        the extraction box.
    """

    star_id: str
    wavelength: np.ndarray
    intensity: np.ndarray
    resolution: float
    half_width: float


def load_spectra(connection: sqlite3.Connection) -> list[StoredSpectrum]:
    """Read the stored spectra that may serve as ordinary-star nulls.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection to the catalog database.

    Returns
    -------
    spectra : `list` [`StoredSpectrum`]
        Spectra of stars that are not glowing-gas targets or types that
        usually emit.
    """
    spectra = []
    for row in connection.execute(_QUERY):
        star_id, wavelengths, corrected, raw, resolution, rectangle, object_types, stellar_type = row
        if not wavelengths or stellar_type == _EXTENDED_TARGET_TYPE or star_id.endswith("_Cluster"):
            continue
        if any(code in (object_types or "").split("|") for code in _EMITTING_TYPES):
            continue
        wavelength_values = np.array(json.loads(wavelengths), dtype=float)
        intensity_values = np.array(
            json.loads(corrected) if corrected else json.loads(raw or "[]"), dtype=float
        )
        if intensity_values.size != wavelength_values.size:
            continue
        resolution_element = float(resolution) if resolution else FALLBACK_RESOLUTION_ELEMENT_ANGSTROM
        box_width = float(json.loads(rectangle)[3]) if rectangle else None
        half_width = (
            line_half_width_angstrom(wavelength_values, box_width)
            if box_width is not None
            else resolution_element
        )
        spectra.append(
            StoredSpectrum(
                star_id,
                wavelength_values,
                intensity_values,
                resolution_element,
                max(half_width, resolution_element),
            )
        )
    return spectra


def run_detector(spectrum: StoredSpectrum, intensity: np.ndarray | None = None) -> list[dict[str, object]]:
    """Run the detector the way the pipeline does.

    Parameters
    ----------
    spectrum : `StoredSpectrum`
        The spectrum.
    intensity : `numpy.ndarray`, optional
        A different brightness to use, for example with a line injected.

    Returns
    -------
    lines : `list` [`dict`]
        The detector's entries.
    """
    return detect_emission_lines(
        spectrum.wavelength,
        spectrum.intensity if intensity is None else intensity,
        spectrum.half_width,
        resolution_element_angstrom=spectrum.resolution,
    )


def false_line_summary(spectra: Sequence[StoredSpectrum]) -> dict[str, float]:
    """Count the lines the detector reports in ordinary-star spectra.

    Parameters
    ----------
    spectra : `Sequence` [`StoredSpectrum`]
        The null spectra.

    Returns
    -------
    summary : `dict` [`str`, `float`]
        ``spectra`` run, ``with_a_detected_line``, ``called_emission_source``
        (two or more detected lines), and ``detected_lines`` in total.
    """
    with_line = 0
    sources = 0
    total = 0
    for spectrum in spectra:
        lines = run_detector(spectrum)
        detected = sum(1 for entry in lines if entry["verdict"] == VERDICT_DETECTED)
        with_line += detected > 0
        sources += is_emission_line_source(lines)
        total += detected
    return {
        "spectra": len(spectra),
        "with_a_detected_line": with_line,
        "called_emission_source": sources,
        "detected_lines": total,
    }


def inject_line(spectrum: StoredSpectrum, group_name: str, strength: float) -> np.ndarray | None:
    """Add a box-shaped emission line to a spectrum.

    Parameters
    ----------
    spectrum : `StoredSpectrum`
        The spectrum to add the line to.
    group_name : `str`
        One of the detector's named line groups.
    strength : `float`
        The line's height as a fraction of the spectrum's median brightness.

    Returns
    -------
    intensity : `numpy.ndarray` or `None`
        The spectrum with the line added, or `None` if the line lies outside
        the wavelengths the spectrum covers.
    """
    members = dict(NEBULA_LINE_GROUPS)[group_name]
    centre = float(
        np.average([wavelength for wavelength, _weight in members], weights=[w for _wl, w in members])
    )
    wavelength = spectrum.wavelength
    if centre < wavelength.min() + spectrum.half_width or centre > wavelength.max() - spectrum.half_width:
        return None
    sigma = spectrum.resolution / 2.355
    box = 0.5 * (
        erf((wavelength - (centre - spectrum.half_width)) / (np.sqrt(2) * sigma))
        - erf((wavelength - (centre + spectrum.half_width)) / (np.sqrt(2) * sigma))
    )
    return spectrum.intensity + strength * float(np.nanmedian(spectrum.intensity)) * box


def recovery_table(
    spectra: Sequence[StoredSpectrum],
    strengths: Sequence[float] = STRENGTHS,
    groups: Sequence[str] = INJECTED_LINES,
) -> dict[tuple[str, float], tuple[int, int]]:
    """Measure the share of injected lines the detector reports as detected.

    Parameters
    ----------
    spectra : `Sequence` [`StoredSpectrum`]
        The spectra to inject into.
    strengths : `Sequence` [`float`], optional
        Line heights as fractions of the median brightness.
    groups : `Sequence` [`str`], optional
        The line groups to inject.

    Returns
    -------
    table : `dict` [`tuple` [`str`, `float`], `tuple` [`int`, `int`]]
        For each line and strength, the number detected and the number tried.
    """
    table: dict[tuple[str, float], tuple[int, int]] = {}
    for group in groups:
        for strength in strengths:
            detected = tried = 0
            for spectrum in spectra:
                injected = inject_line(spectrum, group, strength)
                if injected is None:
                    continue
                tried += 1
                lines = run_detector(spectrum, injected)
                detected += any(
                    group in str(entry["line"]) and entry["verdict"] == VERDICT_DETECTED for entry in lines
                )
            table[group, strength] = (detected, tried)
    return table


def format_report(summary: dict[str, float], table: dict[tuple[str, float], tuple[int, int]]) -> str:
    """Write the false-line counts and the recovery table.

    Parameters
    ----------
    summary : `dict` [`str`, `float`]
        From `false_line_summary`.
    table : `dict`
        From `recovery_table`.

    Returns
    -------
    text : `str`
        The report.
    """
    count = int(summary["spectra"])
    lines = [
        f"{count} stored spectra of stars not known to emit.",
        f"  with at least one 'detected' line (false lines):  {int(summary['with_a_detected_line'])} "
        f"({summary['with_a_detected_line'] / max(count, 1):.1%})",
        f"  called an emission-line source (two or more):     {int(summary['called_emission_source'])} "
        f"({summary['called_emission_source'] / max(count, 1):.1%})",
        f"  detected lines in all:                            {int(summary['detected_lines'])}",
        "",
        f"Share of injected lines reported 'detected' (limit: {DETECTED_SIGNIFICANCE:g} error bars):",
        "  height / median brightness   " + "".join(f"{strength:>8.2f}" for strength in STRENGTHS),
    ]
    for group in INJECTED_LINES:
        cells = []
        for strength in STRENGTHS:
            detected, tried = table.get((group, strength), (0, 0))
            cells.append(f"{detected / tried:>8.0%}" if tried else f"{'--':>8}")
        lines.append(f"  {group:27s}" + "".join(cells))
    return "\n".join(lines)


def main() -> int:
    """Run the validation from the command line.

    Returns
    -------
    exit_code : `int`
        ``0`` on success, ``1`` if the catalog database cannot be found.
    """
    database = os.path.join(str(Astrometrics().config.get_library_path()), "astrometrics.db")
    if not os.path.exists(database):
        print(f"No catalog database at {database}.")
        return 1
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        spectra = load_spectra(connection)
    finally:
        connection.close()
    print(format_report(false_line_summary(spectra), recovery_table(spectra)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
