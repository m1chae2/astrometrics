"""Purpose: Unit tests for the CatalogAccess layer and its integration.

Description: Verifies that CatalogAccess resolves paths and catalogs
correctly, and that a mock catalog_access can be injected to isolate scientific
core logic.
"""

from typing import Any
from unittest.mock import MagicMock

from astrometricslib import AbstractCatalogAccess, Astrometrics, CatalogAccess, StellarObject, Target
from astrometricslib.models.target import FrameRecord


class MockCatalogAccess(AbstractCatalogAccess):
    """A mock CatalogAccess for testing in-memory data flows."""

    def __init__(self):  # ruff: ignore[missing-return-type-special-method]
        self.targets = [Target(id="M 31"), Target(id="Orion")]
        self.stellar_objects = [StellarObject(id="Star1"), StellarObject(id="Star2")]

    def get(self, dataset_type: str, selector: dict[str, Any]) -> Any:
        """Return the in-memory targets, stellar objects, or frames.

        Returns
        -------
        Any
            The in-memory list for `target_catalog`/`stellar_catalog`, an
            empty list for `raw_frames`, or `None` otherwise.
        """
        if dataset_type == "target_catalog":
            return self.targets
        elif dataset_type == "stellar_catalog":
            return self.stellar_objects
        elif dataset_type == "raw_frames":
            return []
        return None

    def put(self, obj: Any, dataset_type: str, selector: dict[str, Any]) -> None:
        """Store obj as the in-memory targets or stellar objects."""
        if dataset_type == "target_catalog":
            self.targets = obj
        elif dataset_type == "stellar_catalog":
            self.stellar_objects = obj

    def exists(self, dataset_type: str, selector: dict[str, Any]) -> bool:
        """Return `False` always; this mock never reports existence.

        Returns
        -------
        bool
            Always `False`.
        """
        return False

    def get_local_path(self, dataset_type: str, selector: dict[str, Any]) -> str:
        """Return a fixed placeholder path for any dataset type.

        Returns
        -------
        str
            The fixed placeholder path `"/mock/path"`.
        """
        return "/mock/path"


def test_disk_butler_instantiation():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies that CatalogAccess can be instantiated with default config."""
    catalog_access = CatalogAccess()
    assert catalog_access.config is not None


def test_mock_catalog_access_injection():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a mock CatalogAccess can be injected into the facade."""
    mock_catalog_access = MockCatalogAccess()
    astrometrics = Astrometrics(catalog_access=mock_catalog_access)

    # Verify hydration used the mock catalog_access
    targets = astrometrics.targets.list()
    assert len(targets) == 2
    assert targets[0].id == "M 31"
    assert targets[1].id == "Orion"

    # Verify saving routes back to mock catalog_access
    astrometrics.targets.save()
    assert len(mock_catalog_access.targets) == 2


def test_disk_butler_raw_frames_skips_already_tracked_frames(tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify CatalogAccess re-parses only new, untracked frames."""
    lights_dir = tmp_path / "lights" / "TestTarget"
    lights_dir.mkdir(parents=True)
    known_file = lights_dir / "known.fits"
    known_file.write_text("")
    new_file = lights_dir / "new.fits"
    new_file.write_text("")

    mock_config = MagicMock()
    mock_config.get_frames_path.return_value = str(tmp_path)
    catalog_access = CatalogAccess(config=mock_config)

    target = Target(id="TestTarget", frames=[FrameRecord(path=str(known_file))])

    mock_create_record = mocker.patch(
        "astrometricslib.data_access.frame_scanning.create_frame_record_from_fits",
        side_effect=lambda path, camera=None: FrameRecord(path=path),
    )

    result = catalog_access.get("raw_frames", {"target": target})

    assert mock_create_record.call_count == 1
    assert mock_create_record.call_args[0][0] == str(new_file)
    assert {f.path for f in result} == {str(known_file), str(new_file)}
    assert result is target.frames


def test_disk_butler_caches_stellar_catalog_reads(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify repeated stellar_catalog reads avoid redundant disk I/O."""
    mock_config = MagicMock()
    catalog_access = CatalogAccess(config=mock_config)

    mock_load = mocker.patch.object(
        catalog_access._generic,
        "get_all",
        return_value=[StellarObject(id="Star1")],
    )

    first = catalog_access.get("stellar_catalog", {})
    second = catalog_access.get("stellar_catalog", {})

    assert mock_load.call_count == 1
    assert first is second


def test_disk_butler_put_refreshes_stellar_catalog_cache(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify put() writes through to disk and refreshes the cache."""
    mock_config = MagicMock()
    catalog_access = CatalogAccess(config=mock_config)

    mock_load = mocker.patch.object(
        catalog_access._generic,
        "get_all",
        return_value=[StellarObject(id="Star1")],
    )
    mock_save = mocker.patch.object(catalog_access._generic, "put_all")

    updated = [StellarObject(id="Star2")]
    catalog_access.put(updated, "stellar_catalog", {})
    result = catalog_access.get("stellar_catalog", {})

    assert mock_save.call_count == 1
    assert result == updated
    assert mock_load.call_count == 0


def test_disk_butler_list_projected_reads_stellar_catalog_columns(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify list_projected reaches the real stellar_catalog registration.

    Regression coverage for the target_id-indexed browsing path added
    alongside local_database's lightweight summary loader: confirms the
    DatasetSpec registered in this module actually has target_id
    available to list_projected, and that it filters correctly.
    """
    from astrometricslib.utilities.config_loader import AppConfiguration

    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    (library_path / "frames").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({
        "Image Library": {"path": str(library_path), "frames_path": str(library_path / "frames")}
    })

    catalog_access = CatalogAccess(config=config)
    in_field = StellarObject(id="InField", name="InField")
    in_field.target_ids = ["M 13"]
    out_of_field = StellarObject(id="OutOfField", name="OutOfField")
    out_of_field.target_ids = ["M 81"]
    catalog_access.put([in_field, out_of_field], "stellar_catalog", {})

    rows = catalog_access.list_projected("stellar_catalog", ["id", "name"], where={"target_id": "M 13"})

    assert rows == [{"id": "InField", "name": "InField"}]


def test_disk_butler_stellar_catalog_has_a_target_id_index(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the real stellar_objects table gets its target_id index.

    A raw sqlite_master check rather than trusting list_projected alone
    to work -- correct query results don't prove the index exists, only
    that filtering is correct; a full scan would return the same rows.
    """
    import sqlite3

    from astrometricslib.utilities.config_loader import AppConfiguration

    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    (library_path / "frames").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({
        "Image Library": {"path": str(library_path), "frames_path": str(library_path / "frames")}
    })

    catalog_access = CatalogAccess(config=config)
    catalog_access.put([StellarObject(id="Polaris", name="Polaris")], "stellar_catalog", {})

    conn = sqlite3.connect(str(library_path / "astrometrics.db"))
    indexes = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    }
    conn.close()

    assert "idx_stellar_objects_target_id" in indexes
