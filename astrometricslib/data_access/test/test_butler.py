"""Purpose: Unit tests for the Butler data access layer and its integration.

Description: Verifies that DiskButler resolves paths and catalogs
correctly, and that a mock butler can be injected to isolate scientific
core logic. # REQ: BKD-5
"""

from typing import Any
from unittest.mock import MagicMock

from astrometricslib import AbstractButler, Astrometrics, DiskButler, StellarObject, Target
from astrometricslib.models.target import FrameRecord


class MockButler(AbstractButler):
    """A mock implementation of the Butler for testing in-memory data flows."""

    def __init__(self):  # ruff: ignore[missing-return-type-special-method]
        self.targets = [Target(id="M 31"), Target(id="Orion")]
        self.stellar_objects = [StellarObject(id="Star1"), StellarObject(id="Star2")]

    def get(self, dataset_type: str, coordinate: dict[str, Any]) -> Any:
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

    def put(self, obj: Any, dataset_type: str, coordinate: dict[str, Any]) -> None:
        """Store obj as the in-memory targets or stellar objects."""
        if dataset_type == "target_catalog":
            self.targets = obj
        elif dataset_type == "stellar_catalog":
            self.stellar_objects = obj

    def exists(self, dataset_type: str, coordinate: dict[str, Any]) -> bool:
        """Return `False` always; this mock never reports existence.

        Returns
        -------
        bool
            Always `False`.
        """
        return False

    def query_coordinates(self, dataset_type: str, query: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the query unchanged, wrapped in a single-item list.

        Returns
        -------
        list[dict[str, Any]]
            A single-item list containing `query` unchanged.
        """
        return [query]

    def get_local_path(self, dataset_type: str, coordinate: dict[str, Any]) -> str:
        """Return a fixed placeholder path for any dataset type.

        Returns
        -------
        str
            The fixed placeholder path `"/mock/path"`.
        """
        return "/mock/path"


def test_disk_butler_instantiation():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies that DiskButler can be instantiated with default config."""
    butler = DiskButler()
    assert butler.config is not None


def test_mock_butler_injection():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a mock Butler can be injected into the high-level interface."""
    mock_butler = MockButler()
    astrometrics = Astrometrics(butler=mock_butler)

    # Verify hydration used the mock butler
    targets = astrometrics.targets.list()
    assert len(targets) == 2
    assert targets[0].id == "M 31"
    assert targets[1].id == "Orion"

    # Verify saving routes back to mock butler
    astrometrics.targets.save()
    assert len(mock_butler.targets) == 2


def test_disk_butler_raw_frames_skips_already_tracked_frames(tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify DiskButler re-parses only new, untracked frames."""
    lights_dir = tmp_path / "lights" / "TestTarget"
    lights_dir.mkdir(parents=True)
    known_file = lights_dir / "known.fits"
    known_file.write_text("")
    new_file = lights_dir / "new.fits"
    new_file.write_text("")

    mock_config = MagicMock()
    mock_config.get_frames_path.return_value = str(tmp_path)
    butler = DiskButler(config=mock_config)

    target = Target(id="TestTarget", frames=[FrameRecord(path=str(known_file))])

    mock_create_record = mocker.patch(
        "astrometricslib.data_access.frame_scanning.create_frame_record_from_fits",
        side_effect=lambda path, camera=None: FrameRecord(path=path),
    )

    result = butler.get("raw_frames", {"target": target})

    assert mock_create_record.call_count == 1
    assert mock_create_record.call_args[0][0] == str(new_file)
    assert {f.path for f in result} == {str(known_file), str(new_file)}
    assert result is target.frames


def test_disk_butler_caches_stellar_catalog_reads(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify repeated stellar_catalog reads avoid redundant disk I/O."""
    mock_config = MagicMock()
    butler = DiskButler(config=mock_config)

    mock_load = mocker.patch.object(
        butler._generic,
        "get_all",
        return_value=[StellarObject(id="Star1")],
    )

    first = butler.get("stellar_catalog", {})
    second = butler.get("stellar_catalog", {})

    assert mock_load.call_count == 1
    assert first is second


def test_disk_butler_put_refreshes_stellar_catalog_cache(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify put() writes through to disk and refreshes the cache."""
    mock_config = MagicMock()
    butler = DiskButler(config=mock_config)

    mock_load = mocker.patch.object(
        butler._generic,
        "get_all",
        return_value=[StellarObject(id="Star1")],
    )
    mock_save = mocker.patch.object(butler._generic, "put_all")

    updated = [StellarObject(id="Star2")]
    butler.put(updated, "stellar_catalog", {})
    result = butler.get("stellar_catalog", {})

    assert mock_save.call_count == 1
    assert result == updated
    assert mock_load.call_count == 0
