"""Tests for refreshing the target catalog only when storage changed.

`TargetCatalog.list` and `TargetCatalog.get(refresh=True)` read what other
programs saved. They skip the read when the library's write counter shows
nothing was written, and read only one row when the caller wants one target.
These tests check that the answers are the same as a full read would give,
that unsaved edits in memory are kept, that the reads are really skipped, and
that several threads can refresh together.
"""

import threading
from pathlib import Path
from typing import Any

import pytest

from astrometricslib._internal.drivers.catalog_access import CatalogAccess
from astrometricslib.api.targets import TargetCatalog
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.target import FrameRecord, Target


@pytest.fixture
def config(tmp_path: Path) -> AppConfiguration:
    """Give a configuration whose library is a temporary folder.

    Returns
    -------
    config : `AppConfiguration`
        The configuration.
    """
    library = tmp_path / "library"
    library.mkdir()
    (tmp_path / "frames").mkdir()
    configuration = AppConfiguration()
    configuration.update_config({
        "Image Library": {"path": str(library), "frames_path": str(tmp_path / "frames")}
    })
    return configuration


class _CountingAccess(CatalogAccess):
    """A catalog access that counts whole-catalog reads and row reads."""

    def __init__(self, config: AppConfiguration) -> None:
        """Start with no reads counted."""
        super().__init__(config=config)
        self.full_reads = 0
        self.row_reads = 0

    def get(self, dataset_type: str, selector: dict) -> Any:
        """Count a read of the whole catalog, then read it.

        Returns
        -------
        dataset : `Any`
            What `CatalogAccess.get` gives.
        """
        if dataset_type == "target_catalog":
            self.full_reads += 1
        return super().get(dataset_type, selector)

    def get_by_ids(self, dataset_type: str, ids: list[str]) -> list:
        """Count a read of some rows, then read them.

        Returns
        -------
        rows : `list`
            What `CatalogAccess.get_by_ids` gives.
        """
        self.row_reads += 1
        return super().get_by_ids(dataset_type, ids)


def _library(config: AppConfiguration) -> tuple[TargetCatalog, _CountingAccess, CatalogAccess]:
    """Build a library of two stored targets and a catalog over it.

    Returns
    -------
    catalog, access, other_program : `tuple`
        The catalog under test, its counting storage, and a second storage on
        the same library that stands for another program.
    """
    other_program = CatalogAccess(config=config)
    other_program.put(
        [
            Target(id="M 1", frames=[FrameRecord(path="/a/1.fits")]),
            Target(id="M 2", frames=[FrameRecord(path="/a/2.fits")]),
        ],
        "target_catalog",
        {},
    )
    access = _CountingAccess(config)
    return TargetCatalog(config, access), access, other_program


def test_a_second_list_with_nothing_written_does_not_read(config: AppConfiguration) -> None:
    """The first list after start-up and every later one skip the read."""
    catalog, access, _other = _library(config)
    reads_at_start = access.full_reads

    first = catalog.list()
    second = catalog.list()

    assert access.full_reads == reads_at_start
    assert [target.id for target in first] == ["M 1", "M 2"]
    assert first[0] is second[0]


def test_a_write_by_another_program_is_seen_by_list(config: AppConfiguration) -> None:
    """A changed and a new target appear after another program saves."""
    catalog, access, other = _library(config)
    held = catalog.get("M 1")
    other.merge_and_record(
        "target_catalog",
        [Target(id="M 1", common_name="Changed"), Target(id="M 3")],
        lambda _old, new: new,
    )
    reads_before = access.full_reads

    listed = catalog.list()

    assert access.full_reads == reads_before + 1
    assert sorted(target.id for target in listed) == ["M 1", "M 2", "M 3"]
    assert next(target for target in listed if target.id == "M 1") is held
    assert held.common_name == "Changed"
    # The next call has nothing new to read.
    catalog.list()
    assert access.full_reads == reads_before + 1


def test_unsaved_edits_survive_a_refresh(config: AppConfiguration) -> None:
    """An edited target keeps its edit; an untouched one is refreshed."""
    catalog, _access, other = _library(config)
    edited = catalog.get("M 1")
    edited.common_name = "My edit"
    untouched = catalog.get("M 2")
    other.merge_and_record(
        "target_catalog",
        [Target(id="M 1", common_name="Elsewhere"), Target(id="M 2", common_name="Elsewhere")],
        lambda _old, new: new,
    )

    catalog.list()
    assert edited.common_name == "My edit"
    assert untouched.common_name == "Elsewhere"

    # The same holds when one target is refreshed on its own.
    other.merge_and_record("target_catalog", [Target(id="M 1", common_name="Again")], lambda _o, new: new)
    assert catalog.get("M 1", refresh=True).common_name == "My edit"


