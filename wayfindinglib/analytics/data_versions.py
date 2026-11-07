"""Purpose: Work out the cheap data versions that tell a cache to rebuild.

Description: Both the night review (control) and the target report
(planning) keep a built result until a cheap sign says an input changed.
The signs they share live here, in the analytics layer, so planning does
not have to import control: the active equipment, the configured site, the
configuration sections a result reads, and the versions of a night review
and of a target report.

The wayfinding records use the same write counter as the science library:
each write adds one to a counter in the ``data_versions`` table of
``wayfinding.db`` inside the write's own transaction (see
`astrometricslib.drivers.bump_data_version`). An update in place, a delete,
and a row number used again all change the counter, which row counts could
miss.

A night review (`review_data_version`) watches the counters of the guiding
samples, the guiding runs, the Ekos sessions and the site, plus the library
counters, the equipment and the configuration sections.

A target report (`report_data_version`) reads a narrower set: the Ekos
session records, the site and the guiding runs. Guiding samples are written
every few seconds during a night, and a report reads only the samples inside
the exposures of frames already in the library. So the report does not watch
the live counter. It watches how many samples exist, rounded down to a
coarse step, and the counter of bulk replacements (a guide log sync). A few
new live samples do not change the version. A guide log sync does, and it
also writes a guiding run, which is counted exactly.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from astrometricslib.drivers import get_library_data_version
from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog
from wayfindinglib.data_access.site_profile_reader import configured_observer_location
from wayfindinglib.drivers.control_record_store import BULK_GUIDING_SAMPLES_KIND, RECORD_VERSION_TABLES

if TYPE_CHECKING:
    from collections.abc import Hashable

logger = logging.getLogger(__name__)

REPORT_EXACT_TABLES = (
    "ekos_session_contexts",
    "site_profiles",
    "guiding_runs",
)
"""The ``wayfinding.db`` tables a target report reads; every write counts.

The report reads the Ekos session records, the site and the guiding runs.
It does not read the observation sessions, so they are not listed.
"""

REPORT_COARSE_TABLE = "guiding_samples"
"""The table written every few seconds during a night, counted coarsely."""

REPORT_COARSE_STEP = 2000
"""Rows of `REPORT_COARSE_TABLE` that must be added to change the version.

Design estimate WAY-D105 (see ``validation/wayfinding.md``). At the
measured guiding cadence of about 6.7 seconds a sample (the performance
envelope's ``guide_cadence_seconds``), 2000 samples are about 3.7 hours of
live guiding. A live night therefore changes the version about once. A
guide log sync is seen by its own counter and by the guiding run it writes.
"""

CONFIG_SECTION_PREFIX = "Observatory"
"""Configuration sections that start with this are part of the version.

They hold the camera profiles (gain, read noise), the equipment items and
the site, which a depth forecast reads."""


def config_signs(config: Any) -> str:
    """Make a stable fingerprint of the configuration a result reads.

    A depth goal or target report reads the camera's gain and read noise
    from the ``[Observatory.Camera.*]`` sections, and other
    ``[Observatory.*]`` items. Changing one of them changes the answer, so
    the fingerprint is part of the version.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.

    Returns
    -------
    fingerprint : `str`
        A hash of the text of every section whose name starts with
        `CONFIG_SECTION_PREFIX`. Empty when the sections cannot be read.
    """
    try:
        source = getattr(config, "app_config", config)
        sections = {}
        for name in sorted(source.sections()):
            if str(name).startswith(CONFIG_SECTION_PREFIX):
                table = source[name]
                unwrap = getattr(table, "unwrap", None)
                sections[str(name)] = unwrap() if callable(unwrap) else dict(table)
        text = json.dumps(sections, sort_keys=True, default=str)
    except Exception:
        # A setting that cannot be read leaves the fingerprint empty.
        logger.warning("Could not read the configuration sections for the data version.", exc_info=True)
        return ""
    return hashlib.sha256(text.encode()).hexdigest()


def equipment_signs(config: Any) -> tuple[str, ...]:
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


def report_data_version(config: Any, butler: Any) -> Hashable | None:
    """Work out the version of every input of a target report.

    A target report reads the library, the equipment, site and configuration,
    and a few wayfinding tables (see the module description). Live guiding
    samples are counted in coarse steps so live guiding does not rebuild the
    report.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    butler : `DiskButler`
        The record store. It gives the wayfinding records.

    Returns
    -------
    version : `~collections.abc.Hashable` or `None`
        A value that is equal for two calls only when no input has visibly
        changed between them. `None` when it cannot be worked out, in which
        case the caller must not keep a result.
    """
    try:
        records = butler.control_records
        wayfinding = records.data_versions((*REPORT_EXACT_TABLES, BULK_GUIDING_SAMPLES_KIND))
        sample_count = records.guiding_sample_count()
        library = get_library_data_version(config)
        if wayfinding is None or sample_count is None or library is None:
            return None
        coarse = sample_count // REPORT_COARSE_STEP
        return (
            tuple(sorted(wayfinding.items())),
            coarse,
            library,
            equipment_signs(config),
            config_signs(config),
        )
    except Exception:
        # A version that cannot be worked out only means no result is kept.
        logger.exception("Could not work out the data version of the target report.")
        return None


def review_data_version(context: Any) -> Hashable | None:
    """Work out the version of every input of a night review.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the configuration (``config``) and the record store
        (``records``).

    Returns
    -------
    version : `~collections.abc.Hashable` or `None`
        A value that is equal for two calls only when no input has visibly
        changed between them. `None` when it cannot be worked out, in which
        case the caller must not keep a result.
    """
    try:
        wayfinding = context.records.data_versions(RECORD_VERSION_TABLES)
        if wayfinding is None:
            return None
        library = get_library_data_version(context.config)
        if library is None:
            return None
        return (
            tuple(sorted(wayfinding.items())),
            library,
            equipment_signs(context.config),
            config_signs(context.config),
        )
    except Exception:
        # A version that cannot be worked out only means no result is kept.
        logger.exception("Could not work out the data version of the night review.")
        return None
