r"""Re-analyze every stored spectrum with the current spectral analysis.

Spectra saved by older versions of the pipeline have three problems this
script repairs without re-extracting anything from the images:

1. Where the spectrum trail ran off the image, the missing part was saved
   as brightness zero. Runs of exact zeros at either end are removed and
   the star's ``valid_fraction`` and requested wavelength range are
   recorded, so the spectrum shows where measurement really stopped.
2. Samples beyond the camera's wavelength range are removed.
3. The spectral-type match and the absorption-feature test were made with
   older, less careful methods. They are recomputed with
   `analyze_spectrum`: the instrument's response is removed before
   matching reference spectra, and each feature gets a p-value, an
   expected depth and a verdict.

Check first, then apply::

    python -m astrometricslib.scripts.recompute_spectral_analysis

    python -m astrometricslib.scripts.recompute_spectral_analysis --apply

To work on only the stars of one target, add ``--target`` (for example
``--target Vega``). Without it, every star with a spectrum is recomputed.

``--apply`` copies the catalog database to a timestamped ``.bak`` file in
the same directory before writing anything. Running it again gives the
same results.
"""

import argparse
import logging
import sys

import numpy as np

from astrometricslib import Astrometrics
from astrometricslib.models.stellar_source import SpectralObservation, StellarObject
from astrometricslib.pipelines.spectroscopy.spectrum_analysis import analyze_spectrum
from astrometricslib.scripts.reconcile_position_only_star_catalog import _backup_catalog_database

logger = logging.getLogger(__name__)

# Fewest exact-zero samples in a row, at the start or end of a spectrum,
# that count as "the trail ran off the image". A real spectrum can dip to
# zero at a single sample where a bad pixel was masked, but not for ten in
# a row (about 110 A at this dispersion).
_MINIMUM_ZERO_RUN_SAMPLES = 10

# Fewest samples that must remain for a spectrum to be analyzed at all.
_MINIMUM_SAMPLES_TO_ANALYZE = 30


def find_measured_samples(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    minimum_wavelength_angstrom: float,
    maximum_wavelength_angstrom: float,
) -> np.ndarray:
    """Find which samples of a stored spectrum were really measured.

    Trailing and leading runs of exact zeros (at least
    `_MINIMUM_ZERO_RUN_SAMPLES` long) are treated as "off the image", and
    anything outside the camera's wavelength range is dropped.

    Parameters
    ----------
    wavelength_angstrom : `np.ndarray`
        The stored wavelengths, in Angstroms.
    intensity : `np.ndarray`
        The stored brightness values.
    minimum_wavelength_angstrom : `float`
        The shortest wavelength the camera can see, in Angstroms.
    maximum_wavelength_angstrom : `float`
        The longest wavelength the camera can see, in Angstroms.

    Returns
    -------
    measured : `np.ndarray`
        A boolean array, `True` for each sample to keep.
    """
    measured = (
        np.isfinite(wavelength_angstrom)
        & np.isfinite(intensity)
        & (wavelength_angstrom >= minimum_wavelength_angstrom)
        & (wavelength_angstrom <= maximum_wavelength_angstrom)
    )
    # An exact zero, as the old extractor wrote for a sample off the image.
    is_zero = np.isclose(intensity, 0.0, rtol=0.0, atol=0.0)
    # Drop a run of zeros that starts at the end of the spectrum...
    end = len(intensity)
    while end > 0 and is_zero[end - 1]:
        end -= 1
    if len(intensity) - end >= _MINIMUM_ZERO_RUN_SAMPLES:
        measured[end:] = False
    # ...and one that starts at the beginning.
    start = 0
    while start < len(intensity) and is_zero[start]:
        start += 1
    if start >= _MINIMUM_ZERO_RUN_SAMPLES:
        measured[:start] = False
    return measured


