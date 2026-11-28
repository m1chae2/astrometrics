"""Purpose: Browse the sky catalogs and manage the deep-star catalog.

Description: The owner functions behind ``wayfinder.planning.catalogs``:

* `query_catalogs`: the registered online catalog drivers.
* `query_sources`: the targets and stars near a point, from the library,
  SIMBAD and the online catalog drivers, in the shape the Planetarium draws
  (`sky_sources` gathers them).
* `get_deep_status`, `predict_deep_catalog` and `record_deep_catalog`: how
  much of the local copy of the Gaia catalog is installed, a guess of its
  finished size from a sample, and the download itself
  (`wayfindinglib.drivers.catalog`).
"""

from collections.abc import Sequence

from astrometricslib.arguments import Page, check_include, check_limit
from astrometricslib.exceptions import InvalidArgumentError
from wayfindinglib.models.planning.deep_catalog import (
    DeepCatalogBuildReport,
    DeepCatalogEstimate,
    DeepCatalogStatus,
)
from wayfindinglib.models.planning.sky_catalogs import CatalogDriverInfo
from wayfindinglib.models.planning.sky_source import SkySource, SourceQueryResult
from wayfindinglib.tasks.planning_tasks.planning_context import PlanningContext

__all__ = [
    "SOURCE_PLACES",
    "get_deep_status",
    "predict_deep_catalog",
    "query_catalogs",
    "query_sources",
    "record_deep_catalog",
]

SOURCE_PLACES = ("targets", "stars", "simbad", "catalogs")
"""Where `query_sources` can look."""


def query_catalogs(
    context: PlanningContext, limit: int | None = None, offset: int = 0
) -> Page[CatalogDriverInfo]:
    """List the registered online catalog drivers.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    limit : `int`, optional
        Most drivers to return; `None` returns every one.
    offset : `int`, optional
        How many to skip.

    Returns
    -------
    drivers : `Page` [`CatalogDriverInfo`]
        One entry for every registered online catalog driver.
    """
    check_limit(limit, 1, None)
    check_limit(offset, 0, None, name="offset", allow_none=False)
    drivers = [
        CatalogDriverInfo.model_validate(row) for row in context.sky_engine.list_catalog_driver_metadata()
    ]
    return Page.of(drivers, offset=offset, limit=limit)


def query_sources(
    context: PlanningContext,
    ra_deg: float,
    dec_deg: float,
    radius_deg: float = 1.0,
    source: Sequence[str] = ("targets", "stars"),
    magnitude_min: float | None = None,
    magnitude_max: float | None = None,
    include_stars_without_catalog_magnitude: bool = True,
    catalog_drivers: Sequence[str] = (),
    limit: int | None = None,
) -> SourceQueryResult:
    """List the targets and stars near a point, from the places named.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    ra_deg, dec_deg : `float`
        The center, ICRS degrees.
    radius_deg : `float`, optional
        The search radius, in degrees.
    source : `Sequence` [`str`], optional
        Where to look, from `SOURCE_PLACES`.
    magnitude_min, magnitude_max : `float`, optional
        The brightest and faintest star magnitudes to keep.
    include_stars_without_catalog_magnitude : `bool`, optional
        Keep library and SIMBAD stars with no real catalog magnitude.
    catalog_drivers : `Sequence` [`str`], optional
        The online catalog drivers to ask; needed with ``"catalogs"``.
    limit : `int`, optional
        Most stars to list, brightest first. `None` lists every star in the
        order found. Targets are never cut.

    Returns
    -------
    result : `SourceQueryResult`
        The sources, the number of stars before the cut, whether a cut
        dropped any, and the catalog drivers that failed.

    Raises
    ------
    InvalidArgumentError
        If ``source`` names an unknown place, ``"catalogs"`` and
        ``catalog_drivers`` do not come together, a star limit is given with
        no stars asked for, or ``limit`` is below 1.
    """
    from wayfindinglib.tasks.planning_tasks import sky_sources

    check_limit(limit, 1, None)
    sections = check_include(source, SOURCE_PLACES)
    if ("catalogs" in sections) != bool(catalog_drivers):
        raise InvalidArgumentError("catalog_drivers goes with source 'catalogs', and 'catalogs' needs it.")
    asks_for_stars = bool(sections & {"stars", "simbad", "catalogs"})
    if not asks_for_stars and (
        magnitude_min is not None or magnitude_max is not None or not include_stars_without_catalog_magnitude
    ):
        raise InvalidArgumentError(
            "The magnitude limits only apply when source has 'stars', 'simbad' or 'catalogs'."
        )
    found: list[SkySource] = []
    catalogs_cut_short = False
    failed_catalogs: list[str] = []
    if sections & {"targets", "stars", "simbad"}:
        found.extend(
            sky_sources.collect_sky_sources(
                context.sky_engine,
                ra_deg,
                dec_deg,
                radius_deg,
                include_stars="stars" in sections,
                include_online="simbad" in sections,
                limiting_magnitude=magnitude_max,
                include_stars_without_catalog_magnitude=include_stars_without_catalog_magnitude,
                include_targets="targets" in sections,
                magnitude_min=magnitude_min,
            )
        )
    if "catalogs" in sections:
        from_catalogs, catalogs_cut_short, failed_catalogs = sky_sources.online_catalog_sources(
            context.sky_engine, ra_deg, dec_deg, radius_deg, list(catalog_drivers), magnitude_max
        )
        found.extend(
            catalog_source
            for catalog_source in from_catalogs
            if magnitude_min is None
            or (catalog_source.has_catalog_magnitude and catalog_source.magnitude >= magnitude_min)
        )
    targets = [item for item in found if item.type == "target"]
    stars = [item for item in found if item.type == "star"]
    total_stars = len(stars)
    if limit is not None:
        stars.sort(key=lambda item: (item.magnitude is None, item.magnitude or 0.0))
    truncated = catalogs_cut_short or (limit is not None and total_stars > limit)
    return SourceQueryResult(
        sources=[*targets, *(stars[:limit] if limit is not None else stars)],
        total_stars=total_stars,
        stars_truncated=truncated,
        failed_catalogs=failed_catalogs,
    )


