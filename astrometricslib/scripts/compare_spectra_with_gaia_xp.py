r"""Compare a target's stored spectra with their Gaia DR3 XP spectra.

The pipeline checks each calibrated spectrum against the star's Gaia DR3 XP
spectrum (see `pipelines/spectroscopy/post_processing/compare_to_gaia_xp.py`).
This script runs the same comparison for every star of a target that has
already been processed, without touching the images or the catalog, and prints
what the owner needs to measure the response error on real data:

* one line per star: its Gaia source id, the tilt of observed / XP (in percent
  per 1000 Angstroms), the RMS residual, the wavelength shift, and the median
  ratio observed / XP in the four bands 4200-5000, 5000-6000, 6000-7000 and
  7000-8000 Angstroms. A star that could not be compared says why instead;
* the run summary: the median ratio in each band over the compared stars and
  its scatter. That median ratio is the residual instrument response. Dividing
  the response-corrected spectra by it would make them agree with Gaia;
* the verdict of the `gaia_xp_agreement` gate.

The script reads the response-corrected spectrum the pipeline stored. A star
without one (the camera had no stored instrument response when it was
processed) is listed as not compared. Gaia XP spectra are downloaded once and
cached in the library's data folder (``catalogs/gaia_xp``), so a second run
needs no network.

Run it with::

    python -m astrometricslib.scripts.compare_spectra_with_gaia_xp "Vega"

It writes nothing to the catalog.
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from typing import Any

import numpy as np

from astrometricslib import Astrometrics, configure_logging
from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.drivers.interfaces.gaia_xp_driver import GaiaXpDriver
from astrometricslib.models.gaia_xp_comparison import GaiaXpComparison
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp import (
    COMPARISON_BANDS_ANGSTROM,
    GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM,
    compare_to_gaia_xp,
    gaia_dr3_source_id,
    gaia_xp_gate,
    summarize_gaia_xp,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
    ResolutionProfile,
    load_line_spread_profile,
)

logger = logging.getLogger(__name__)


def compare_star(
    star: StellarObject, driver: GaiaXpDriver, line_spread: ResolutionProfile | None
) -> GaiaXpComparison | None:
    """Compare one stored star with its Gaia XP spectrum.

    Parameters
    ----------
    star : `StellarObject`
        A star from the catalog.
    driver : `GaiaXpDriver`
        Where the XP spectrum comes from.
    line_spread : `ResolutionProfile` or `None`
        The camera's line spread, or `None` when none is stored.

    Returns
    -------
    comparison : `GaiaXpComparison` or `None`
        The comparison, or `None` when the star has no stored spectrum.
    """
    spectroscopy = star.spectroscopy
    if spectroscopy is None or not spectroscopy.wavelengths_angstrom:
        return None
    response_corrected = (
        np.asarray(spectroscopy.response_corrected_intensities, dtype=float)
        if spectroscopy.response_corrected_intensities
        else None
    )
    # The pipeline may store the spectrum's errors beside it; use them when it
    # does, and only XP's errors otherwise.
    errors = getattr(spectroscopy, "response_corrected_intensity_errors", None)
    return compare_to_gaia_xp(
        driver,
        gaia_dr3_source_id(star),
        np.asarray(spectroscopy.wavelengths_angstrom, dtype=float),
        response_corrected,
        intensity_errors=np.asarray(errors, dtype=float) if errors else None,
        line_spread=line_spread,
        fallback_resolution_angstrom=spectroscopy.resolution_element_angstrom
        or FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
    )


def _number(value: float | None, width: int, decimals: int, signed: bool = False) -> str:
    """Format a number for the table, or a dash when it is missing.

    Parameters
    ----------
    value : `float` or `None`
        The number.
    width : `int`
        The column width.
    decimals : `int`
        Digits after the decimal point.
    signed : `bool`, optional
        Whether to always print the sign.

    Returns
    -------
    text : `str`
        The number, right-aligned in `width` characters.
    """
    if value is None:
        return "-".rjust(width)
    return f"{value:{'+' if signed else ''}.{decimals}f}".rjust(width)


def format_report(results: Sequence[tuple[str, GaiaXpComparison]]) -> str:
    """Build the report text: one line per star, then the run summary.

    Parameters
    ----------
    results : `Sequence` [`tuple` [`str`, `GaiaXpComparison`]]
        Each star's id and its comparison.

    Returns
    -------
    report : `str`
        The table, the per-band run summary and the gate verdict.
    """
    band_names = [f"{low:.0f}-{high:.0f}" for low, high in COMPARISON_BANDS_ANGSTROM]
    header = f"{'star':36s} {'Gaia source':>19s} {'slope%/kA':>10s} {'rms':>6s} {'shift A':>8s}  " + " ".join(
        name.rjust(9) for name in band_names
    )
    lines = [header, "-" * len(header)]
    rows = []
    for star_id, comparison in results:
        if comparison.status != "compared":
            lines.append(
                f"{star_id[:36]:36s} {comparison.gaia_source_id or '-':>19}  not compared: "
                f"{comparison.not_checked_reason}"
            )
            continue
        rows.append({"star_id": star_id, **comparison.model_dump()})
        ratios = " ".join(_number(band.median_ratio, 9, 3) for band in comparison.bands)
        lines.append(
            f"{star_id[:36]:36s} {comparison.gaia_source_id:>19d} "
            f"{_number(comparison.slope_percent_per_1000_angstrom, 10, 2, signed=True)} "
            f"{_number(comparison.residual_rms_fraction, 6, 3)} "
            f"{_number(comparison.wavelength_shift_angstrom, 8, 1, signed=True)}  {ratios}"
        )

    summary = summarize_gaia_xp(rows)
    lines.append("")
    if summary is None:
        lines.append("No star could be compared with a Gaia XP spectrum.")
    else:
        lines.append(
            f"Run summary over {summary.compared_star_count} compared star(s). Median ratio observed / XP "
            "is the residual instrument response; a response-corrected spectrum divided by it would agree "
            "with Gaia."
        )
        lines.append(f"{'band (A)':>12s} {'median ratio':>13s} {'scatter':>9s} {'stars':>6s}")
        for name, band in zip(band_names, summary.bands, strict=True):
            lines.append(
                f"{name:>12s} {_number(band.median_ratio, 13, 4)} {_number(band.scatter, 9, 4)} "
                f"{band.star_count:>6d}"
            )
        lines.append(
            f"Median tilt {_number(summary.median_slope_percent_per_1000_angstrom, 0, 2, signed=True)} "
            f"percent per 1000 A; median absolute tilt "
            f"{_number(summary.median_absolute_slope_percent_per_1000_angstrom, 0, 2)} "
            f"(designed limit {GAIA_XP_SLOPE_LIMIT_PERCENT_PER_1000_ANGSTROM:g})."
        )
    gate = gaia_xp_gate(rows)
    lines.append(f"Gate {gate.name}: {gate.status.value}. {gate.detail}".rstrip())
    return "\n".join(lines)


def compare_target(
    astrometrics: Any, target_id: str, driver: GaiaXpDriver, camera_name: str | None = None
) -> list[tuple[str, GaiaXpComparison]]:
    """Compare every star of a target that has a stored spectrum.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library, for the catalog.
    target_id : `str`
        The target whose stars to compare.
    driver : `GaiaXpDriver`
        Where the XP spectra come from.
    camera_name : `str`, optional
        The camera the spectra were taken with, for its stored line spread.
        The configuration's primary camera when left out.

    Returns
    -------
    results : `list` [`tuple` [`str`, `GaiaXpComparison`]]
        Each star's id and comparison. A star with no stored spectrum is left
        out.
    """
    camera_name = camera_name or astrometrics.config.get_primary_camera_name()
    line_spread = load_line_spread_profile(camera_name)
    star_ids = [
        summary.id
        for summary in astrometrics.catalog_access.list_star_summaries(target_id=target_id)
        if summary.has_spectra and not summary.id.endswith("::spectroscopy")
    ]
    results = []
    for star in astrometrics.catalog_access.get_by_ids("stellar_catalog", star_ids) if star_ids else []:
        comparison = compare_star(star, driver, line_spread)
        if comparison is not None:
            results.append((star.id, comparison))
    return results


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        Parser taking the target and an optional camera name.
    """
    parser = argparse.ArgumentParser(
        prog="compare_spectra_with_gaia_xp",
        description="Compare a target's stored spectra with their Gaia DR3 XP spectra.",
    )
    parser.add_argument("target", help="The target whose stars to compare (for example Vega).")
    parser.add_argument(
        "--camera",
        default=None,
        help="Camera name, for its stored line spread. Default: the primary camera.",
    )
    return parser


def run_comparison(argv: list[str] | None = None) -> int:
    """Print the Gaia XP comparison for every spectrum of a target.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        ``0`` on success, ``1`` when the target has no stored spectrum.
    """
    arguments = _build_argument_parser().parse_args(argv)
    configure_logging("compare_spectra_with_gaia_xp", level=logging.INFO, log_dir="")
    astrometrics = Astrometrics()
    results = compare_target(
        astrometrics, arguments.target, Drivers().gaia_xp_or_default(), camera_name=arguments.camera
    )
    if not results:
        print(f"No stored spectra found for target {arguments.target!r}.")
        return 1
    print(format_report(results))
    return 0


if __name__ == "__main__":
    sys.exit(run_comparison())
