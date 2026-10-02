"""Purpose: Unit tests for choosing the standard star in the response script.

Description: The response is only as good as the star it is derived from. The
first version took the brightest detected source, which on a rebuilt Vega
stack was a bright star at the frame edge, not Vega. These tests pin the
rule that replaced it: the source nearest the frame centre, or the position
the caller gives.
"""

import pytest

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.scripts.derive_instrument_response import select_standard_star

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
    with pytest.raises(ValueError, match="--star-position"):
        select_standard_star([_star("wrong", 1528.0, 1527.0)], IMAGE_SHAPE)


def test_with_no_sources_the_script_asks_for_a_position() -> None:
    """An empty detection list gives the same request, not a crash."""
    with pytest.raises(ValueError, match="--star-position"):
        select_standard_star([], IMAGE_SHAPE)


def test_a_given_position_is_used_even_when_nothing_was_detected_there() -> None:
    """A saturated zero order the detector rejected can still be named."""
    star = select_standard_star([_star("wrong", 1528.0, 1527.0)], IMAGE_SHAPE, star_position=(1494.0, 1502.0))

    assert star.star_data["xcentroid"] == pytest.approx(1494.0)
    assert star.star_data["ycentroid"] == pytest.approx(1502.0)
