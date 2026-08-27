"""Purpose: Unit tests for coordinate string parsing.

Description: Verifies sexagesimal and decimal RA/Dec string parsing into
decimal degrees, and the error behavior for empty or unparseable input.
"""

import pytest

from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string


def test_parse_ra_sexagesimal_hours():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an hour-angle RA string parses to the expected degrees."""
    ra_deg = parse_coordinate_string("13h 29m 52.7s", is_ra=True)
    assert ra_deg == pytest.approx(202.469583, abs=1e-4)


def test_parse_dec_sexagesimal_degrees():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a degree-based Dec string parses to the expected degrees."""
    dec_deg = parse_coordinate_string("+47d 11m 43s", is_ra=False)
    assert dec_deg == pytest.approx(47.195278, abs=1e-4)


def test_parse_dec_with_symbol_delimiters():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify degree/arcmin/arcsec symbol delimiters are accepted."""
    dec_deg = parse_coordinate_string("47° 11′ 43″", is_ra=False)
    assert dec_deg == pytest.approx(47.195278, abs=1e-4)


def test_parse_empty_string_raises():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an empty coordinate string raises ValueError."""
    with pytest.raises(ValueError):
        parse_coordinate_string("", is_ra=True)


def test_parse_whitespace_only_raises():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a whitespace-only coordinate string raises ValueError."""
    with pytest.raises(ValueError):
        parse_coordinate_string("   ", is_ra=True)


def test_parse_non_string_raises_value_error():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a non-string input raises ValueError, not AttributeError."""
    with pytest.raises(ValueError):
        parse_coordinate_string(123, is_ra=True)  # type: ignore[arg-type]


def test_parse_declination_out_of_range_raises():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a declination outside -90 to 90 degrees raises ValueError."""
    with pytest.raises(ValueError):
        parse_coordinate_string("91", is_ra=False)
    with pytest.raises(ValueError):
        parse_coordinate_string("-91", is_ra=False)


def test_parse_declination_at_boundary_is_valid():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify declinations at exactly -90 and 90 degrees are accepted."""
    assert parse_coordinate_string("90", is_ra=False) == pytest.approx(90.0)
    assert parse_coordinate_string("-90", is_ra=False) == pytest.approx(-90.0)


def test_parse_unit_marker_only_string_raises():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a string of only a unit marker (no value) raises ValueError."""
    with pytest.raises(ValueError):
        parse_coordinate_string("h", is_ra=True)


def test_parse_colon_separated_string():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify colon-separated sexagesimal RA strings are accepted."""
    ra_deg = parse_coordinate_string("09:55:33", is_ra=True)
    assert ra_deg == pytest.approx(148.8875, abs=1e-4)
