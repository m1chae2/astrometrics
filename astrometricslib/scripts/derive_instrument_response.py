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
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.astrometry.pipeline import AstrometryPipeline
from astrometricslib.pipelines.spectroscopy.instrument_response import (
    DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM,
    derive_instrument_response,
)
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
from astrometricslib.pipelines.spectroscopy.spectral_resolution import resolve_resolution_element_angstrom

DATA_DIR = Path(__file__).resolve().parents[1] / "pipelines" / "spectroscopy" / "data"

# How far, in pixels, the standard star's zero order may sit from the frame
# centre before the script refuses to guess. The telescope is pointed at the
# standard star, so it lands near the centre. Validated only on Vega: the
# original stack put the star 10 px from the centre, and in the rebuilt stack
# (where the star was not detected) the nearest wrong source was 33 px away.
# 25 px falls between those two cases; it has not been tested on other stars.
MAXIMUM_STAR_OFFSET_FROM_CENTER_PX = 25.0


def select_standard_star(
    stellar_objects: list[StellarObject],
    image_shape: tuple[int, int],
    star_position: tuple[float, float] | None = None,
    maximum_offset_px: float = MAXIMUM_STAR_OFFSET_FROM_CENTER_PX,
) -> StellarObject:
    """Choose which detected source is the standard star.

    The brightest source is not always the standard star: a bright star
    near the frame edge can outshine it. The standard star is the one the
    telescope was pointed at, so it is the source closest to the frame
    centre, unless the caller says where it is.

    Parameters
    ----------
    stellar_objects : `list` [`StellarObject`]
        The detected sources.
    image_shape : `tuple` [`int`, `int`]
        The image's ``(height, width)`` in pixels.
    star_position : `tuple` [`float`, `float`], optional
        The star's ``(x, y)`` pixel position. When given, it is used as it
        is, even if the detector did not list a source there (a saturated,
        flat-topped zero order is sometimes rejected as a source).
    maximum_offset_px : `float`, optional
        The farthest a detected source may be from the frame centre and
        still be taken as the star.

    Returns
    -------
    star : `StellarObject`
        The chosen star.

    Raises
    ------
    ValueError
        If no source lies within ``maximum_offset_px`` of the frame centre
        and no position was given.
    """
    if star_position is not None:
        x_position, y_position = float(star_position[0]), float(star_position[1])
        return StellarObject(
            id="manual_standard_star",
            name="Standard star (position given)",
            star_data={"xcentroid": x_position, "ycentroid": y_position},
        )

    height, width = image_shape
    best_star, best_offset = None, float("inf")
    for star in stellar_objects:
        # Detections name the centroid `x_centroid` or `xcentroid`.
        x_position = star.star_data.get("x_centroid", star.star_data.get("xcentroid"))
        y_position = star.star_data.get("y_centroid", star.star_data.get("ycentroid"))
        if x_position is None or y_position is None:
            continue
        offset = float(np.hypot(x_position - width / 2, y_position - height / 2))
        if offset < best_offset:
            best_star, best_offset = star, offset

    if best_star is None or best_offset > maximum_offset_px:
        raise ValueError(
            f"No detected source lies within {maximum_offset_px:.0f} px of the frame centre "
            f"(nearest: {best_offset:.0f} px). Pass the star's pixel position with --star-position X Y."
        )
    return best_star


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
        stack, no source is near the frame centre, or nothing could be
        extracted.
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
    arguments = parser.parse_args(argv)

    astrometrics = Astrometrics()
    target = astrometrics.targets.get(arguments.target)
    stacked_path = getattr(target, "stacked_spectral_target", None) if target else None
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
    except ValueError as selection_error:
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
    response = derive_instrument_response(
        np.array(spectroscopy.wavelengths_angstrom),
        np.array(intensity),
        arguments.reference_type,
        pipeline.config.camera.name,
        source=(
            f"{arguments.target} master spectral stack {Path(stacked_path).name}, star at pixel "
            f"({star_x:.0f}, {star_y:.0f}), "
            f"derived {datetime.now(UTC).date().isoformat()}"
        ),
        wavelength_range_angstrom=fit_range_angstrom,
        resolution_element_angstrom=resolution_element_angstrom,
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
