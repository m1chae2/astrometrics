"""Tests for datastore.local_database's NumpyEncoder / safe_json_dumps.

Both astrometricslib.drivers.local_database (the targets table) and the
generic datastore.butler.Butler (used for stellar_catalog and friends)
route every save through safe_json_dumps, so this is the one place that
decides whether a scientific value (numpy, astropy, pandas) saved into a
model's field crashes the save or degrades safely.
"""

import json

import astropy.units as u
import numpy as np
import pandas as pd
import pytest

from datastore.butler import Butler, DatasetSpec
from datastore.local_database import safe_json_dumps


def test_safe_json_dumps_handles_numpy_scalars_and_arrays():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Numpy scalar and array values must round-trip as plain JSON types."""
    payload = {
        "an_int": np.int64(7),
        "a_float": np.float64(3.5),
        "an_array": np.array([1.0, 2.0, 3.0]),
    }
    decoded = json.loads(safe_json_dumps(payload))
    assert decoded == {"an_int": 7, "a_float": 3.5, "an_array": [1.0, 2.0, 3.0]}


def test_safe_json_dumps_handles_astropy_quantity():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A Quantity must serialize with its unit preserved, not crash."""
    payload = {"separation": 12.5 * u.arcsec}
    decoded = json.loads(safe_json_dumps(payload))
    assert decoded == {"separation": {"value": 12.5, "unit": "arcsec"}}


def test_safe_json_dumps_handles_pandas_series_and_dataframe():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A pandas Series/DataFrame must serialize to plain lists, not crash."""
    payload = {
        "series": pd.Series([1.0, 2.0, 3.0]),
        "frame": pd.DataFrame({"x": [1, 2], "y": [3, 4]}),
    }
    decoded = json.loads(safe_json_dumps(payload))
    assert decoded["series"] == [1.0, 2.0, 3.0]
    assert decoded["frame"] == {"x": [1, 2], "y": [3, 4]}


class _FakeConfig:
    """Minimal config stand-in exposing get_library_path()."""

    def __init__(self, library_path: str):  # ruff: ignore[missing-return-type-special-method]
        self._library_path = library_path

    def get_library_path(self) -> str:
        """Return the library root path.

        Returns
        -------
        path : `str`
            The configured library root path.
        """
        return self._library_path


def test_stellar_object_with_scientific_types_in_any_fields_round_trips(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A star carrying numpy/Quantity/pandas values in its Any-typed fields.

    (flux, magnitude, star_data) must save and reload through the real
    Butler without raising -- these fields have no pydantic type
    validation, so nothing but this encoder stands between an
    un-serializable scientific value and a crash on save.
    """
    from astrometricslib import StellarObject

    star = StellarObject(id="star-1", name="Test Star")
    star.flux = 12.5 * u.arcsec
    star.magnitude = np.float64(8.2)
    star.star_data = pd.Series({"xcentroid": 10.0, "ycentroid": 20.0})

    butler = Butler(config=_FakeConfig(str(tmp_path)))
    butler.register_dataset_type(
        "stellar_object", DatasetSpec(table_name="stellar_objects", model_class=StellarObject)
    )

    butler.put(star, "stellar_object")
    (reloaded,) = butler.get_all("stellar_object")

    assert reloaded.id == "star-1"
    assert reloaded.flux == {"value": 12.5, "unit": "arcsec"}
    assert reloaded.magnitude == pytest.approx(8.2)
    assert reloaded.star_data == [10.0, 20.0]
