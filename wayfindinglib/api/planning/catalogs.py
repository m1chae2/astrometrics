"""Purpose: `PlanningCatalogs`, the sky catalogs of planning.

Reached as ``wayfinder.planning.catalogs``.

Description: `query` lists the online catalog drivers and `query_sources` the
targets and stars near a point, from the library, SIMBAD and those drivers,
in the shape the Planetarium draws. The deep-star catalog is a local copy of
Gaia stars the Planetarium draws faint stars from: `get_deep_status` says how
much is installed, `predict_deep_catalog` guesses its finished size from a
sample and `record_deep_catalog` downloads it. Each method is a thin wrapper
of one owner function in `wayfindinglib.tasks.planning_tasks.catalog_answers`.
"""

from astrometricslib.arguments import Page
from astrometricslib.jobs import background_job
from wayfindinglib._internal.renames import MovedNames
from wayfindinglib.models.planning.deep_catalog import (
    DeepCatalogBuildReport,
    DeepCatalogEstimate,
    DeepCatalogStatus,
)
from wayfindinglib.models.planning.sky_catalogs import CatalogDriverInfo
from wayfindinglib.models.planning.sky_source import SourceQueryResult
from wayfindinglib.tasks.planning_tasks.planning_context import PlanningContext

__all__ = ["PlanningCatalogs"]


