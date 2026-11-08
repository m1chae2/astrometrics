"""Tests that `find_star` keeps its map of star ids until the catalog changes.

Looking a star up by a different spelling (``HD_0004`` for ``HD 0004``)
used to read every star id each time, about half a second in a large
library. These tests check that the answer is unchanged, that the ids are
read once while the catalog is unchanged, and that a write makes the map
be rebuilt.
"""

from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from astrometricslib._internal.drivers.catalog_access import CatalogAccess
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.shared.star_catalog_queries import find_star


@pytest.fixture
def storage(tmp_path: Path) -> CatalogAccess:
    """Provide a temporary library holding two stars.

    Returns
    -------
    storage : `CatalogAccess`
        The catalog storage over the temporary database.
    """
    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    (library_path / "frames").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    access = CatalogAccess(config)
    access.put(
        [
            StellarObject(id="HD 0004", name="A", ra=1.0, dec=2.0),
            StellarObject(id="HIP 35136", name="B", ra=3.0, dec=4.0),
        ],
        "stellar_catalog",
        {},
    )
    return access


def test_find_star_matches_other_spellings(storage: CatalogAccess) -> None:
    """Spelling differences do not matter; a missing star gives `None`."""
    assert find_star(storage, "HD 0004").id == "HD 0004"
    assert find_star(storage, "hd_0004").id == "HD 0004"
    assert find_star(storage, "HIP_35136").id == "HIP 35136"
    assert find_star(storage, "HIP_99999") is None


def test_find_star_reads_the_ids_once_while_the_catalog_is_unchanged(
    storage: CatalogAccess, mocker: MockerFixture
) -> None:
    """Later spelling lookups do not read the id column again."""
    spy = mocker.spy(storage, "list_star_ids")
    find_star(storage, "HIP_35136")
    find_star(storage, "hip35136")
    find_star(storage, "HIP_99999")
    assert spy.call_count == 1


def test_find_star_rebuilds_the_map_after_a_write(storage: CatalogAccess, mocker: MockerFixture) -> None:
    """A star added later is found by another spelling; ids are read again."""
    assert find_star(storage, "HIP_40000") is None
    storage.put([StellarObject(id="HIP 40000", name="C", ra=5.0, dec=6.0)], "stellar_catalog", {})
    spy = mocker.spy(storage, "list_star_ids")
    assert find_star(storage, "HIP_40000").id == "HIP 40000"
    assert spy.call_count == 1


def test_find_star_does_not_cache_for_a_storage_without_a_version(mocker: MockerFixture) -> None:
    """A storage with no write count is read every time."""
    storage = mocker.Mock()
    storage.get_by_ids.side_effect = lambda _kind, ids: [mocker.Mock(id=ids[0])] if ids == ["HD 1"] else []
    storage.get_dataset_version.return_value = None
    storage.list_star_ids.return_value = ["HD 1"]
    assert find_star(storage, "hd_1").id == "HD 1"
    assert find_star(storage, "hd_1").id == "HD 1"
    assert storage.list_star_ids.call_count == 2
