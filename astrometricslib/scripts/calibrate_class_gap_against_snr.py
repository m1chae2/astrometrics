r"""Calibrate the spectral-class gap against signal-to-noise.

The spectra are good ones, degraded with added noise.

The classifier says how much better its best reference fits than the runner-up
and than the nearest other class. This script finds how those gaps shrink as a
spectrum gets noisier. It takes the stored spectra that already have a high
signal-to-noise and a catalog type, adds white noise until each reads a stated
signal-to-noise, classifies each copy with the project's own classifier and
records the gaps. The method, its limits and the results are in validation
register entry SPE-30.

Check first, then store the table::

    python -m astrometricslib.scripts.calibrate_class_gap_against_snr

    python -m astrometricslib.scripts.calibrate_class_gap_against_snr --apply

Without ``--apply`` the table is printed and nothing is written. With it the
table is written to
``astrometricslib/pipelines/spectroscopy/data/class_gap_against_snr_<camera>.json``
(for example ``class_gap_against_snr_zwo_asi_533mm_pro.json``), where
`class_gap_for_snr` reads it. The file records the camera, a definition
version, the ambiguity threshold and the signal-to-noise estimator it was
built with, and the lookup refuses a file that does not match the code. The
script reads the library's catalog and writes nothing there.

A spectrum is used when it has a catalog type on the O-to-M ladder, a
response-corrected spectrum with its resolution element, and a signal-to-noise
of at least 30 on that response-corrected spectrum, by the estimate from the
spectrum's own scatter (`estimate_spectrum_signal_to_noise`). It is not the
per-sample-variance signal-to-noise, which reads far higher. The uncertainty
is found by resampling star fields, not spectra.
"""

import argparse
import datetime
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from astrometricslib import Astrometrics
from astrometricslib.models.stellar_source import AMBIGUOUS_RMS_GAP, ladder_position
from astrometricslib.pipelines.spectroscopy.processing.class_gap_calibration import (
    CAMERA_NAME,
    CLASS_GAP_DEFINITION_VERSION,
    CLASS_GAP_SNR_ESTIMATOR,
    DEFAULT_SNR_GRID,
    REACHED_SHARE,
    ClassGapTable,
    SpectrumSample,
    class_gap_table,
    class_gap_table_path,
)
from astrometricslib.pipelines.spectroscopy.processing.spectrum_signal import (
    estimate_spectrum_signal_to_noise,
)

# The signal-to-noise a spectrum needs to be used. Every grid value is below
# it, so every spectrum can be degraded to every grid value.
MINIMUM_NATIVE_SNR = 30.0


def select_samples(catalog_access: Any, minimum_snr: float = MINIMUM_NATIVE_SNR) -> list[SpectrumSample]:
    """Pick the stored spectra to degrade.

    Parameters
    ----------
    catalog_access : `CatalogAccess`
        Lists and loads the library's stars.
    minimum_snr : `float`, optional
        The least signal-to-noise (by the spectrum's own scatter) to use.

    Returns
    -------
    samples : `list` [`SpectrumSample`]
        One for each star with a catalog type on the O-to-M ladder, a
        response-corrected spectrum and a signal-to-noise of at least
        `minimum_snr`. The field is the star's first target.
    """
    wanted = [
        summary
        for summary in catalog_access.list_star_summaries()
        if summary.has_spectra and ladder_position(summary.spectral_type) is not None and summary.target_ids
    ]
    samples: list[SpectrumSample] = []
    for start in range(0, len(wanted), 200):
        chunk = wanted[start : start + 200]
        for star in catalog_access.get_by_ids("stellar_catalog", [summary.id for summary in chunk]):
            spectrum = star.spectroscopy
            if (
                spectrum is None
                or not spectrum.response_corrected_intensities
                or spectrum.input_quality is None
            ):
                continue
            if len(spectrum.response_corrected_intensities) != len(spectrum.wavelengths_angstrom):
                continue
            # The signal-to-noise is read on the spectrum that is degraded (the
            # response-corrected one), by the same estimator that sets the
            # target values. The stored `inputQuality.signalToNoise` is read on
            # the quantum-efficiency-corrected spectrum and can differ.
            snr = estimate_spectrum_signal_to_noise(
                np.asarray(spectrum.wavelengths_angstrom, dtype=float),
                np.asarray(spectrum.response_corrected_intensities, dtype=float),
                float(spectrum.input_quality.resolution_element_angstrom),
            )
            if snr is None or not np.isfinite(snr) or snr < minimum_snr:
                continue
            samples.append(
                SpectrumSample(
                    field_id=star.target_ids[0] if star.target_ids else "",
                    star_id=star.id,
                    wavelength_angstrom=np.asarray(spectrum.wavelengths_angstrom, dtype=float),
                    intensity=np.asarray(spectrum.response_corrected_intensities, dtype=float),
                    resolution_element_angstrom=float(spectrum.input_quality.resolution_element_angstrom),
                    catalog_type=star.spectral_type,
                    native_snr=float(snr),
                )
            )
    return samples


