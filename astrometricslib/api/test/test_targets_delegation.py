"""Purpose: Delegation-contract tests for TargetCatalog.

Description: Most of TargetCatalog's methods are thin pass-throughs to
free functions in pipelines.shared. The underlying functions already
have their own thorough tests, so these tests only check the wiring:
that each method calls the right function with the right arguments and
returns its result unchanged. The handful of methods with real branching
logic of their own (get_header's ownership check, reindex_frames' three
forms) get behavior tests instead.
"""

from unittest.mock import MagicMock

import pytest

from astrometricslib.api.targets import TargetCatalog
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared import frame_grouping, image_conversions, target_records
from astrometricslib.pipelines.shared.quality import frame_statistics


def _make_catalog() -> TargetCatalog:
    """Build a catalog over a mock config and an empty mock storage.

    Returns
    -------
    catalog : `TargetCatalog`
        A catalog holding no targets.
    """
    config = MagicMock()
    catalog_access = MagicMock()
    catalog_access.get.return_value = []
    return TargetCatalog(config, catalog_access)


def test_list_delegates_to_target_records(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the return value passes through unchanged."""
    catalog = _make_catalog()
    mock = MagicMock(return_value=["a target"])
    monkeypatch.setattr(target_records, "list_targets", mock)

    assert catalog.list() == ["a target"]
    mock.assert_called_once_with(catalog)


def test_get_delegates_to_target_records(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the target id is forwarded and the result passes through."""
    catalog = _make_catalog()
    target = Target(id="M13")
    mock = MagicMock(return_value=target)
    monkeypatch.setattr(target_records, "get_target", mock)

    assert catalog.get("M13") is target
    mock.assert_called_once_with(catalog, "M13")


def test_create_delegates_to_target_records(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the target id is forwarded and the result passes through."""
    catalog = _make_catalog()
    target = Target(id="M13")
    mock = MagicMock(return_value=target)
    monkeypatch.setattr(target_records, "create_target", mock)

    assert catalog.create("M13") is target
    mock.assert_called_once_with(catalog, "M13")


def test_delete_delegates_to_target_records(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the target id is forwarded and the result passes through."""
    catalog = _make_catalog()
    mock = MagicMock(return_value=True)
    monkeypatch.setattr(target_records, "delete_target", mock)

    assert catalog.delete("M13") is True
    mock.assert_called_once_with(catalog, "M13")


def test_save_delegates_to_target_records(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify save calls through with the catalog itself."""
    catalog = _make_catalog()
    mock = MagicMock()
    monkeypatch.setattr(target_records, "save_targets", mock)

    catalog.save()

    mock.assert_called_once_with(catalog)


def test_add_appends_a_new_target_and_saves(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a new target is appended, tracked, and saved.

    Unlike the other CRUD methods, add() has real logic of its own
    rather than delegating outright, so this exercises that logic
    directly instead of just checking a forwarded call.
    """
    catalog = _make_catalog()
    save_mock = MagicMock()
    monkeypatch.setattr(catalog, "save", save_mock)
    target = Target(id="M13")

    catalog.add(target)

    assert catalog._targets == [target]
    assert "M13" in catalog._touched_target_ids
    save_mock.assert_called_once()


def test_add_does_not_duplicate_an_existing_target(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify adding a target already in the catalog does not duplicate it."""
    catalog = _make_catalog()
    monkeypatch.setattr(catalog, "save", MagicMock())
    existing = Target(id="M13")
    catalog._targets.append(existing)

    catalog.add(Target(id="M13"))

    assert catalog._targets == [existing]


def test_reindex_frames_with_paths_adds_each_file_and_saves(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify each path reaches add_frame with the overrides, then a save."""
    catalog = _make_catalog()
    target = Target(id="M13")
    mock = MagicMock(side_effect=lambda t, path, *rest: FrameRecord(path=path))
    monkeypatch.setattr(frame_grouping, "add_frame", mock)
    save_mock = MagicMock()
    monkeypatch.setattr(catalog, "save", save_mock)

    report = catalog.reindex_frames(
        target, paths=["/frame.fits"], role="DARK", filter_type="Ha", camera_id="ASI294"
    )

    mock.assert_called_once_with(target, "/frame.fits", "DARK", "Ha", "ASI294")
    assert report.added_paths == ["/frame.fits"]
    assert report.targets[0].target_id == "M13"
    save_mock.assert_called_once()


@pytest.mark.parametrize("picture", ["/m13_final.PNG", "/m13.jpg", "/m13.tiff"])
def test_reindex_frames_makes_a_finished_picture_the_processed_image(
    monkeypatch: pytest.MonkeyPatch, picture: str
) -> None:
    """Verify a .jpg, .png or .tiff becomes the processed image, not a frame."""
    catalog = _make_catalog()
    target = Target(id="M13")
    add_frame = MagicMock(side_effect=lambda t, path, *rest: FrameRecord(path=path))
    monkeypatch.setattr(frame_grouping, "add_frame", add_frame)
    monkeypatch.setattr(catalog, "save", MagicMock())

    report = catalog.reindex_frames(target, paths=["/frame.fits", picture])

    add_frame.assert_called_once_with(target, "/frame.fits", "LIGHT", None, None)
    assert target.stacking.processed_image == picture
    assert report.processed_image == picture
    assert report.added_paths == ["/frame.fits"]


def test_is_processed_image_ignores_case_and_rejects_fits() -> None:
    """Verify the file-ending rule for finished pictures."""
    assert target_records.is_processed_image("/a/b/M13.JPEG")
    assert target_records.is_processed_image("final.tif")
    assert not target_records.is_processed_image("/a/b/M13.fits")
    assert not target_records.is_processed_image("/a/b/M13")


def test_reindex_frames_of_one_target_delegates_with_keyword_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the scan options are forwarded as keyword arguments."""
    catalog = _make_catalog()
    monkeypatch.setattr(catalog, "save", MagicMock())
    target = Target(id="M13")
    mock = MagicMock()
    monkeypatch.setattr(target_records, "reindex_frames", mock)

    catalog.reindex_frames(target, prune_missing=True, refresh_headers=True)

    mock.assert_called_once_with(
        target, prune_missing=True, catalog_access=catalog.catalog_access, refresh_headers=True
    )


def test_reindex_frames_refuses_arguments_its_form_does_not_use() -> None:
    """Verify a header override without paths, or bare paths, is refused."""
    catalog = _make_catalog()

    with pytest.raises(InvalidArgumentError, match="does not use"):
        catalog.reindex_frames(Target(id="M13"), camera_id="ASI294")
    with pytest.raises(InvalidArgumentError, match="needs a target"):
        catalog.reindex_frames(paths=["/frame.fits"])


def test_reindex_frames_refuses_a_name_that_matches_no_target() -> None:
    """Verify a target name that names nothing raises NotFoundError."""
    catalog = _make_catalog()

    with pytest.raises(NotFoundError):
        catalog.reindex_frames("No Such Target")


def test_get_frame_delegates_to_image_conversions(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify all arguments are forwarded and the result passes through."""
    catalog = _make_catalog()
    target = Target(id="M13")
    mock = MagicMock(return_value="/frame.fits")
    monkeypatch.setattr(image_conversions, "get_frame", mock)

    result = catalog.get_frame(target, "800", "60", index=2)

    assert result == "/frame.fits"
    mock.assert_called_once_with(target, "800", "60", 2)


def test_delete_images_delegates_to_image_conversions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the catalog itself is forwarded, so frames can be pruned."""
    catalog = _make_catalog()
    mock = MagicMock(return_value={"deleted": 1})
    monkeypatch.setattr(image_conversions, "delete_images", mock)

    result = catalog.delete_images(["/a.fits"], target=Target(id="M13"))

    assert result == {"deleted": 1}
    mock.assert_called_once_with(["/a.fits"], catalog, "M13")


def test_camera_query_delegates_with_the_full_target_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify `query(detail="cameras")` gets this catalog's own targets."""
    catalog = _make_catalog()
    targets = [Target(id="M13")]
    monkeypatch.setattr(catalog, "list", lambda: targets)
    mock = MagicMock(return_value={"ASI294": 3})
    monkeypatch.setattr(frame_statistics, "list_camera_names", mock)

    result = catalog.query(detail="cameras")

    assert result.cameras == {"ASI294": 3}
    mock.assert_called_once_with(targets)


class TestGetHeader:
    """Behavior tests for get_header's target-ownership check."""

    def test_reads_the_header_when_no_target_is_given(self, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify a bare path read skips the ownership check entirely."""
        catalog = _make_catalog()
        mock = MagicMock(return_value=[{"KEY": "VALUE"}])
        monkeypatch.setattr(image_conversions, "get_fits_header", mock)

        result = catalog.get_header("/fake.fits")

        assert result == [{"KEY": "VALUE"}]
        mock.assert_called_once_with("/fake.fits")

    def test_reads_the_header_when_the_path_belongs_to_the_target(self, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify a path matching the target's stacked_image is accepted."""
        catalog = _make_catalog()
        target = Target(id="M13", stacked_image="/stack.fits")
        mock = MagicMock(return_value=[])
        monkeypatch.setattr(image_conversions, "get_fits_header", mock)

        catalog.get_header("/stack.fits", target=target)

        mock.assert_called_once_with("/stack.fits")

    def test_raises_when_the_path_does_not_belong_to_the_target(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify an unrelated path is rejected before ever reading it."""
        catalog = _make_catalog()
        target = Target(id="M13")

        with pytest.raises(InvalidArgumentError, match="does not belong to target"):
            catalog.get_header("/unrelated.fits", target=target)