def get_deep_status(context: PlanningContext) -> DeepCatalogStatus:
    """Report how much of the deep-star catalog is installed.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.

    Returns
    -------
    status : `DeepCatalogStatus`
        What is on disk. Reads the disk only.
    """
    from wayfindinglib.drivers.catalog import deep_star_store

    return DeepCatalogStatus.model_validate(deep_star_store.read_catalog_status(context.config))


def _given(**settings: object) -> dict[str, object]:
    """Keep the settings the caller gave.

    Parameters
    ----------
    **settings : `object`
        Setting name -> value, `None` for one not given.

    Returns
    -------
    given : `dict` [`str`, `object`]
        The settings that are not `None`.
    """
    return {name: value for name, value in settings.items() if value is not None}


def predict_deep_catalog(
    healpix_level: int | None = None,
    magnitude_max: float | None = None,
    sample_count: int | None = None,
    query_timeout_s: float | None = None,
    request_delay_s: float | None = None,
) -> DeepCatalogEstimate:
    """Guess the finished size of the deep-star catalog from a sample.

    Counts the stars in a sample of chunks, which sends about two dozen
    queries to the Gaia archive. Nothing is saved.

    Parameters
    ----------
    healpix_level : `int`, optional
        How finely the sky is cut into chunks.
    magnitude_max : `float`, optional
        Count only stars brighter than this Gaia G magnitude.
    sample_count : `int`, optional
        How many chunks to count.
    query_timeout_s : `float`, optional
        Seconds to wait for each count.
    request_delay_s : `float`, optional
        Seconds to pause between counts.

    Returns
    -------
    estimate : `DeepCatalogEstimate`
        The guess and the spread of the sample.
    """
    from astrometricslib.jobs import registered_job
    from wayfindinglib.drivers.catalog.deep_star_catalog_builder import estimate_catalog_size

    with registered_job(job_type="planning", target_id="deep_catalog"):
        estimate = estimate_catalog_size(
            **_given(
                healpix_level=healpix_level,
                magnitude_limit=magnitude_max,
                sample_count=sample_count,
                query_timeout_seconds=query_timeout_s,
                request_delay_seconds=request_delay_s,
            )
        )
    return DeepCatalogEstimate.model_validate(estimate)


def record_deep_catalog(
    context: PlanningContext,
    healpix_level: int | None = None,
    magnitude_max: float | None = None,
    request_delay_s: float | None = None,
    max_attempts: int | None = None,
    query_timeout_s: float | None = None,
    maximum_pixels: int | None = None,
    pixels: Sequence[int] | None = None,
) -> DeepCatalogBuildReport:
    """Download the deep-star catalog, skipping chunks already saved.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state; its settings say where to save.
    healpix_level : `int`, optional
        How finely the sky is cut into chunks.
    magnitude_max : `float`, optional
        Download only stars brighter than this Gaia G magnitude.
    request_delay_s : `float`, optional
        Seconds to pause between requests.
    max_attempts : `int`, optional
        How many times to try one chunk before skipping it.
    query_timeout_s : `float`, optional
        Seconds to wait for the archive on each attempt.
    maximum_pixels : `int`, optional
        Stop after this many chunks.
    pixels : `Sequence` [`int`], optional
        Only these chunks, instead of the whole sky.

    Returns
    -------
    report : `DeepCatalogBuildReport`
        The chunks asked for, downloaded and failed, the stars added, the
        time taken and whether it stopped early.
    """
    from astrometricslib.jobs import registered_job
    from wayfindinglib.drivers.catalog.deep_star_catalog_builder import build_deep_star_catalog

    with registered_job(job_type="planning", target_id="deep_catalog"):
        report = build_deep_star_catalog(
            context.config,
            **_given(
                healpix_level=healpix_level,
                magnitude_limit=magnitude_max,
                request_delay_seconds=request_delay_s,
                max_attempts=max_attempts,
                query_timeout_seconds=query_timeout_s,
                maximum_pixels=maximum_pixels,
                pixels=None if pixels is None else list(pixels),
            ),
        )
    return DeepCatalogBuildReport.model_validate(report)