def test_refreshing_one_target_reads_one_row(config: AppConfiguration) -> None:
    """`get(refresh=True)` reads one row after a write, else none."""
    catalog, access, other = _library(config)
    held = catalog.get("M 2")
    reads_at_start = access.full_reads

    assert catalog.get("M 2", refresh=True) is held
    assert (access.full_reads, access.row_reads) == (reads_at_start, 0)

    other.merge_and_record(
        "target_catalog", [Target(id="M 2", common_name="New name", frames=[])], lambda _old, new: new
    )
    refreshed = catalog.get("m2", refresh=True)

    assert refreshed is held
    assert held.common_name == "New name"
    assert held.frames == []
    assert access.full_reads == reads_at_start
    assert access.row_reads == 1
    catalog.get("M 2", refresh=True)
    assert access.row_reads == 1


def test_refreshing_a_target_the_catalog_does_not_hold_reads_all(config: AppConfiguration) -> None:
    """A target another program added is found by a full read."""
    catalog, access, other = _library(config)
    other.merge_and_record("target_catalog", [Target(id="M 9")], lambda _old, new: new)
    reads_before = access.full_reads

    found = catalog.get("M 9", refresh=True)

    assert found is not None
    assert found.id == "M 9"
    assert access.full_reads == reads_before + 1


def test_a_target_deleted_elsewhere_is_dropped_by_refresh(config: AppConfiguration) -> None:
    """A held target whose row is gone is dropped by a full read."""
    catalog, _access, other = _library(config)
    catalog.get("M 2")
    other.delete_by_ids("target_catalog", ["M 2"])

    assert catalog.get("M 2", refresh=True) is None
    assert [target.id for target in catalog.list()] == ["M 1"]


def test_refreshed_targets_equal_a_full_read(config: AppConfiguration) -> None:
    """After a mix of writes, the targets equal what storage holds."""
    catalog, _access, other = _library(config)
    other.merge_and_record(
        "target_catalog",
        [
            Target(
                id="M 1",
                common_name="One",
                frames=[FrameRecord(path="/a/1.fits"), FrameRecord(path="/a/x.fits")],
            ),
            Target(id="M 4"),
        ],
        lambda _old, new: new,
    )
    catalog.get("M 1", refresh=True)
    listed = catalog.list()

    stored = {target.id: target for target in other.get("target_catalog", {})}
    assert {target.id for target in listed} == set(stored)
    for target in listed:
        assert target.model_dump_json() == stored[target.id].model_dump_json()


def test_a_save_by_this_catalog_is_seen_by_the_next_list(config: AppConfiguration) -> None:
    """The catalog's own save is a write; the next list reads again."""
    catalog, access, _other = _library(config)
    target = catalog.get("M 1")
    target.common_name = "Saved"
    catalog.save()
    reads_before = access.full_reads

    listed = catalog.list()

    assert access.full_reads == reads_before + 1
    assert next(item for item in listed if item.id == "M 1").common_name == "Saved"


def test_several_threads_can_refresh_together(config: AppConfiguration) -> None:
    """Threads that refresh together raise nothing and keep one object each."""
    catalog, _access, other = _library(config)
    failures: list[BaseException] = []

    def reader() -> None:
        """Refresh the catalog many times, recording any error."""
        try:
            for _ in range(20):
                catalog.list()
                catalog.get("M 1", refresh=True)
                catalog.get("M 2", refresh=True)
        except BaseException as error:  # ruff: ignore[blind-except]
            failures.append(error)

    def writer() -> None:
        """Change a target in storage many times, recording any error."""
        try:
            for index in range(10):
                other.merge_and_record(
                    "target_catalog", [Target(id="M 2", common_name=f"v{index}")], lambda _old, new: new
                )
        except BaseException as error:  # ruff: ignore[blind-except]
            failures.append(error)

    threads = [threading.Thread(target=reader) for _ in range(6)] + [threading.Thread(target=writer)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert failures == []
    ids = [target.id for target in catalog.list()]
    assert sorted(ids) == ["M 1", "M 2"]
    assert catalog.get("M 2", refresh=True).common_name == "v9"
