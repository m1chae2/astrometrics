r"""Compare two instrument-response files and say whether they agree.

The instrument response (see
`astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response`)
is the smooth tilt the grating, optics and air put on every spectrum. It is
derived from one standard star. If the response is a real property of the
instrument, a second standard star observed on the same night must give the
same response. This script checks that.

    python -m astrometricslib.scripts.compare_instrument_responses \
        response_vega.json response_second_star.json --tolerance 0.02

How it compares them:

1. It evaluates both responses at every Angstrom over their common
   wavelength range (the longest start to the shortest end).
2. It divides each response by its own value at 5500 A, so only the shape
   matters and a difference in overall brightness is ignored.
3. The fractional difference at a wavelength is the second normalized
   response divided by the first, minus 1. The script prints the largest
   absolute difference and the root-mean-square (RMS) difference over the
   range.
4. It also prints the red-to-blue tilt of each response: the normalized value
   at the red wavelength (7000 A by default) divided by the value at the blue
   wavelength (4500 A by default). The difference in tilt is the second tilt
   divided by the first, minus 1.

The exit code is 0 when both the largest difference and the tilt difference
are within ``--tolerance`` (default 0.02). The default is a designed value,
not a measured one: 2 percent is a round number chosen to be small next to
the colour error a wrong response puts on a spectrum, and it has not been
checked against pairs of real standard stars. The exit code is 1 when either
is larger, and 2 when a file cannot be read or the responses do not overlap
at the normalization wavelength.

The script compares the files only. It does not check that both were
derived under the same conditions. Two responses made at different airmasses
differ by the air's extinction (see ``reference_airmass`` in the file), and
this script reports that difference as a mismatch.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response import (
    InstrumentResponse,
    read_instrument_response_file,
)

# The wavelength both responses are scaled to 1 at, in Angstroms.
# Mid-spectrum, where the response is flat and best determined.
DEFAULT_NORMALIZATION_ANGSTROM = 5500.0

# The wavelengths whose normalized values give the red-to-blue tilt.
DEFAULT_BLUE_ANGSTROM = 4500.0
DEFAULT_RED_ANGSTROM = 7000.0

# The largest fractional difference that still counts as agreement. Designed,
# not measured (see the module docstring).
DEFAULT_TOLERANCE = 0.02

# Spacing of the wavelengths the two responses are evaluated at, in Angstroms.
GRID_STEP_ANGSTROM = 1.0


@dataclass(frozen=True)
class ResponseComparison:
    """How two instrument responses differ once scaled to match at 5500 A.

    Attributes
    ----------
    range_angstrom : `tuple` [`float`, `float`]
        The common wavelength range the comparison covers.
    maximum_fractional_difference : `float`
        The largest absolute fractional difference over the range.
    rms_fractional_difference : `float`
        The root-mean-square fractional difference over the range.
    first_tilt : `float`
        The red-to-blue tilt of the first response.
    second_tilt : `float`
        The red-to-blue tilt of the second response.
    tilt_difference : `float`
        ``second_tilt / first_tilt - 1``.
    """

    range_angstrom: tuple[float, float]
    maximum_fractional_difference: float
    rms_fractional_difference: float
    first_tilt: float
    second_tilt: float
    tilt_difference: float

    def is_within(self, tolerance: float) -> bool:
        """Say whether the responses agree to a tolerance.

        Parameters
        ----------
        tolerance : `float`
            The largest fractional difference that counts as agreement.

        Returns
        -------
        agrees : `bool`
            `True` when the largest difference and the tilt difference are
            both within the tolerance.
        """
        return self.maximum_fractional_difference <= tolerance and abs(self.tilt_difference) <= tolerance


def compare_instrument_responses(
    first: InstrumentResponse,
    second: InstrumentResponse,
    normalize_at_angstrom: float = DEFAULT_NORMALIZATION_ANGSTROM,
    blue_angstrom: float = DEFAULT_BLUE_ANGSTROM,
    red_angstrom: float = DEFAULT_RED_ANGSTROM,
) -> ResponseComparison:
    """Compare two responses over their common range.

    Parameters
    ----------
    first : `InstrumentResponse`
        The reference response.
    second : `InstrumentResponse`
        The response to check against it.
    normalize_at_angstrom : `float`, optional
        The wavelength each response is scaled to 1 at.
    blue_angstrom : `float`, optional
        The blue wavelength of the tilt.
    red_angstrom : `float`, optional
        The red wavelength of the tilt.

    Returns
    -------
    comparison : `ResponseComparison`
        The differences.

    Raises
    ------
    InvalidArgumentError
        If the responses share no wavelength range, or the normalization,
        blue or red wavelength lies outside it.
    """
    low = max(first.minimum_wavelength_angstrom, second.minimum_wavelength_angstrom)
    high = min(first.maximum_wavelength_angstrom, second.maximum_wavelength_angstrom)
    if low >= high:
        raise InvalidArgumentError("The two responses share no wavelength range.")
    for name, wavelength in (
        ("normalization", normalize_at_angstrom),
        ("blue", blue_angstrom),
        ("red", red_angstrom),
    ):
        if not low <= wavelength <= high:
            raise InvalidArgumentError(
                f"The {name} wavelength {wavelength:g} A is outside the common range {low:g}-{high:g} A."
            )

    grid = np.arange(low, high + 0.5 * GRID_STEP_ANGSTROM, GRID_STEP_ANGSTROM)
    anchor = np.array([normalize_at_angstrom])
    first_curve = first.value_at(grid) / first.value_at(anchor)[0]
    second_curve = second.value_at(grid) / second.value_at(anchor)[0]
    difference = second_curve / first_curve - 1.0

    tilt_points = np.array([blue_angstrom, red_angstrom])
    first_blue, first_red = first.value_at(tilt_points) / first.value_at(anchor)[0]
    second_blue, second_red = second.value_at(tilt_points) / second.value_at(anchor)[0]
    first_tilt = float(first_red / first_blue)
    second_tilt = float(second_red / second_blue)
    return ResponseComparison(
        range_angstrom=(float(low), float(high)),
        maximum_fractional_difference=float(np.max(np.abs(difference))),
        rms_fractional_difference=float(np.sqrt(np.mean(difference**2))),
        first_tilt=first_tilt,
        second_tilt=second_tilt,
        tilt_difference=second_tilt / first_tilt - 1.0,
    )


def run_comparison(argv: list[str] | None = None) -> int:
    """Compare two response files and print the result.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        0 when the responses agree within the tolerance, 1 when they do not,
        2 when a file cannot be read or the responses cannot be compared.
    """
    parser = argparse.ArgumentParser(description="Compare two instrument-response JSON files.")
    parser.add_argument("first", type=Path, help="The reference response file.")
    parser.add_argument("second", type=Path, help="The response file to check against it.")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        help="Largest fractional difference (maximum or tilt) that counts as agreement. Default 0.02.",
    )
    parser.add_argument(
        "--normalize-at",
        type=float,
        default=DEFAULT_NORMALIZATION_ANGSTROM,
        help="Wavelength, in Angstroms, both responses are scaled to 1 at. Default 5500.",
    )
    parser.add_argument(
        "--blue", type=float, default=DEFAULT_BLUE_ANGSTROM, help="Blue wavelength of the tilt. Default 4500."
    )
    parser.add_argument(
        "--red", type=float, default=DEFAULT_RED_ANGSTROM, help="Red wavelength of the tilt. Default 7000."
    )
    arguments = parser.parse_args(argv)

    try:
        first = read_instrument_response_file(arguments.first)
        second = read_instrument_response_file(arguments.second)
        comparison = compare_instrument_responses(
            first, second, arguments.normalize_at, arguments.blue, arguments.red
        )
    except InvalidArgumentError as error:
        print(error)
        return 2

    low, high = comparison.range_angstrom
    print(f"Common range {low:g}-{high:g} A, both scaled to 1 at {arguments.normalize_at:g} A.")
    print(f"Largest fractional difference: {comparison.maximum_fractional_difference:.4f}")
    print(f"RMS fractional difference:     {comparison.rms_fractional_difference:.4f}")
    print(
        f"Red-to-blue tilt ({arguments.red:g} A over {arguments.blue:g} A): "
        f"{comparison.first_tilt:.4f} and {comparison.second_tilt:.4f}, "
        f"difference {comparison.tilt_difference:+.4f}"
    )
    if comparison.is_within(arguments.tolerance):
        print(f"Within the tolerance of {arguments.tolerance:g}.")
        return 0
    print(f"Above the tolerance of {arguments.tolerance:g}: the two responses disagree.")
    return 1


if __name__ == "__main__":
    sys.exit(run_comparison())
