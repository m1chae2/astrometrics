"""Purpose: Tests for the copy kept of a catalog record before a delete.

Description: Deleting a target or stellar object must first write the record
to a ``deleted_records`` file, so a mistaken delete can be undone by hand.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from backend.services.data.deletion_archive import ARCHIVE_FOLDER_NAME, archive_record_before_delete
from backend.services.data.target_service import TargetService


def test_the_record_is_written_as_json_before_a_delete(tmp_path: Path) -> None:
    """The archive file holds the kind, id and fields of the record."""
    path = archive_record_before_delete(tmp_path, "target", "M 52 - Bubble Nebula", {"ra": "1h", "n": 3})

    assert path is not None
    assert path.parent == tmp_path / ARCHIVE_FOLDER_NAME
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == {"kind": "target", "id": "M 52 - Bubble Nebula", "record": {"ra": "1h", "n": 3}}


def test_values_json_cannot_store_are_saved_as_text(tmp_path: Path) -> None:
    """A record with an odd value still archives instead of failing."""
    path = archive_record_before_delete(tmp_path, "target", "x", {"when": object})

    assert path is not None
    assert "object" in path.read_text(encoding="utf-8")


def test_an_unwritable_folder_does_not_stop_the_delete(tmp_path: Path) -> None:
    """A failure to archive returns None rather than raising."""
    blocker = tmp_path / "file"
    blocker.write_text("x")

    assert archive_record_before_delete(blocker, "target", "x", {}) is None


def test_deleting_a_target_archives_it_first(tmp_path: Path) -> None:
    """`TargetService.delete_target` leaves an archive file behind."""
    target = SimpleNamespace(id="T 1", serialize=lambda: {"id": "T 1"})
    targets = SimpleNamespace(get=lambda target_id: target, delete=lambda target_id: True)
    service = TargetService.__new__(TargetService)
    service.config = SimpleNamespace(get_library_path=lambda: tmp_path)
    service.astrometrics = SimpleNamespace(targets=targets)

    assert service.delete_target("T 1") is True

    archived = list((tmp_path / ARCHIVE_FOLDER_NAME).glob("*_target_T_1.json"))
    assert len(archived) == 1
