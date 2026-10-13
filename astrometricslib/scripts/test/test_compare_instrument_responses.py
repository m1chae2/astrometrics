"""Purpose: Tests for the instrument-response comparison script.

Description: The script compares two stored responses after scaling both to 1
at 5500 A. These tests check that two identical files agree exactly, that a
response with a known tilt gives the known difference, that a plain change of
overall brightness is ignored, that the exit code follows the tolerance, and
that unreadable or non-overlapping files give exit code 2.
"""

import dataclasses
import json
from pathlib import Path

import numpy as np
import pytest

from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response import (
    InstrumentResponse,
    read_instrument_response_file,
)
from astrometricslib.scripts.compare_instrument_responses import (
    compare_instrument_responses,
    run_comparison,
)
from astrometricslib.scripts.derive_instrument_response import (
    DATA_DIR,
    build_response_payload,
    response_file_name,
)

# The scaled wavelength is (wavelength - 6000) / 2000, so adding T to the
# linear coefficient multiplies the response by exp(T * scaled wavelength).
# Scaled to 1 at 5500 A, the second response over the first is then
# exp(T * (wavelength - 5500) / 2000).
TILT_COEFFICIENT = 0.05


def base_response() -> InstrumentResponse:
    """Build a response like the stored one.

    Returns
    -------
    response : `InstrumentResponse`
        A response over 4200-8000 A.
    """
    return InstrumentResponse(
        camera_name="ZWO ASI 533MM Pro",
        coefficients=(0.5, 0.3, -0.8, -0.3, 10.9),
        minimum_wavelength_angstrom=4200.0,
        maximum_wavelength_angstrom=8000.0,
        reference_type="A0V",
        source="test star",
        reference_airmass=1.15,
    )


def tilted(response: InstrumentResponse, tilt: float) -> InstrumentResponse:
    """Give a copy of a response with a tilt added.

    Parameters
    ----------
    response : `InstrumentResponse`
        The response to copy.
    tilt : `float`
        Added to the linear coefficient.

    Returns
    -------
    tilted : `InstrumentResponse`
        The copy.
    """
    coefficients = list(response.coefficients)
    coefficients[-2] += tilt
    return dataclasses.replace(response, coefficients=tuple(coefficients))


def write(response: InstrumentResponse, path: Path) -> Path:
    """Write a response to a JSON file.

    Parameters
    ----------
    response : `InstrumentResponse`
        The response.
    path : `pathlib.Path`
        The file to write.

    Returns
    -------
    path : `pathlib.Path`
        The same path.
    """
    path.write_text(json.dumps(build_response_payload(response)))
    return path


def test_identical_responses_agree_exactly() -> None:
    """Two copies of one response differ by nothing and have equal tilt."""
    comparison = compare_instrument_responses(base_response(), base_response())

    assert comparison.maximum_fractional_difference == pytest.approx(0.0, abs=1e-15)
    assert comparison.rms_fractional_difference == pytest.approx(0.0, abs=1e-15)
    assert comparison.tilt_difference == pytest.approx(0.0, abs=1e-15)
    assert comparison.range_angstrom == (4200.0, 8000.0)
    assert comparison.is_within(0.0)


def test_a_known_tilt_gives_the_known_differences() -> None:
    """A tilt of T gives exp(T * (wavelength - 5500) / 2000) - 1."""
    comparison = compare_instrument_responses(base_response(), tilted(base_response(), TILT_COEFFICIENT))

    grid = np.arange(4200.0, 8000.5, 1.0)
    expected = np.exp(TILT_COEFFICIENT * (grid - 5500.0) / 2000.0) - 1.0
    assert comparison.maximum_fractional_difference == pytest.approx(np.exp(0.0625) - 1.0)
    assert comparison.rms_fractional_difference == pytest.approx(float(np.sqrt(np.mean(expected**2))))
    # Red to blue is 7000 A over 4500 A: the tilt grows by
    # exp(T * 2500 / 2000).
    assert comparison.tilt_difference == pytest.approx(np.exp(0.0625) - 1.0)
    assert comparison.second_tilt == pytest.approx(comparison.first_tilt * np.exp(0.0625))


def test_a_change_of_overall_brightness_is_ignored() -> None:
    """Scaling a response by a constant changes nothing after normalizing."""
    brighter = dataclasses.replace(base_response(), coefficients=(0.5, 0.3, -0.8, -0.3, 10.9 + np.log(3.0)))

    comparison = compare_instrument_responses(base_response(), brighter)

    assert comparison.maximum_fractional_difference == pytest.approx(0.0, abs=1e-12)


def test_only_the_common_range_is_compared() -> None:
    """The range runs from the later start to the earlier end."""
    narrower = dataclasses.replace(
        base_response(), minimum_wavelength_angstrom=4400.0, maximum_wavelength_angstrom=7500.0
    )

    with pytest.raises(InvalidArgumentError, match="red wavelength 7800"):
        compare_instrument_responses(base_response(), narrower, red_angstrom=7800.0)
    comparison = compare_instrument_responses(base_response(), narrower)
    assert comparison.range_angstrom == (4400.0, 7500.0)


def test_responses_with_no_common_range_are_refused() -> None:
    """Two responses that never overlap cannot be compared."""
    blue = dataclasses.replace(
        base_response(), minimum_wavelength_angstrom=3000.0, maximum_wavelength_angstrom=4000.0
    )

    with pytest.raises(InvalidArgumentError, match="no wavelength range"):
        compare_instrument_responses(blue, base_response())


def test_exit_code_is_zero_for_identical_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Identical files give 0 and print zero differences."""
    first = write(base_response(), tmp_path / "first.json")
    second = write(base_response(), tmp_path / "second.json")

    code = run_comparison([str(first), str(second)])

    assert code == 0
    output = capsys.readouterr().out
    assert "Largest fractional difference: 0.0000" in output
    assert "Within the tolerance of 0.02" in output


def test_exit_code_follows_the_tolerance(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A 6.4 percent tilt fails at 0.02 and passes at 0.1."""
    first = write(base_response(), tmp_path / "first.json")
    second = write(tilted(base_response(), TILT_COEFFICIENT), tmp_path / "second.json")

    assert run_comparison([str(first), str(second)]) == 1
    assert "Above the tolerance of 0.02" in capsys.readouterr().out
    assert run_comparison([str(first), str(second), "--tolerance", "0.1"]) == 0


def test_a_small_tilt_is_within_the_default_tolerance(tmp_path: Path) -> None:
    """A tilt of 0.01 gives under 1.3 percent and passes at 0.02."""
    first = write(base_response(), tmp_path / "first.json")
    second = write(tilted(base_response(), 0.01), tmp_path / "second.json")

    assert run_comparison([str(first), str(second)]) == 0


def test_exit_code_is_two_for_an_unreadable_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A missing file or a file that is no response gives 2."""
    good = write(base_response(), tmp_path / "good.json")
    bad = tmp_path / "bad.json"
    bad.write_text('{"camera_name": "x"}')

    assert run_comparison([str(good), str(tmp_path / "missing.json")]) == 2
    assert run_comparison([str(good), str(bad)]) == 2
    assert "not a readable instrument response file" in capsys.readouterr().out


def test_the_stored_response_agrees_with_itself() -> None:
    """The response shipped for the ASI 533 matches itself."""
    path = DATA_DIR / response_file_name("ZWO ASI 533MM Pro")

    assert read_instrument_response_file(path) == read_instrument_response_file(path)
    assert run_comparison([str(path), str(path)]) == 0
