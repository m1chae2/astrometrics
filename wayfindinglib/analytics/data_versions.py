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

A depth goal (`depth_goal_data_version`) reads the same wayfinding records,
equipment and configuration as a report. For the library it watches only the
one target's own data and the data of the cameras that target used, not the
counters that any target's new frame changes. A frame of M 13 taken with
another camera does not make the depth goal of M 31 build again; a frame of
M 13 taken with the same camera does, because the forecast compares the
target with the camera's other frames (the efficiency baseline, the sky
model and the noise model pooled over targets). Only once the night is over:
the camera-wide part counts the frames of finished nights only, because it
describes what the camera usually does and a night in progress is not
representative. A new frame tonight changes the target's own part and leaves
the camera part as it is; the camera part changes once, when the night
rolls over.

The list of nights (`nights_data_versions`) is kept in two parts, so a new
frame tonight does not rebuild the entries of finished nights. The version of
the finished nights is the digest of their own frames and sessions and the
stored sky background rows of their frames. The version
of the night in progress holds tonight's frames, sessions and background
rows. Both include the equipment and configuration signs. A change of the
equipment or the configuration rebuilds both parts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from astrometricslib.drivers import (
    get_camera_data_versions,
    get_frame_metric_signs,
    get_library_data_version,
    get_target_camera_names,
    get_target_data_version,
)
from astrometricslib.reports import observing_night_id
from wayfindinglib.analytics.capture_history import BACKGROUND_METRIC
from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog
from wayfindinglib.data_access.site_profile_reader import configured_observer_location
from wayfindinglib.drivers.control_record_store import BULK_GUIDING_SAMPLES_KIND, RECORD_VERSION_TABLES

if TYPE_CHECKING:
    from collections.abc import Hashable

    from wayfindinglib.analytics.night_index import NightIndex

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

NIGHT_LIST_TABLES = ("guiding_runs", "ekos_session_contexts")
"""The ``wayfinding.db`` tables the list of nights reads; all writes count."""

NIGHT_LIST_LIBRARY_KINDS = ("targets",)
"""The science library data the list of nights reads: the target documents,
which hold the frame records."""

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


def report_context_version(config: Any, butler: Any) -> Hashable | None:
    """Work out the version of a report's inputs that are not library data.

    These are the wayfinding records, the coarse guiding count, the active
    equipment and site, and the configuration sections (see the module
    description). `report_data_version` adds the library counters to them.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    butler : `DiskButler`
        The record store. It gives the wayfinding records.

    Returns
    -------
    version : `~collections.abc.Hashable` or `None`
        A value that is equal for two calls only when none of those inputs
        has visibly changed between them. `None` when it cannot be worked
        out, in which case the caller must not keep a result.
    """
    try:
        records = butler.control_records
        wayfinding = records.data_versions((*REPORT_EXACT_TABLES, BULK_GUIDING_SAMPLES_KIND))
        sample_count = records.guiding_sample_count()
        if wayfinding is None or sample_count is None:
            return None
        return (
            tuple(sorted(wayfinding.items())),
            sample_count // REPORT_COARSE_STEP,
            equipment_signs(config),
            config_signs(config),
        )
    except Exception:
        # A version that cannot be worked out only means no result is kept.
        logger.warning("Could not work out the context version of the target report.", exc_info=True)
        return None


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
    context = report_context_version(config, butler)
    library = get_library_data_version(config)
    if context is None or library is None:
        return None
    return (context, library)


def depth_goal_data_version(
    config: Any, butler: Any, target_id: str, now: float | None = None
) -> Hashable | None:
    """Work out the version of the inputs of one target's depth goal.

    The depth goal reads the target's frames, stacks and measurements, the
    chosen level, and the same context as a target report. Unlike
    `report_data_version` it does not watch the library counters, which a
    new frame of any target changes. It watches the one target's own data
    (`get_target_data_version`). The forecast also reads the frames, stacks
    and calibration records of the same camera from other targets (the
    efficiency baseline, the sky model and the noise model), so it watches
    the data of each camera the target used (`get_camera_data_versions`). A
    new frame from another camera leaves the version as it is.

    The camera part counts only the measurements of finished nights (the
    camera-wide estimate describes the camera's usual performance, and a
    night in progress is not representative; WAY-D107). A frame of the same
    camera taken tonight leaves it as it is, and it changes once when the
    night rolls over. The target's own part (`get_target_data_version`)
    still sees tonight's frames.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    butler : `DiskButler`
        The record store. It gives the wayfinding records.
    target_id : `str`
        The target's id.
    now : `float`, optional
        The moment that decides which night is not finished, as seconds since
        the Unix epoch. Defaults to the present.

    Returns
    -------
    version : `~collections.abc.Hashable` or `None`
        A value that is equal for two calls only when none of those inputs
        has visibly changed between them. `None` when it cannot be worked
        out, in which case the caller must not keep a result.
    """
    context = report_context_version(config, butler)
    target = get_target_data_version(config, target_id)
    cameras = get_target_camera_names(config, target_id)
    if context is None or target is None or cameras is None:
        return None
    camera_versions = get_camera_data_versions(
        config, cameras, finished_before_night=observing_night_id(time.time() if now is None else now)
    )
    if camera_versions is None:
        return None
    return (context, target, tuple(sorted(camera_versions.items())))


