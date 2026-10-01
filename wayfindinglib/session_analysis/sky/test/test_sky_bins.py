"""Purpose: Unit tests for dividing the sky into comparable parts.

Description: Verifies that altitude, azimuth and pier side are placed in the
right band, sector and side at the edges, and that the list of reachable parts
follows the telescope's configured altitude range.
"""

import pytest

from wayfindinglib.session_analysis.sky.sky_bins import (
    altitude_band_limits,
    altitude_label,
    azimuth_label,
    bins_of,
    pier_label,
    reachable_bins,
)


@pytest.mark.parametrize(
    ("altitude", "label"),
    [
        (-2.0, "0-15 deg"),
        (14.9, "0-15 deg"),
        (15.0, "15-30 deg"),
        (30.0, "30-45 deg"),
        (89.9, "75-90 deg"),
        (90.0, "75-90 deg"),
    ],
)
def test_an_altitude_falls_in_the_band_that_starts_at_or_below_it(altitude: float, label: str) -> None:
    """Verify band edges belong to the band above them, and 90 to the top."""
    assert altitude_label(altitude) == label


def test_an_altitude_band_label_gives_back_its_edges() -> None:
    """Verify the edges can be read from the label."""
    assert altitude_band_limits("30-45 deg") == (30.0, 45.0)


@pytest.mark.parametrize(
    ("azimuth", "label"),
    [
        (0.0, "N"),
        (359.0, "N"),
        (22.4, "N"),
        (22.6, "NE"),
        (90.0, "E"),
        (180.0, "S"),
        (270.0, "W"),
        (315.0, "NW"),
        (-10.0, "N"),
    ],
)
def test_an_azimuth_falls_in_the_sector_centred_on_its_compass_point(azimuth: float, label: str) -> None:
    """Verify sectors are centred on the compass points and wrap at north."""
    assert azimuth_label(azimuth) == label


def test_pier_side_is_normalised_however_it_is_written() -> None:
    """Verify frame headers (capitals) and guide logs (capitalised) agree."""
    assert pier_label("WEST") == "West"
    assert pier_label("east") == "East"
    assert pier_label("Unknown") is None
    assert pier_label(None) is None


def test_a_pointing_belongs_to_one_part_along_each_known_dimension() -> None:
    """Verify unknown dimensions are skipped, not guessed."""
    assert bins_of(50.0, 180.0, "WEST") == [
        ("altitude", "45-60 deg"),
        ("azimuth", "S"),
        ("pier_side", "West"),
    ]
    assert bins_of(None, 180.0, None) == [("azimuth", "S")]
    assert bins_of(None, None, None) == []


def test_reachable_bands_follow_the_configured_altitude_range() -> None:
    """Verify bands wholly outside the telescope's range are not expected."""
    labels = [label for dimension, label in reachable_bins(30.0, 80.0) if dimension == "altitude"]

    assert labels == ["30-45 deg", "45-60 deg", "60-75 deg", "75-90 deg"]
    assert len(reachable_bins(0.0, 90.0)) == 6 + 8 + 2