class PlanningCatalogs(MovedNames):
    """The online catalogs, sources near a point and the deep-star catalog.

    Parameters
    ----------
    context : `PlanningContext`
        The settings and storage the planning objects share.
    """

    def __init__(self, context: PlanningContext) -> None:
        self._context = context

    def query(self, *, limit: int | None = None, offset: int = 0) -> Page[CatalogDriverInfo]:
        """List the registered online catalog drivers.

        Owner:
            wayfindinglib.tasks.planning_tasks.catalog_answers.query_catalogs

        Parameters
        ----------
        limit : `int`, optional
            Most drivers to return. `None` (the default) returns every one.
        offset : `int`, optional
            How many to skip first.

        Returns
        -------
        drivers : `Page` [`CatalogDriverInfo`]
            One entry for every registered online catalog driver.
        """
        from wayfindinglib.tasks.planning_tasks.catalog_answers import query_catalogs

        return query_catalogs(self._context, limit, offset)

    def query_sources(
        self,
        ra_deg: float,
        dec_deg: float,
        radius_deg: float = 1.0,
        *,
        source: list[str] | None = None,
        magnitude_min: float | None = None,
        magnitude_max: float | None = None,
        include_stars_without_catalog_magnitude: bool = True,
        catalog_drivers: list[str] | None = None,
        limit: int | None = None,
    ) -> SourceQueryResult:
        """List the targets and stars near a point, from the places named.

        Targets show their stacked image, or their longest LIGHT frame when
        they have no stack. Library stars are read from the library's indexed
        summaries, so a wide region stays fast. Single-frame photometry
        detections are never listed.

        Owner:
            wayfindinglib.tasks.planning_tasks.catalog_answers.query_sources

        Parameters
        ----------
        ra_deg : `float`
            Right ascension of the center, in ICRS (J2000) degrees.
        dec_deg : `float`
            Declination of the center, in ICRS (J2000) degrees.
        radius_deg : `float`, optional
            Search radius, in degrees. Defaults to 1.
        source : `list` [`str`], optional
            Where to look: ``"targets"`` (the library's targets), ``"stars"``
            (the library's stars), ``"simbad"`` (SIMBAD's objects that the
            library does not hold) and ``"catalogs"`` (the online catalog
            drivers named in ``catalog_drivers``). Targets and stars when
            omitted.
        magnitude_min : `float`, optional
            Brightest star magnitude to keep. Stars with no catalog magnitude
            are then left out.
        magnitude_max : `float`, optional
            Faintest star magnitude to keep. Targets are never left out.
        include_stars_without_catalog_magnitude : `bool`, optional
            Keep library and SIMBAD stars that have no real catalog
            magnitude. Defaults to `True`.
        catalog_drivers : `list` [`str`], optional
            The online catalog drivers to ask, such as ``["deep_stars"]``.
            Needed with ``"catalogs"``, and allowed only with it.
        limit : `int`, optional
            Most stars to list, brightest first. `None` (the default) lists
            every star in the order found. Targets are never cut.

        Returns
        -------
        result : `SourceQueryResult`
            The sources, the number of stars found before the cut, whether a
            cut dropped any (a catalog driver's own cap counts), and the
            catalog drivers that failed.

        Notes
        -----
        Raises `InvalidArgumentError` if ``source`` names an unknown place,
        ``"catalogs"`` and ``catalog_drivers`` do not come together, or a
        star limit is given with no stars asked for.
        """
        from wayfindinglib.tasks.planning_tasks.catalog_answers import query_sources

        return query_sources(
            self._context,
            ra_deg,
            dec_deg,
            radius_deg,
            source if source is not None else ("targets", "stars"),
            magnitude_min,
            magnitude_max,
            include_stars_without_catalog_magnitude,
            catalog_drivers or (),
            limit,
        )

    def get_deep_status(self) -> DeepCatalogStatus:
        """Report how much of the deep-star catalog is installed.

        Reads the disk only.

        Owner:
            wayfindinglib.tasks.planning_tasks.catalog_answers.get_deep_status

        Returns
        -------
        status : `DeepCatalogStatus`
            Whether it is installed and complete, its star count, chunks and
            size.
        """
        from wayfindinglib.tasks.planning_tasks.catalog_answers import get_deep_status

        return get_deep_status(self._context)

    @background_job("planning", grace_period_seconds=20.0)
    def predict_deep_catalog(
        self,
        *,
        healpix_level: int | None = None,
        magnitude_max: float | None = None,
        sample_count: int | None = None,
        query_timeout_s: float | None = None,
        request_delay_s: float | None = None,
    ) -> DeepCatalogEstimate:
        """Guess the finished size of the deep-star catalog.

        Counts the stars in a sample of chunks, which sends about two dozen
        queries to the Gaia archive. Nothing is saved. The run is recorded as
        a job.

        Owner:
            wayfindinglib.tasks.planning_tasks.catalog_answers.predict_deep_catalog

        Parameters
        ----------
        healpix_level : `int`, optional
            How finely the estimate cuts the sky.
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
            The guessed star count and size, with the spread of the sample.
        """
        from wayfindinglib.tasks.planning_tasks.catalog_answers import predict_deep_catalog

        return predict_deep_catalog(
            healpix_level, magnitude_max, sample_count, query_timeout_s, request_delay_s
        )

    @background_job("planning", grace_period_seconds=20.0)
    def record_deep_catalog(
        self,
        *,
        healpix_level: int | None = None,
        magnitude_max: float | None = None,
        request_delay_s: float | None = None,
        max_attempts: int | None = None,
        query_timeout_s: float | None = None,
        maximum_pixels: int | None = None,
        pixels: list[int] | None = None,
    ) -> DeepCatalogBuildReport:
        """Download the deep-star catalog the Planetarium draws from.

        Chunks saved by an earlier run are skipped, so a stopped run resumes.
        A catalog made with a different depth or chunk size is refused. The
        run is recorded as a job.

        Owner:
            wayfindinglib.tasks.planning_tasks.catalog_answers.record_deep_catalog

        Parameters
        ----------
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
            Stop after downloading this many chunks.
        pixels : `list` [`int`], optional
            Only these chunks, instead of the whole sky.

        Returns
        -------
        report : `DeepCatalogBuildReport`
            The chunks asked for, downloaded and failed, the stars added, the
            time taken and whether it stopped early.
        """
        from wayfindinglib.tasks.planning_tasks.catalog_answers import record_deep_catalog

        return record_deep_catalog(
            self._context,
            healpix_level,
            magnitude_max,
            request_delay_s,
            max_attempts,
            query_timeout_s,
            maximum_pixels,
            pixels,
        )
