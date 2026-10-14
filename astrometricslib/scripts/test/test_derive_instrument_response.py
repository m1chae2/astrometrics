"""Purpose: Unit tests for choosing the standard star in the response script.

Description: The response is only as good as the star it is derived from. The
first version took the brightest detected source, which on a rebuilt Vega
stack was a bright star at the frame edge, not Vega. These tests pin the
rule that replaced it: the source nearest the frame centre, or the position
the caller gives.

They also check the file writer: the stored response must carry the
``reference_airmass`` the pipeline needs for its airmass correction.
"""

import json
from pathlib import Path

import pytest

from astrometricslib.foundation.errors import ProcessingError
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response import InstrumentResponse
from astrometricslib.scripts.derive_instrument_response import (
    DATA_DIR,
    build_response_payload,
    response_file_name,
    select_standard_star,
    write_response_file,
)

IMAGE_SHAPE = (3008, 3008)


def _star(name: str, x_position: float, y_position: float, key_style: str = "xcentroid") -> StellarObject:
    x_key, y_key = ("x_centroid", "y_centroid") if key_style == "x_centroid" else ("xcentroid", "ycentroid")
    return StellarObject(id=name, name=name, star_data={x_key: x_position, y_key: y_position})


def test_the_source_nearest_the_centre_is_chosen_not_the_first_listed() -> None:
    """A bright edge star listed first must not win."""
    edge_star = _star("edge", 2341.0, 2985.0)
    vega = _star("vega", 1494.0, 1502.0)

    assert select_standard_star([edge_star, vega], IMAGE_SHAPE) is vega


def test_both_spellings_of_the_centroid_keys_are_read() -> None:
    """Detections use either `xcentroid` or `x_centroid`."""
    vega = _star("vega", 1494.0, 1502.0, key_style="x_centroid")

    assert select_standard_star([_star("edge", 100.0, 100.0), vega], IMAGE_SHAPE) is vega


def test_a_source_far_from_the_centre_is_refused_with_a_hint() -> None:
    """When only far-off sources exist, the script asks for a position."""
    with pytest.raises(ProcessingError, match="--star-position"):
        select_standard_star([_star("wrong", 1528.0, 1527.0)], IMAGE_SHAPE)


def test_with_no_sources_the_script_asks_for_a_position() -> None:
    """An empty detection list gives the same request, not a crash."""
    with pytest.raises(ProcessingError, match="--star-position"):
        select_standard_star([], IMAGE_SHAPE)


def test_a_given_position_is_used_even_when_nothing_was_detected_there() -> None:
    """A saturated zero order the detector rejected can still be named."""
    star = select_standard_star([_star("wrong", 1528.0, 1527.0)], IMAGE_SHAPE, star_position=(1494.0, 1502.0))

    assert star.star_data["xcentroid"] == pytest.approx(1494.0)
    assert star.star_data["ycentroid"] == pytest.approx(1502.0)


def _fitted_response(reference_airmass: float | None) -> InstrumentResponse:
    """Build a response like the script's fit would give.

    Parameters
    ----------
    reference_airmass : `float` or `None`
        The airmass of the standard star's observation.

    Returns
    -------
    response : `InstrumentResponse`
        A response with made-up coefficients.
    """
    return InstrumentResponse(
        camera_name="ZWO ASI 533MM Pro",
        coefficients=(0.5, 0.3, -0.8, -0.3, 10.9),
        minimum_wavelength_angstrom=4200.0,
        maximum_wavelength_angstrom=8000.0,
        reference_type="A0V",
        source="test star",
        reference_airmass=reference_airmass,
    )


def test_the_payload_includes_the_reference_airmass() -> None:
    """The stored fields carry the airmass the response was derived at."""
    payload = build_response_payload(_fitted_response(1.15))

    assert payload["reference_airmass"] == pytest.approx(1.15)
    assert payload["coefficients"] == [0.5, 0.3, -0.8, -0.3, 10.9]


def test_the_payload_has_the_same_keys_as_the_stored_response_file() -> None:
    """The writer's keys match the stored response file's keys."""
    stored_file = DATA_DIR / response_file_name("ZWO ASI 533MM Pro")
    stored = json.loads(stored_file.read_text())

    assert set(build_response_payload(_fitted_response(1.15))) == set(stored)


def test_the_written_file_holds_the_reference_airmass(tmp_path: Path) -> None:
    """The file written to disk holds the `reference_airmass` value."""
    path = write_response_file(_fitted_response(1.32), tmp_path)

    assert path == tmp_path / "instrument_response_zwo_asi_533mm_pro.json"
    assert json.loads(path.read_text())["reference_airmass"] == pytest.approx(1.32)


def test_an_unknown_airmass_is_written_as_null(tmp_path: Path) -> None:
    """With no airmass the key is still present, and holds `null`."""
    path = write_response_file(_fitted_response(None), tmp_path)

    written = json.loads(path.read_text())
    assert "reference_airmass" in written
    assert written["reference_airmass"] is None