def recompute_star(
    star: StellarObject,
    camera_name: str,
    minimum_wavelength_angstrom: float,
    maximum_wavelength_angstrom: float,
) -> bool:
    """Repair and re-analyze one star's stored spectrum, in place.

    Parameters
    ----------
    star : `StellarObject`
        The star, changed in place.
    camera_name : `str`
        The camera the spectrum was taken with.
    minimum_wavelength_angstrom : `float`
        The shortest wavelength the camera can see, in Angstroms.
    maximum_wavelength_angstrom : `float`
        The longest wavelength the camera can see, in Angstroms.

    Returns
    -------
    changed : `bool`
        `True` when the star had a spectrum to re-analyze.
    """
    spectroscopy = star.spectroscopy
    if spectroscopy is None or not spectroscopy.wavelengths_angstrom:
        return False

    wavelengths = np.array(spectroscopy.wavelengths_angstrom, dtype=float)
    intensities = np.array(spectroscopy.intensities, dtype=float)
    corrected = (
        np.array(spectroscopy.quantum_efficiency_corrected_intensities, dtype=float)
        if spectroscopy.quantum_efficiency_corrected_intensities
        else None
    )
    measured = find_measured_samples(
        wavelengths, intensities, minimum_wavelength_angstrom, maximum_wavelength_angstrom
    )
    if corrected is not None and corrected.size == measured.size:
        measured &= np.isfinite(corrected)

    if spectroscopy.requested_wavelength_range_angstrom is None:
        spectroscopy.requested_wavelength_range_angstrom = [
            float(wavelengths.min()),
            float(wavelengths.max()),
        ]
    spectroscopy.valid_fraction = float(measured.mean())

    def trim(values: list | None) -> list | None:
        """Keep only the measured samples of a per-sample list.

        Returns
        -------
        trimmed : `list` or `None`
            The kept values, or the input unchanged if it does not line up
            with the spectrum one-to-one.
        """
        if values is None or len(values) != measured.size:
            return values
        return np.asarray(values, dtype=float)[measured].tolist()

    spectroscopy.wavelengths_angstrom = wavelengths[measured].tolist()
    spectroscopy.intensities = intensities[measured].tolist()
    if corrected is not None and corrected.size == measured.size:
        spectroscopy.quantum_efficiency_corrected_intensities = corrected[measured].tolist()
    spectroscopy.trail_centerline_px = trim(spectroscopy.trail_centerline_px)
    spectroscopy.trail_width_px = trim(spectroscopy.trail_width_px)

    trimmed_history = []
    for observation in star.spectra_history:
        if (
            observation.wavelengths
            and observation.intensities
            and len(observation.wavelengths) == len(observation.intensities)
        ):
            history_wavelengths = np.array(observation.wavelengths, dtype=float)
            history_intensities = np.array(observation.intensities, dtype=float)
            keep = find_measured_samples(
                history_wavelengths,
                history_intensities,
                minimum_wavelength_angstrom,
                maximum_wavelength_angstrom,
            )
            observation = SpectralObservation(
                timestamp=observation.timestamp,
                wavelengths=history_wavelengths[keep].tolist(),
                intensities=history_intensities[keep].tolist(),
            )
        trimmed_history.append(observation)
    star.spectra_history = trimmed_history

    if len(spectroscopy.wavelengths_angstrom) < _MINIMUM_SAMPLES_TO_ANALYZE:
        spectroscopy.self_determined_spectral_type = "Unknown"
        spectroscopy.self_determined_spectral_type_confidence = None
        spectroscopy.self_determined_spectral_type_rms = None
        spectroscopy.self_determined_spectral_type_note = "too little of the spectrum was measured"
        spectroscopy.self_determined_spectral_type_candidates = []
        spectroscopy.probable_spectral_features = []
        return True

    analysis = analyze_spectrum(
        np.array(spectroscopy.wavelengths_angstrom),
        np.array(
            spectroscopy.quantum_efficiency_corrected_intensities
            if spectroscopy.quantum_efficiency_corrected_intensities
            else spectroscopy.intensities
        ),
        camera_name,
        is_quantum_efficiency_corrected=bool(spectroscopy.quantum_efficiency_corrected_intensities),
        catalog_spectral_type=star.spectral_type,
        trail_width_px=spectroscopy.trail_width_px,
    )
    spectroscopy.resolution_element_angstrom = (
        analysis.resolution_element_angstrom if analysis.is_resolution_measured else None
    )
    classification = analysis.classification
    spectroscopy.self_determined_spectral_type = str(classification["spectral_type"])
    spectroscopy.self_determined_spectral_type_confidence = classification["confidence"]
    spectroscopy.self_determined_spectral_type_rms = classification["rms"]
    spectroscopy.self_determined_spectral_type_note = str(classification.get("reason") or "")
    spectroscopy.self_determined_spectral_type_candidates = classification["ranked_types"]
    spectroscopy.probable_spectral_features = analysis.features
    return True


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        Parser covering whether to write.
    """
    parser = argparse.ArgumentParser(
        prog="recompute_spectral_analysis",
        description="Repair stored spectra and recompute their spectral type and feature tests.",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write the results. Without this only a report is printed."
    )
    parser.add_argument(
        "--target",
        default=None,
        help="Only recompute the stars of this target (for example Vega). Default: every star.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Explicitly request a preview. This is already the default."
    )
    return parser


def run_recompute(argv: list[str] | None = None) -> int:
    """Report or apply the recomputation for every star with a spectrum.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        ``0`` on success, ``1`` if `--apply` was requested but the safety
        backup could not be made.
    """
    arguments = _build_argument_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    astrometrics = Astrometrics()
    camera_name = astrometrics.config.get_primary_camera_name()
    camera_config = astrometrics.config.get_camera_config(camera_name)
    minimum_wavelength_angstrom = float(camera_config.get("sensor_min_wavelength", 300.0)) * 10.0
    maximum_wavelength_angstrom = float(camera_config.get("sensor_max_wavelength", 1000.0)) * 10.0

    star_ids = [
        summary.id
        for summary in astrometrics.catalog_access.list_star_summaries(target_id=arguments.target)
        if summary.has_spectra and not summary.id.endswith("::spectroscopy")
    ]
    if not star_ids:
        print(
            "No stars with spectra found"
            + (f" for target {arguments.target!r}." if arguments.target else ".")
        )
        return 0

    stars = astrometrics.catalog_access.get_by_ids("stellar_catalog", star_ids)
    print(
        f"{len(stars)} star(s) with spectra, camera {camera_name} "
        f"({minimum_wavelength_angstrom:.0f}-{maximum_wavelength_angstrom:.0f} A):"
    )
    changed_stars = []
    for star in stars:
        before_samples = len(star.spectroscopy.wavelengths_angstrom)
        before_type = star.spectroscopy.self_determined_spectral_type
        if not recompute_star(star, camera_name, minimum_wavelength_angstrom, maximum_wavelength_angstrom):
            continue
        changed_stars.append(star)
        spectroscopy = star.spectroscopy
        detected = [
            str(feature["feature"]).split("(")[-1].strip(")")
            for feature in spectroscopy.probable_spectral_features
            if feature["verdict"] in ("detected", "possible")
        ]
        print(
            f"  {star.id[:36]:36s} samples {before_samples:4d} -> "
            f"{len(spectroscopy.wavelengths_angstrom):4d}  "
            f"type {before_type or '-':8s} -> {spectroscopy.self_determined_spectral_type:8s} "
            f"(catalog {star.spectral_type or '-':8s})  features: {', '.join(detected) or 'none'}"
        )

    if not arguments.apply:
        print("\nDry run: nothing was written. Re-run with --apply to save these results.")
        return 0

    backup_path = _backup_catalog_database(astrometrics)
    if backup_path is None:
        print(
            "\nCould not create a safety backup of the catalog database; aborting without writing anything."
        )
        return 1
    print(f"\nBacked up the catalog database to {backup_path}.")
    astrometrics.catalog_access.merge_and_record(
        "stellar_catalog", changed_stars, lambda _existing, updated: updated
    )
    print(f"Saved {len(changed_stars)} star(s).")
    return 0


if __name__ == "__main__":
    sys.exit(run_recompute())
