"""Tests for `calibration.query` and its capture-setting matching.

The library is a stand-in that returns fixed counts rows, so the tests check
only what the query adds: the rows, their keys, paging and the matching
rules of a `CaptureSetting`.
"""

from types import SimpleNamespace

import pytest

from astrometricslib import AppConfiguration, CaptureSetting
from astrometricslib.api.calibration import CalibrationCatalog
from astrometricslib.foundation.errors import InvalidArgumentError

STATS = {
    "darks": [
        {
            "camera": "ASI533",
            "gain": "100",
            "offset": 10.0,
            "binning": "1x1",
            "temperature_c": -9.0,
            "exposure_s": 60.0,
            "count": 20,
        },
    ],
    "biases": [
        {"camera": "ASI533", "gain": "100", "offset": 10.0, "binning": "2x2", "count": 30},
        {"camera": "ASI533", "gain": "0", "offset": 10.0, "binning": "1x1", "count": 5},
        {"camera": "D5300", "gain": "800", "offset": 0.0, "binning": "1x1", "count": 7},
    ],
    "flats": [
        {"camera": "ASI533", "filter": "L", "gain": "100", "offset": 10.0, "binning": "2x2", "count": 25},
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
    catalog = CalibrationCatalog(AppConfiguration(), None)
    catalog._library = SimpleNamespace(get_stats=lambda: STATS)
    return catalog


def test_counts_rows_carry_unique_row_keys_in_kind_order(catalog: CalibrationCatalog) -> None:
    """Each row has a unique key, darks first; a kind narrows the page."""
    page = catalog.query()

    assert [row.kind for row in page] == ["dark", "bias", "bias", "bias", "flat"]
    assert len({row.row_key for row in page}) == page.total == 5
    assert [row.count for row in catalog.query("bias")] == [30, 5, 7]
    dark = catalog.query("dark")[0]
    assert (dark.gain, dark.offset, dark.binning, dark.temperature_c, dark.exposure_s) == (
        100,
        10.0,
        "1x1",
        -9.0,
        60.0,
    )
    assert catalog.query("flat")[0].filter_name == "L"


def test_paging_cuts_the_rows(catalog: CalibrationCatalog) -> None:
    """A limit gives a page with the next offset; the total counts all."""
    page = catalog.query(limit=2)

    assert len(page) == 2
    assert page.total == 5
    assert page.next_offset == 2


def test_a_setting_normalizes_binning_and_compares_gain_as_numbers(catalog: CalibrationCatalog) -> None:
    """Binning 2 equals "2x2", and the camera matches ignoring case."""
    matched = catalog.query("bias", setting=CaptureSetting(camera="asi533", gain=100, offset=10, binning=2))

    assert [(row.camera, row.binning, row.count) for row in matched] == [("ASI533", "2x2", 30)]


def test_a_setting_without_a_kind_uses_the_dark_temperature_slot_and_exposure(
    catalog: CalibrationCatalog,
) -> None:
    """A dark matches on temperature slot and exposure; a flat on filter."""
    setting = CaptureSetting(camera="ASI533", gain=100, binning=1, temperature_c=-10.2)
    assert [row.kind for row in catalog.query(setting=setting)] == ["dark"]
    assert catalog.query("dark", setting=setting.model_copy(update={"exposure_s": 60.02})).total == 1
    assert catalog.query("dark", setting=setting.model_copy(update={"exposure_s": 120.0})).total == 0
    flat = CaptureSetting(camera="ASI533", binning=2, filter_name="Luminance", temperature_c=5.0)
    assert [row.kind for row in catalog.query("flat", setting=flat)] == ["flat"]
    assert catalog.query("flat", setting=flat.model_copy(update={"filter_name": "Ha"})).total == 0


def test_an_unknown_kind_is_refused(catalog: CalibrationCatalog) -> None:
    """Only dark, bias and flat are kinds."""
    with pytest.raises(InvalidArgumentError):
        catalog.query("lamp")  # ty: ignore[invalid-argument-type]
