r"""Derive the instrument response from a known star's master stack.

Reads the master stacked spectral image of a standard star (Vega by
default, type A0V), extracts its spectrum without saving anything to the
catalog, fits the instrument response (see
`astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response`)
and writes it as ``instrument_response_<camera>.json`` in the spectroscopy
data folder.

    python -m astrometricslib.scripts.derive_instrument_response

Options let you choose another target, reference type or camera. Run it
again after any change to the grating, camera or telescope, since the
response belongs to one setup.

The file also records ``reference_airmass``, the airmass of the standard
star's observation. Airmass is how much air the light crossed (1.0 straight
overhead). The response includes the air's dimming at that airmass, and the
pipeline needs the number to correct a target seen at another airmass (see
`astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_extinction`).
The script reads it from the stack's ``AIRMASS`` header card. If the stack
has none, pass ``--reference-airmass``. With neither, the file stores
``null`` and the pipeline skips the airmass correction.
"""

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from astrometricslib import Astrometrics
from astrometricslib.foundation.errors import ProcessingError
from astrometricslib.pipelines.astrometry.pipeline import AstrometryPipeline
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline, _frame_airmass
from astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_extinction import (
    MAXIMUM_VALID_AIRMASS,
    MINIMUM_VALID_AIRMASS,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response import (
    DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM,
    InstrumentResponse,
    derive_instrument_response,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    load_line_spread_profile,
    resolve_resolution_element_angstrom,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.standard_star_selection import (
    select_standard_star,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "pipelines" / "spectroscopy" / "data"


def build_response_payload(response: InstrumentResponse) -> dict[str, object]:
    """Give the fields of a response as they are stored in its JSON file.

    The keys match what `load_instrument_response` reads.

    Parameters
    ----------
    response : `InstrumentResponse`
        The fitted response.

    Returns
    -------
    payload : `dict` [`str`, `object`]
        The response as plain values. ``reference_airmass`` is the airmass of
        the standard star's observation, or `None` (stored as ``null``) when
        it is not known.
    """
    return {
        "camera_name": response.camera_name,
        "coefficients": list(response.coefficients),
        "minimum_wavelength_angstrom": response.minimum_wavelength_angstrom,
        "maximum_wavelength_angstrom": response.maximum_wavelength_angstrom,
        "reference_type": response.reference_type,
        "source": response.source,
        "reference_airmass": response.reference_airmass,
    }


def response_file_name(camera_name: str) -> str:
    """Give the name of the JSON file that stores a camera's response.

    Parameters
    ----------
    camera_name : `str`
        The camera's name, for example ``ZWO ASI 533MM Pro``.

    Returns
    -------
    file_name : `str`
        ``instrument_response_`` plus the lowercase camera name with every
        run of other characters turned into one underscore, then ``.json``.
    """
    return "instrument_response_" + re.sub(r"[^a-z0-9]+", "_", camera_name.lower()).strip("_") + ".json"


def write_response_file(response: InstrumentResponse, directory: Path = DATA_DIR) -> Path:
    """Write a response to its JSON file.

    Parameters
    ----------
    response : `InstrumentResponse`
        The fitted response.
    directory : `pathlib.Path`, optional
        The folder to write into. Defaults to the spectroscopy data folder.

    Returns
    -------
    path : `pathlib.Path`
        The file that was written.
    """
    path = directory / response_file_name(response.camera_name)
    path.write_text(json.dumps(build_response_payload(response), indent=2) + "\n")
    return path


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
        stack, no source is near the frame centre, nothing could be
        extracted, or the reference airmass is outside 1 to 10.
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
    parser.add_argument(
        "--star-position",
        type=float,
        nargs=2,
        metavar=("X", "Y"),
        default=None,
        help=(
            "The standard star's zero-order pixel position. By default the detected source nearest "
            "the frame centre is used; give this when the detector does not list the star."
        ),
    )
    parser.add_argument(
        "--reference-airmass",
        type=float,
        default=None,
        help=(
            "The airmass the standard star was observed at. By default the stack's AIRMASS header "
            "card is used. Give this when the stack has none; without either, the response is stored "
            "with no reference airmass and the pipeline skips the airmass correction."
        ),
    )
    arguments = parser.parse_args(argv)

    astrometrics = Astrometrics()
    target = astrometrics.targets.get(arguments.target)
    stacked_path = (
        getattr(getattr(target, "spectral_stacking", None), "stacked_image", None) if target else None
    )
    if not stacked_path:
        print(f"Target {arguments.target!r} has no master stacked spectral image.")
        return 1

    context = AstrometryPipeline().process(stacked_path, attempt_plate_solving=False)
    pipeline = SpectroscopyPipeline()
    try:
        star = select_standard_star(
            context.stellar_objects,
            context.image.data.shape,
            tuple(arguments.star_position) if arguments.star_position else None,
        )
    except ProcessingError as selection_error:
        print(selection_error)
        return 1
    star_x = star.star_data.get("x_centroid", star.star_data.get("xcentroid"))
    star_y = star.star_data.get("y_centroid", star.star_data.get("ycentroid"))
    print(f"Standard star taken at pixel ({star_x:.0f}, {star_y:.0f}).")
    # Extract just this star: `process` reads the first listed star.
    context.stellar_objects = [star]
    results = pipeline.process(context, limit=1)
    if not results:
        print("No spectrum could be extracted at that position.")
        return 1
    star = results[0]
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
    # Blur the reference to how blurry this observation really is, measured
    # from its own trail width, so the fitted response does not absorb a
    # mismatch around every line.
    resolution_element_angstrom, is_resolution_measured = resolve_resolution_element_angstrom(
        np.array(spectroscopy.wavelengths_angstrom), spectroscopy.trail_width_px
    )
    print(
        f"Resolution element {resolution_element_angstrom:.1f} A "
        f"({'measured from the trail width' if is_resolution_measured else 'fallback, no trail width'})."
    )
    has_line_spread_profile = load_line_spread_profile(pipeline.config.camera.name) is not None
    blur_basis = "line-spread profile" if has_line_spread_profile else "trail width"
    if has_line_spread_profile:
        print("The reference is blurred with the camera's line-spread profile; the single width is not used.")
    reference_airmass = (
        arguments.reference_airmass
        if arguments.reference_airmass is not None
        else _frame_airmass(context.image)
    )
    if (
        reference_airmass is not None
        and not MINIMUM_VALID_AIRMASS <= reference_airmass <= MAXIMUM_VALID_AIRMASS
    ):
        print(
            f"Reference airmass {reference_airmass} is outside {MINIMUM_VALID_AIRMASS:.0f} to "
            f"{MAXIMUM_VALID_AIRMASS:.0f}. Nothing was written."
        )
        return 1
    if reference_airmass is None:
        print(
            "No reference airmass: the stack has no AIRMASS header card and --reference-airmass was not "
            "given. The response is stored without one, so the pipeline will skip the airmass correction."
        )
    else:
        print(f"Reference airmass {reference_airmass:.3f}.")
    response = derive_instrument_response(
        np.array(spectroscopy.wavelengths_angstrom),
        np.array(intensity),
        arguments.reference_type,
        pipeline.config.camera.name,
        source=(
            f"{arguments.target} master spectral stack {Path(stacked_path).name}, star at pixel "
            f"({star_x:.0f}, {star_y:.0f}), reference blurred with the {blur_basis}, "
            f"derived {datetime.now(UTC).date().isoformat()}"
        ),
        wavelength_range_angstrom=fit_range_angstrom,
        resolution_element_angstrom=resolution_element_angstrom,
        reference_airmass=reference_airmass,
    )
    print(f"Wrote {write_response_file(response)}")
    return 0


if __name__ == "__main__":
    sys.exit(run_derivation())
