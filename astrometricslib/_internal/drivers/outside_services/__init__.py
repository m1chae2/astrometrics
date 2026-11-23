"""Purpose: The gateway to every outside service the libraries ask.

Description: SIMBAD, VizieR, the ESA Gaia archive (and its XP spectra), SkyBoT,
Astrometry.net and file downloads are asked only through this package. It
applies one rate limit per host across processes, backs a host off after 429,
503 or repeated timeouts, picks and falls back between a service's servers, and
counts what happened for the run. See ``drivers/README.md`` ("Talking to
outside services"). No module outside this package may import a network client.
"""

from astrometricslib._internal.drivers.outside_services.clients import (
    SIMBAD_LOCK,
    AstrometryNetSolver,
    GaiaArchive,
    gaia_archive_load_data,
    gaia_archive_query,
    get_gaia_archive,
    open_download,
    simbad_query_object,
    simbad_query_region,
    simbad_query_tap,
    skybot_cone_search,
    vizier_query_region,
)
from astrometricslib._internal.drivers.outside_services.failures import (
    DROPPED_CONNECTION_ERRORS,
    HTTP_PROTOCOL_ERRORS,
    Failure,
    FailureKind,
    ServicePausedError,
    ServiceTimeoutError,
    classify_failure,
)
from astrometricslib._internal.drivers.outside_services.gateway import (
    ServiceEvent,
    call_service,
    hosts_in_order,
    merge_identical_requests,
    record_service_events,
    run_with_deadline,
)
from astrometricslib._internal.drivers.outside_services.services import (
    ASTROMETRY_NET,
    DOWNLOADS,
    ESA_GAIA,
    GAIA_XP,
    SERVICES,
    SIMBAD,
    SKYBOT,
    VIZIER,
    OutsideService,
)

__all__ = [
    "ASTROMETRY_NET",
    "DOWNLOADS",
    "DROPPED_CONNECTION_ERRORS",
    "ESA_GAIA",
    "GAIA_XP",
    "HTTP_PROTOCOL_ERRORS",
    "SERVICES",
    "SIMBAD",
    "SIMBAD_LOCK",
    "SKYBOT",
    "VIZIER",
    "AstrometryNetSolver",
    "Failure",
    "FailureKind",
    "GaiaArchive",
    "OutsideService",
    "ServiceEvent",
    "ServicePausedError",
    "ServiceTimeoutError",
    "call_service",
    "classify_failure",
    "gaia_archive_load_data",
    "gaia_archive_query",
    "get_gaia_archive",
    "hosts_in_order",
    "merge_identical_requests",
    "open_download",
    "record_service_events",
    "run_with_deadline",
    "simbad_query_object",
    "simbad_query_region",
    "simbad_query_tap",
    "skybot_cone_search",
    "vizier_query_region",
]
