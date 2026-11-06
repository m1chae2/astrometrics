"""Purpose: Work out the cheap data version that tells the cache to rebuild.

Description: A review of a night reads the science library (frames and their
quality measurements), the recorded guiding samples, guiding runs, Ekos
sessions and the site, and it judges them for the equipment in use. The
version is a small value made from the cheap signs that any of these changed:
the size and write time of the science library's database file and the
size of its log, the row counts and largest row numbers of the wayfinding
tables, and the active equipment and configured site. Reading it takes a
few small queries, far less than the seconds a review takes.

The version is coarse on purpose. Any write to the science database changes
it, even one that does not touch the night under review, and then the review
is built again. That is the cost today on every call, so a wrong rebuild is
only a missed saving. A change the version cannot see (a write made by
another program that leaves the file size and write time alone, or a
setting outside those named above) is the risk, so `ObservationReview`
offers `invalidate_night` too.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog
from wayfindinglib.data_access.site_profile_reader import configured_observer_location

if TYPE_CHECKING:
    from collections.abc import Hashable

    from wayfindinglib.api.control.context import ControlContext

logger = logging.getLogger(__name__)

SCIENCE_DATABASE_FILE = "astrometrics.db"
"""The science library's database, with the frames and quality rows."""

SCIENCE_DATABASE_LOG_FILE = "astrometrics.db-wal"
"""The database's write-ahead log, where recent writes wait before they reach
the database file. Only its size is used: merely opening the database (even to
read) creates, touches and removes the log, so its time and existence change on
every read."""


def _file_stamp(path: str, with_time: bool) -> tuple[int, ...] | None:
    """Give the size (and, if asked, the write time) of a file.

    Returns
    -------
    stamp : `tuple` [`int`, ...] or `None`
        The size in bytes, and with `with_time` the nanoseconds since 1970 at
        the last write. `None` if the file is not there.
    """
    try:
        status = os.stat(path)
    except OSError:
        return None
    return (status.st_size, status.st_mtime_ns) if with_time else (status.st_size,)


def _equipment_signs(config: Any) -> tuple[str, ...]:
    """Describe the active equipment and configured site as text.

    Returns
    -------
    signs : `tuple` [`str`, ...]
        The active telescope, camera, guide scope and guide camera, and the
        configured observer location. Each is empty when there is none.
    """
    catalog = get_equipment_catalog(config)
    items = (
        catalog.active_telescope(),
        catalog.active_camera(),
        catalog.active_guide_scope(),
        catalog.active_guide_camera(),
    )
    return (*(repr(item) for item in items), repr(configured_observer_location(config)))


def review_data_version(context: ControlContext) -> Hashable | None:
    """Work out the version of every input of a night review.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the configuration and the record store.

    Returns
    -------
    version : `~collections.abc.Hashable` or `None`
        A value that is equal for two calls only when no input has visibly
        changed between them. `None` when it cannot be worked out, in which
        case the caller must not keep a result.
    """
    try:
        wayfinding = context.records.data_versions()
        if wayfinding is None:
            return None
        library_folder = str(context.config.get_library_path())
        database = _file_stamp(os.path.join(library_folder, SCIENCE_DATABASE_FILE), with_time=True)
        if database is None:
            return None
        # A reader makes the log appear and vanish, so a missing log is empty.
        log = _file_stamp(os.path.join(library_folder, SCIENCE_DATABASE_LOG_FILE), with_time=False) or (0,)
        return tuple(sorted(wayfinding.items())), database, log, _equipment_signs(context.config)
    except Exception:
        # A version that cannot be worked out only means no result is kept.
        logger.exception("Could not work out the data version of the night review.")
        return None
