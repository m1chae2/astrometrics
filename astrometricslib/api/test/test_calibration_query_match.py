"""Tests for the ``match`` detail and camera totals of the calibration query.

The library is a stand-in that returns fixed counts rows, so the tests check
only what the query adds: row keys, camera totals and the matching rules.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.api.processing import CalibrationCatalog
from astrometricslib.foundation.errors import InvalidArgumentError

STATS = {
    "darks": [
        {
            "camera": "ASI533",
            "iso": "100",
            "offset": 10.0,
            "binning": "1x1",
            "temperature_c": -9.0,
            "exposure": 60.0,
            "count": 20,
        },
    ],
    "biases": [
        {"camera": "ASI533", "iso": "100", "offset": 10.0, "binning": "2x2", "count": 30},
        {"camera": "ASI533", "iso": "0", "offset": 10.0, "binning": "1x1", "count": 5},
        {"camera": "D5300", "iso": "800", "offset": 0.0, "binning": "1x1", "count": 7},
    ],
    "flats": [
        {"camera": "ASI533", "filter": "L", "iso": "100", "offset": 10.0, "binning": "2x2", "count": 25},
    ],
}


@pytest.fixture
def catalog() -> CalibrationCatalog:
    """Make a catalog over a stand-in library.

    Returns
    -------
    catalog : `CalibrationCatalog`
        A catalog whose library gives `STATS`.
    """
    catalog = CalibrationCatalog(None, None)
    catalog._library = SimpleNamespace(get_stats=lambda: STATS, load_library=lambda: None)
    return catalog


def test_counts_rows_carry_row_keys_and_cameras_carry_totals(catalog: CalibrationCatalog) -> None:
    """Every counts row has a unique key, and each camera has its totals."""
    result = catalog.query()

    keys = [row["row_key"] for rows in (result.darks, result.biases, result.flats) for row in rows]
    assert len(set(keys)) == len(keys) == 5
    assert result.camera_totals == [
        {"camera": "ASI533", "dark_count": 20, "bias_count": 35, "flat_count": 25},
        {"camera": "D5300", "dark_count": 0, "bias_count": 7, "flat_count": 0},
    ]


def test_match_normalizes_binning_and_compares_gain_as_numbers(catalog: CalibrationCatalog) -> None:
    """Binning "2" equals "2x2", and gain "100.0" equals "100"."""
    result = catalog.query(
        detail="match", kind="bias", camera_id="asi533", gain="100.0", offset=10.0, binning="2"
    )

    biases = {row["row_key"] for row in catalog.query().biases if row["binning"] == "2x2"}
    assert set(result.matched_row_keys) == biases
    assert result.camera_totals[0]["bias_count"] == 35


def test_match_without_a_kind_looks_at_every_kind_and_uses_the_dark_temperature_slot(
    catalog: CalibrationCatalog,
) -> None:
    """A dark matches on its temperature slot; a flat ignores temperature."""
    result = catalog.query(detail="match", camera_id="ASI533", gain=100, binning=1, temperature_c=-10.2)
    all_rows = catalog.query()

    assert result.matched_row_keys == [all_rows.darks[0]["row_key"]]
    flat = catalog.query(detail="match", kind="flat", camera_id="ASI533", binning="2x2", temperature_c=5.0)
    assert flat.matched_row_keys == [all_rows.flats[0]["row_key"]]


def test_match_needs_a_camera_and_refuses_unrelated_arguments(catalog: CalibrationCatalog) -> None:
    """The match detail needs `camera_id`; others refuse the new arguments."""
    with pytest.raises(InvalidArgumentError):
        catalog.query(detail="match", gain=100)
    with pytest.raises(InvalidArgumentError):
        catalog.query(detail="counts", gain=100)
