r"""Derive the instrument response from a known star's master stack.

Reads the master stacked spectral image of a standard star (Vega by
default, type A0V), extracts its spectrum without saving anything to the
catalog, fits the instrument response (see
`astrometricslib.pipelines.spectroscopy.instrument_response`) and writes
it as ``instrument_response_<camera>.json`` in the spectroscopy data
folder.

    python -m astrometricslib.scripts.derive_instrument_response

Options let you choose another target, reference type or camera. Run it
again after any change to the grating, camera or telescope, since the
response belongs to one setup.
"""

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from astrometricslib import Astrometrics
from astrometricslib.pipelines.astrometry.pipeline import AstrometryPipeline
from astrometricslib.pipelines.spectroscopy.instrument_response import (
    DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM,
    derive_instrument_response,
)
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline

DATA_DIR = Path(__file__).resolve().parents[1] / "pipelines" / "spectroscopy" / "data"


def run_derivation(argv: list[str] | None = None) -> int:
    """Extract the standard star's spectrum and store the fitted response.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        ``0`` on success, ``1`` when the target has no master spectral
        stack.
    """
    parser = argparse.ArgumentParser(description="Derive the instrument response from a standard star.")
    parser.add_argument("--target", default="Vega", help="Catalog target holding the standard star.")
    parser.add_argument(
        "--reference-type", default="A0V", help="The star's spectral type, as a bundled reference."
    )
    parser.add_argument(
        "--minimum-wavelength",
        type=float,
        default=DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM[0],
        help="Shortest wavelength to fit, in Angstroms.",
    )
    parser.add_argument(
        "--maximum-wavelength",
        type=float,
        default=DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM[1],
        help=(
            "Longest wavelength to fit, in Angstroms. The default stops at 8000 A because second-order "
            "light and low sensitivity spoil the spectrum beyond it; pass the camera's limit "
            "(for example 10000) to fit the full range, for a setup that blocks second-order light."
        ),
    )
    arguments = parser.parse_args(argv)

    astrometrics = Astrometrics()
    target = astrometrics.targets.get(arguments.target)
    stacked_path = getattr(target, "stacked_spectral_target", None) if target else None
    if not stacked_path:
        print(f"Target {arguments.target!r} has no master stacked spectral image.")
        return 1

    context = AstrometryPipeline().process(stacked_path, attempt_plate_solving=False)
    pipeline = SpectroscopyPipeline()
    # The brightest detection in a standard star's own field is the star.
    star = pipeline.process(context, limit=1)[0]
    spectroscopy = star.spectroscopy
    intensity = spectroscopy.quantum_efficiency_corrected_intensities or spectroscopy.intensities

    camera_config = astrometrics.config.get_camera_config(pipeline.config.camera.name)
    camera_range_angstrom = (
        float(camera_config.get("sensor_min_wavelength", 300.0)) * 10.0,
        float(camera_config.get("sensor_max_wavelength", 1000.0)) * 10.0,
    )
    # The fitted range is never wider than what the camera can see.
    fit_range_angstrom = (
        max(arguments.minimum_wavelength, camera_range_angstrom[0]),
        min(arguments.maximum_wavelength, camera_range_angstrom[1]),
    )
    response = derive_instrument_response(
        np.array(spectroscopy.wavelengths_angstrom),
        np.array(intensity),
        arguments.reference_type,
        pipeline.config.camera.name,
        source=(
            f"{arguments.target} master spectral stack {Path(stacked_path).name}, brightest detection, "
            f"derived {datetime.now(UTC).date().isoformat()}"
        ),
        wavelength_range_angstrom=fit_range_angstrom,
    )
    file_name = (
        "instrument_response_" + re.sub(r"[^a-z0-9]+", "_", response.camera_name.lower()).strip("_") + ".json"
    )
    payload = {
        "camera_name": response.camera_name,
        "coefficients": list(response.coefficients),
        "minimum_wavelength_angstrom": response.minimum_wavelength_angstrom,
        "maximum_wavelength_angstrom": response.maximum_wavelength_angstrom,
        "reference_type": response.reference_type,
        "source": response.source,
    }
    (DATA_DIR / file_name).write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {DATA_DIR / file_name}")
    return 0


if __name__ == "__main__":
    sys.exit(run_derivation())