def table_record(
    table: ClassGapTable, samples: Sequence[SpectrumSample], arguments: argparse.Namespace
) -> dict:
    """Write a table as a JSON-ready record with the facts a reader needs.

    Parameters
    ----------
    table : `ClassGapTable`
        The result.
    samples : `Sequence` [`SpectrumSample`]
        The spectra it was made from.
    arguments : `argparse.Namespace`
        The run's settings (draws, resamples, seed).

    Returns
    -------
    record : `dict`
        The record that `class_gap_for_snr` reads.
    """
    return {
        "description": (
            "Class gap against signal-to-noise: high-signal-to-noise spectra of classified stars "
            "degraded with white noise and classified again with classify_spectral_type. See "
            "validation register entry SPE-30."
        ),
        "created": datetime.date.today().isoformat(),
        "camera": arguments.camera,
        "definition_version": CLASS_GAP_DEFINITION_VERSION,
        "ambiguous_rms_gap": AMBIGUOUS_RMS_GAP,
        "reached_share": REACHED_SHARE,
        "minimum_native_snr": MINIMUM_NATIVE_SNR,
        "snr_estimator": CLASS_GAP_SNR_ESTIMATOR,
        "interval_percentiles": [16, 84],
        "draws_per_spectrum": arguments.draws,
        "field_bootstrap_count": arguments.bootstrap,
        "seed": arguments.seed,
        "spectrum_count": len(samples),
        "fields": sorted({sample.field_id for sample in samples}),
        "native": table.native.as_dict(),
        "native_agreement_by_field": table.native_agreement_by_field,
        "rows": [row.as_dict() for row in table.rows],
        "class_reachable_snr": table.class_reachable_snr,
        "subtype_reachable_snr": table.subtype_reachable_snr,
    }


def _share_text(row: dict, key: str) -> str:
    """Write one share with its interval for the screen.

    Parameters
    ----------
    row : `dict`
        A row of the record.
    key : `str`
        The share's field name.

    Returns
    -------
    text : `str`
        ``0.54 [0.40-0.66]``, or ``--`` when the share or its interval is
        missing.
    """
    interval = row.get("share_intervals", {}).get(key)
    if row[key] is None or interval is None:
        return "--"
    return f"{row[key]:.2f} [{interval[0]:.2f}-{interval[1]:.2f}]"


def format_table(record: dict) -> str:
    """Write a table as text for the screen.

    Parameters
    ----------
    record : `dict`
        From `table_record`.

    Returns
    -------
    text : `str`
        One line for the undegraded spectra and one for each grid value.
    """
    header = (
        f"{record['spectrum_count']} spectra from {len(record['fields'])} fields; "
        f"ambiguous below a gap of {record['ambiguous_rms_gap']}"
    )
    lines = [
        header,
        "SNR     n   class gap [16-84%]       class sep  subtype sep  class agree  agree w/ own  steps",
    ]
    native = record["native"]
    for label, row in [("native", native), *((f"{item['snr']:g}", item) for item in record["rows"])]:
        low, high = row["gap_to_next_class_low"], row["gap_to_next_class_high"]
        gap = row["median_gap_to_next_class"]
        lines.append(
            f"{label:>6} {row['spectrum_count']:4d}  "
            + (f"{gap:.4f} [{low:.4f}-{high:.4f}]" if gap is not None and low is not None else "no value")
            + "  "
            + "  ".join(
                _share_text(row, key)
                for key in (
                    "class_separated_share",
                    "subtype_separated_share",
                    "class_agreement_share",
                    "undegraded_class_agreement_share",
                )
            )
            + "  "
            + (
                "--"
                if row["median_steps_from_catalog"] is None
                else f"{row['median_steps_from_catalog']:.1f}"
            )
        )
    lines.append(
        "native class agreement by field: "
        + ", ".join(
            f"{name} {value['class_agreement_share']:.2f} (n={value['count']:.0f})"
            for name, value in record["native_agreement_by_field"].items()
        )
    )
    lines.append(f"class separation: {record['class_reachable_snr']}")
    lines.append(f"subtype separation: {record['subtype_reachable_snr']}")
    return "\n".join(lines)


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        Parser covering the run's settings and whether to write.
    """
    parser = argparse.ArgumentParser(
        prog="calibrate_class_gap_against_snr",
        description="Degrade high-signal-to-noise spectra and record the class gap reached.",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write the table. Without this it is only printed."
    )
    parser.add_argument(
        "--draws", type=int, default=5, help="Noise draws per spectrum at each value. Default 5."
    )
    parser.add_argument(
        "--bootstrap", type=int, default=400, help="Field resamples for the range. Default 400."
    )
    parser.add_argument("--seed", type=int, default=20261024, help="Random seed. Default 20261024.")
    parser.add_argument(
        "--grid", type=float, nargs="+", default=list(DEFAULT_SNR_GRID), help="Signal-to-noise values."
    )
    parser.add_argument(
        "--camera", default=CAMERA_NAME, help="The camera the spectra were taken with. Names the file."
    )
    parser.add_argument(
        "--output", type=Path, default=None, help="Where --apply writes. Default: the camera's own file."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the calibration from the command line.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        ``0`` on success, ``1`` when no spectrum could be used.
    """
    arguments = _build_argument_parser().parse_args(argv)
    astrometrics = Astrometrics()
    samples = select_samples(astrometrics.catalog_access)
    if not samples:
        print("No stored spectrum has a catalog type and a signal-to-noise of 30 or more.")
        return 1
    table = class_gap_table(
        samples,
        snr_grid=arguments.grid,
        draws_per_spectrum=arguments.draws,
        bootstrap_count=arguments.bootstrap,
        seed=arguments.seed,
    )
    record = table_record(table, samples, arguments)
    print(format_table(record))
    if arguments.apply:
        output = arguments.output or class_gap_table_path(arguments.camera)
        output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {output}.")
    else:
        print("Dry run: nothing was written. Re-run with --apply to store this table.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
