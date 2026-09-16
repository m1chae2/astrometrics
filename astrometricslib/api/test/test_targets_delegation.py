"""Purpose: Delegation-contract tests for TargetCatalog.

Description: Most of TargetCatalog's methods are thin pass-throughs to
free functions in pipelines.shared. The underlying functions already
have their own thorough tests, so these tests only check the wiring:
that each method calls the right function with the right arguments and
returns its result unchanged. The handful of methods with real branching
logic of their own (get_header's ownership check, measure_frame_input_
quality's conditional save, get_calibration_frame_statistics' grouped/
flat split) get behavior tests instead.
"""

from unittest.mock import MagicMock

import pytest

from astrometricslib.api.processing import CalibrationCatalog
from astrometricslib.api.targets import TargetCatalog
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared import frame_grouping, image_conversions, target_records
from astrometricslib.pipelines.shared.quality import frame_statistics


def _make_catalog() -> TargetCatalog:
    config = MagicMock()
    catalog_access = MagicMock()
    catalog_access.get.return_value = []
    return TargetCatalog(config=config, catalog_access=catalog_access)


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


def test_add_frame_delegates_to_frame_grouping(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify all arguments are forwarded positionally and in order."""
    catalog = _make_catalog()
    target = Target(id="M13")
    mock = MagicMock(return_value="a frame record")
    monkeypatch.setattr(frame_grouping, "add_frame", mock)

    result = catalog.add_frame(target, "/frame.fits", role="DARK", filter_type="Ha", camera="ASI294")

    assert result == "a frame record"
    mock.assert_called_once_with(target, "/frame.fits", "DARK", "Ha", "ASI294")


def test_reindex_frames_delegates_with_keyword_arguments(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify all options are forwarded as keyword arguments."""
    catalog = _make_catalog()
    target = Target(id="M13")
    mock = MagicMock()
    monkeypatch.setattr(target_records, "reindex_frames", mock)
    other_catalog_access = MagicMock()

    catalog.reindex_frames(
        target, prune_missing=True, catalog_access=other_catalog_access, refresh_headers=True
    )

    mock.assert_called_once_with(
        target, prune_missing=True, catalog_access=other_catalog_access, refresh_headers=True
    )


def test_get_frame_delegates_to_image_conversions(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify all arguments are forwarded and the result passes through."""
    catalog = _make_catalog()
    target = Target(id="M13")
    mock = MagicMock(return_value="/frame.fits")
    monkeypatch.setattr(image_conversions, "get_frame", mock)

    result = catalog.get_frame(target, "800", "60", index=2)

    assert result == "/frame.fits"
    mock.assert_called_once_with(target, "800", "60", 2)


def test_delete_images_delegates_to_image_conversions(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the catalog itself is forwarded, so frames can be pruned."""
    catalog = _make_catalog()
    mock = MagicMock(return_value={"deleted": 1})
    monkeypatch.setattr(image_conversions, "delete_images", mock)

    result = catalog.delete_images(["/a.fits"], target_id="M13")

    assert result == {"deleted": 1}
    mock.assert_called_once_with(["/a.fits"], catalog, "M13")


def test_list_camera_names_delegates_with_the_full_target_list(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify it is handed this catalog's own target list, not a fresh one."""
    catalog = _make_catalog()
    targets = [Target(id="M13")]
    monkeypatch.setattr(catalog, "list", lambda: targets)
    mock = MagicMock(return_value={"ASI294": 3})
    monkeypatch.setattr(frame_statistics, "list_camera_names", mock)

    result = catalog.list_camera_names()

    assert result == {"ASI294": 3}
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

        with pytest.raises(ValueError, match="does not belong to target"):
            catalog.get_header("/unrelated.fits", target=target)


class TestMeasureFrameInputQuality:
    """Behavior tests for measure_frame_input_quality's conditional save."""

    def test_saves_when_frames_were_measured(self, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify a run that measured anything triggers a save."""
        catalog = _make_catalog()
        target = Target(id="M13")
        counts = {"measured": 3, "skipped": 0, "failed": 0}
        monkeypatch.setattr(frame_statistics, "measure_frame_input_quality", lambda *a, **k: counts)
        save_mock = MagicMock()
        monkeypatch.setattr(catalog, "save", save_mock)

        result = catalog.measure_frame_input_quality(target)

        assert result == counts
        save_mock.assert_called_once()

    def test_does_not_save_when_nothing_was_measured(self, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify a run that measured nothing skips the save entirely."""
        catalog = _make_catalog()
        target = Target(id="M13")
        counts = {"measured": 0, "skipped": 2, "failed": 0}
        monkeypatch.setattr(frame_statistics, "measure_frame_input_quality", lambda *a, **k: counts)
        save_mock = MagicMock()
        monkeypatch.setattr(catalog, "save", save_mock)

        catalog.measure_frame_input_quality(target)

        save_mock.assert_not_called()

    def test_save_false_skips_saving_even_when_frames_were_measured(self, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify the explicit opt-out is honored regardless of counts."""
        catalog = _make_catalog()
        target = Target(id="M13")
        monkeypatch.setattr(frame_statistics, "measure_frame_input_quality", lambda *a, **k: {"measured": 3})
        save_mock = MagicMock()
        monkeypatch.setattr(catalog, "save", save_mock)

        catalog.measure_frame_input_quality(target, save=False)

        save_mock.assert_not_called()


class TestGetCalibrationFrameStatistics:
    """Behavior tests for the grouped/flat branch."""

    def test_grouped_builds_a_calibration_catalog_from_this_catalog_s_config(self, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify the grouped path is handed a real CalibrationCatalog."""
        catalog = _make_catalog()
        target = Target(id="M13")
        mock = MagicMock(return_value={"grouped": True})
        monkeypatch.setattr(frame_statistics, "get_frame_stats_grouped", mock)

        result = catalog.get_calibration_frame_statistics(target, [], camera="ASI294")

        assert result == {"grouped": True}
        called_target, called_calibration, called_camera = mock.call_args[0]
        assert called_target is target
        assert isinstance(called_calibration, CalibrationCatalog)
        assert called_camera == "ASI294"

    def test_not_grouped_returns_flat_stats_without_a_calibration_catalog(self, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify the flat path never touches calibration data at all."""
        catalog = _make_catalog()
        target = Target(id="M13")
        mock = MagicMock(return_value={"flat": True})
        monkeypatch.setattr(frame_statistics, "get_frame_stats", mock)

        result = catalog.get_calibration_frame_statistics(target, [], grouped=False)

        assert result == {"flat": True}
        mock.assert_called_once_with(target)