def nights_index_version(context: Any, tonight: str, signs: Hashable) -> Hashable | None:
    """Work out the version of the inputs of the index of the nights.

    The index (`NightIndex`) is built from the frames in the target
    documents, the Ekos session records and the guiding runs, so it watches
    the counters of those, the active equipment, the configuration and the
    night in progress. It does not watch the guiding samples.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the configuration (``config``) and the record store
        (``records``).
    tonight : `str`
        The night in progress.
    signs : `~collections.abc.Hashable`
        The equipment and configuration signs (`nights_context_signs`).

    Returns
    -------
    version : `~collections.abc.Hashable` or `None`
        A value that is equal for two calls only when none of those inputs
        has visibly changed between them. `None` when it cannot be worked
        out, in which case the caller must not keep a result.
    """
    try:
        wayfinding = context.records.data_versions(NIGHT_LIST_TABLES)
        targets = get_library_data_version(context.config, NIGHT_LIST_LIBRARY_KINDS)
        if wayfinding is None or targets is None:
            return None
        return (tuple(sorted(wayfinding.items())), targets, tonight, signs)
    except Exception:
        # A version that cannot be worked out only means no result is kept.
        logger.warning("Could not work out the data version of the index of the nights.", exc_info=True)
        return None


def nights_context_signs(config: Any) -> Hashable:
    """Describe the equipment and configuration the list of nights reads.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.

    Returns
    -------
    signs : `~collections.abc.Hashable`
        The active equipment and site, then the fingerprint of the
        configuration sections. Changing either changes the list.
    """
    return (equipment_signs(config), config_signs(config))


def nights_data_versions(
    context: Any, index: NightIndex, signs: Hashable
) -> tuple[Hashable, Hashable] | None:
    """Work out the versions of the two parts of the list of nights.

    The list reads the frames, the Ekos session records, the guiding runs
    and each frame's stored sky background. It does not read the guiding
    samples, which a live night adds every few seconds. It is kept in two
    parts, and each has its own version:

    - The finished nights: the digest of their frames and sessions
      (`NightIndex.finished_digest`) and the stored background rows of their
      frames. A frame, a session or a measurement
      added tonight leaves this version as it is. A change that touches a
      finished night (a restack that moves or removes a frame, an old night
      ingested late, a measurement added to an old frame) changes it. It
      also changes once, when the night rolls over.
    - The night in progress: its frames and sessions
      (`NightIndex.tonight_signature`) and the stored background rows of its
      frames.

    Both include the active equipment, the configured site and the
    configuration signs.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the configuration (``config``).
    index : `NightIndex`
        The nights' frames and sessions.
    signs : `~collections.abc.Hashable`
        The equipment and configuration signs (`nights_context_signs`).

    Returns
    -------
    versions : `tuple` or `None`
        Two `~collections.abc.Hashable` values: the finished nights'
        version, then the night in progress's. `None`
        when they cannot be worked out, in which case the caller must not
        keep a result.
    """
    try:
        background = get_frame_metric_signs(
            context.config, BACKGROUND_METRIC, (index.finished_subject_ids, index.tonight_subject_ids)
        )
        if background is None:
            return None
        return (
            (index.finished_digest, background[0], signs),
            (index.tonight_signature, background[1], signs),
        )
    except Exception:
        # A version that cannot be worked out only means no result is kept.
        logger.warning("Could not work out the data versions of the list of nights.", exc_info=True)
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
        logger.warning("Could not work out the data version of the night review.", exc_info=True)
        return None
