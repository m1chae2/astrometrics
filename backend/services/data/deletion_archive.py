"""Purpose: Keep a copy of every catalog record before it is deleted.

Description: Deleting a target or stellar object from the catalog removes
its database row for good. Before that happens, this module writes the
record to a small JSON file in a ``deleted_records`` folder beside the
database, so a deletion made by mistake can be undone by hand. Only the
catalog record is kept here; image files are never touched by a delete.
"""

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ARCHIVE_FOLDER_NAME = "deleted_records"


def archive_record_before_delete(
    library_path: Path, record_kind: str, record_id: str, record_data: dict[str, Any]
) -> Path | None:
    """Save a record to the ``deleted_records`` folder.

    Parameters
    ----------
    library_path : `pathlib.Path`
        The library folder that holds the catalog database.
    record_kind : `str`
        What is being deleted, such as ``"target"``.
    record_id : `str`
        The record's id.
    record_data : `dict`
        The record's fields. Values that JSON cannot store are saved as text.

    Returns
    -------
    archive_path : `pathlib.Path` or `None`
        The file that was written, or `None` if it could not be written. A
        failure is logged and does not stop the delete, because the person
        asked for the delete.
    """
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", record_id).strip("_") or "record"
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    archive_path = Path(library_path) / ARCHIVE_FOLDER_NAME / f"{timestamp}_{record_kind}_{safe_id}.json"
    try:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_text(
            json.dumps({"kind": record_kind, "id": record_id, "record": record_data}, default=str, indent=1),
            encoding="utf-8",
        )
    except OSError:
        logger.exception("Could not archive %s %r before deleting it", record_kind, record_id)
        return None
    logger.info("Archived %s %r to %s before deleting it.", record_kind, record_id, archive_path)
    return archive_path
