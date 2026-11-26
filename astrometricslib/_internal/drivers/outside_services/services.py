"""Purpose: Name each outside service and state how fast it may be asked.

Description: Every question to a computer outside this one goes through the
gateway in this folder. This file holds the one table of those services. Each
`OutsideService` lists the host names it answers on, how many requests per
second one host may be sent, how long to wait for an answer, and how the
gateway backs off after trouble. The values are design estimates, kept in the
validation register (entries AST-D26 and AST-D27 in
``documentation/library_design/validation/astrometry.md``), and are chosen
below the figures the services publish, where they publish one.

Published policies, as far as they are known:

- SIMBAD (CDS Strasbourg, and its Harvard mirror): the SIMBAD documentation
  says that a client sending more than about 6 queries per second can be
  blacklisted. The limit here is 4 per second per host.
- VizieR (the same two CDS hosts): CDS runs one fair-use policy for its query
  services, so VizieR gets the same 4 per second. The VizieR page itself was
  not read for this value.
- ESA Gaia archive: no requests-per-second figure is published. The archive
  limits job size and run time instead. One request per second is a
  conservative estimate.
- SkyBoT (IMCCE), the Astrometry.net web service and file downloads: no figure
  is published; one request per second per host.
- The CDS TAP server that Siril asks for Gaia stars in a color calibration
  (``tapvizier.u-strasbg.fr``): the program only opens a connection to it, to
  learn whether Siril would wait for it, so the default figure applies. It is
  a separate service from VizieR: it is a different host name from the two
  VizieR hosts above, and pauses are kept for each host.

The numbers are plain constants, not tuned parameters: they limit how the
program behaves toward other people's servers, and they never change a result.
"""

from dataclasses import dataclass

from astrometricslib.foundation.config import SIMBAD_SERVER_CHOICES, VIZIER_SERVER_CHOICES

# Requests per second one SIMBAD or VizieR host may be sent. The published
# blacklisting threshold is about 6 per second; 4 leaves a margin for the
# extra request pyvo sends when it first reads a service's capabilities.
CDS_REQUESTS_PER_SECOND = 4.0

# Requests per second for every service with no published figure.
DEFAULT_REQUESTS_PER_SECOND = 1.0

# How many consecutive timeouts, refused connections or server errors a host
# may give before the gateway stops sending it requests for a while. The first
# one is often a passing network hiccup (the same reason the old Gaia run
# breaker waited for three), so one failure alone starts no pause.
FAILURES_BEFORE_BACKOFF = 2

# The first pause after trouble, in seconds, and the longest a pause may grow
# to. Each further failure in a row doubles the pause. A host that answers 429
# or 503 with a Retry-After header is paused for the time it names instead.
BACKOFF_BASE_SECONDS = 60.0
BACKOFF_CAP_SECONDS = 900.0

# The longest Retry-After the gateway honors, in seconds. A longer request is
# cut to this, so a wrong header cannot silence a service for a day.
RETRY_AFTER_CAP_SECONDS = 3600.0

# How many times a request is repeated on the same host when it answers that it
# is too busy (without a Retry-After), and how long to wait before each repeat.
# Harvard's SIMBAD sheds load with "TAP service too busy" for a few seconds at
# a time; one repeat after a short wait usually gets through. Seen on the real
# server on 2026-11-22.
BUSY_RETRIES = 2
BUSY_WAIT_SECONDS = 2.0

# How long to wait on one HTTP request before giving up, in seconds, per
# service. requests applies no timeout of its own, so without one a stalled
# connection blocks the calling run forever.
SIMBAD_TIMEOUT_SECONDS = 30.0
VIZIER_TIMEOUT_SECONDS = 60.0
ESA_TIMEOUT_SECONDS = 20.0
GAIA_XP_TIMEOUT_SECONDS = 60.0
SKYBOT_TIMEOUT_SECONDS = 60.0
# A solve on Astrometry.net's servers can run for minutes.
ASTROMETRY_NET_TIMEOUT_SECONDS = 300.0
DOWNLOAD_TIMEOUT_SECONDS = 60.0
# Siril waited 2 min 24 s on this server when it did not answer (measured
# 2026-11-23), and held the one Siril slot meanwhile. A server that answers
# does so within a second, so a few seconds are enough to call it down.
SIRIL_GAIA_CATALOG_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class OutsideService:
    """One outside service and the rules for asking it.

    Attributes
    ----------
    name : `str`
        The name used in messages and counters, such as ``"SIMBAD"``.
    hosts : `tuple` [`str`]
        The host names that serve it. The first is the default. Rate limits
        and pauses are kept for each host.
    requests_per_second : `float`
        The most requests one host is sent each second.
    timeout_seconds : `float`
        How long one request may take.
    busy_retries : `int`
        Repeats on the same host after a "too busy" reply.
    busy_wait_seconds : `float`
        The wait before each of those repeats.
    failures_before_backoff : `int`
        Consecutive timeouts or connection failures that start a pause.
    backoff_base_seconds, backoff_cap_seconds : `float`
        The first pause and the longest one.
    """

    name: str
    hosts: tuple[str, ...]
    requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND
    timeout_seconds: float = 30.0
    busy_retries: int = 0
    busy_wait_seconds: float = BUSY_WAIT_SECONDS
    failures_before_backoff: int = FAILURES_BEFORE_BACKOFF
    backoff_base_seconds: float = BACKOFF_BASE_SECONDS
    backoff_cap_seconds: float = BACKOFF_CAP_SECONDS


SIMBAD = OutsideService(
    "SIMBAD",
    SIMBAD_SERVER_CHOICES,
    CDS_REQUESTS_PER_SECOND,
    SIMBAD_TIMEOUT_SECONDS,
    busy_retries=BUSY_RETRIES,
)
VIZIER = OutsideService("VizieR", VIZIER_SERVER_CHOICES, CDS_REQUESTS_PER_SECOND, VIZIER_TIMEOUT_SECONDS)
ESA_GAIA = OutsideService("ESA Gaia archive", ("gea.esac.esa.int",), timeout_seconds=ESA_TIMEOUT_SECONDS)
GAIA_XP = OutsideService("Gaia XP", ("gea.esac.esa.int",), timeout_seconds=GAIA_XP_TIMEOUT_SECONDS)
SKYBOT = OutsideService("SkyBoT", ("vo.imcce.fr",), timeout_seconds=SKYBOT_TIMEOUT_SECONDS)
ASTROMETRY_NET = OutsideService(
    "Astrometry.net", ("nova.astrometry.net",), timeout_seconds=ASTROMETRY_NET_TIMEOUT_SECONDS
)
DOWNLOADS = OutsideService("Downloads", ("downloads",), timeout_seconds=DOWNLOAD_TIMEOUT_SECONDS)
SIRIL_GAIA_CATALOG = OutsideService(
    "Siril Gaia catalog",
    ("tapvizier.u-strasbg.fr",),
    timeout_seconds=SIRIL_GAIA_CATALOG_TIMEOUT_SECONDS,
)

#: Every service by name.
SERVICES: dict[str, OutsideService] = {
    service.name: service
    for service in (SIMBAD, VIZIER, ESA_GAIA, GAIA_XP, SKYBOT, ASTROMETRY_NET, DOWNLOADS, SIRIL_GAIA_CATALOG)
}
