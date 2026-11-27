"""Tests for the one source query of ``wayfinder.planning.catalogs``.

These tests use a fake sky engine and check that `query_sources` sorts, cuts
and reports the total, takes sources from the places asked for, and refuses
arguments that do not go together.
"""

import pytest

from astrometricslib.exceptions import InvalidArgumentError
from wayfindinglib.api.planning.catalogs import PlanningCatalogs
from wayfindinglib.models.planning.sky_source import SkySource
from wayfindinglib.tasks.planning_tasks import sky_sources
from wayfindinglib.tasks.planning_tasks.planning_context import PlanningContext


def make_catalogs() -> PlanningCatalogs:
    """Build the catalogs object over a fake sky engine.

    Returns
    -------
    catalogs : `PlanningCatalogs`
        An object whose sky engine is a stand-in; the tests replace the
        source readers it would call.
    """
    context = PlanningContext.__new__(PlanningContext)
    context._sky_engine = object()
    return PlanningCatalogs(context)


def sky_source(source_id: str, kind: str = "star", magnitude: float | None = None) -> SkySource:
    """Build one sky source.

    Returns
    -------
    source : `SkySource`
        A source of the given kind and magnitude.
    """
    return SkySource(
        id=source_id, ra=1.0, dec=2.0, name=source_id, common_name=source_id, type=kind, magnitude=magnitude
    )


@pytest.fixture
def library_sources(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Make the library read return five stars and one target.

    Returns
    -------
    calls : `list` [`dict`]
        The keyword arguments of every library read.
    """
    calls: list[dict] = []
    found = [sky_source("M 57", "target")] + [
        sky_source(f"S{n}", magnitude=m) for n, m in enumerate([9.0, None, 3.0, 5.0, 1.0])
    ]

    def fake_collect(
        sky: object, ra: float, dec: float, radius: float, **keywords: object
    ) -> list[SkySource]:
        """Record the call and return the prepared sources.

        Returns
        -------
        sources : `list` [`SkySource`]
            The prepared sources.
        """
        calls.append(keywords)
        return list(found)

    monkeypatch.setattr(sky_sources, "collect_sky_sources", fake_collect)
    return calls


def test_stars_come_brightest_first_and_are_cut_with_the_total_reported(library_sources: list[dict]) -> None:
    """Five stars, limit two: the two brightest, and the cut is stated."""
    answer = make_catalogs().query_sources(284.0, 32.6, 1.0, limit=2)
    assert [item.id for item in answer.sources] == ["M 57", "S4", "S2"]
    assert answer.total_stars == 5
    assert answer.stars_truncated is True


def test_no_limit_keeps_every_star_in_the_order_found(library_sources: list[dict]) -> None:
    """limit=None does not sort or cut."""
    answer = make_catalogs().query_sources(284.0, 32.6, 1.0, limit=None)
    assert [item.id for item in answer.sources] == ["M 57", "S0", "S1", "S2", "S3", "S4"]
    assert answer.stars_truncated is False


def test_the_places_asked_for_reach_the_library_read(library_sources: list[dict]) -> None:
    """The places and magnitude limits reach the library read."""
    make_catalogs().query_sources(
        284.0, 32.6, 1.0, source=["stars", "simbad"], magnitude_max=8.0, magnitude_min=2.0
    )
    assert library_sources == [
        {
            "include_stars": True,
            "include_online": True,
            "limiting_magnitude": 8.0,
            "include_stars_without_catalog_magnitude": True,
            "include_targets": False,
            "magnitude_min": 2.0,
        }
    ]


def test_catalogs_are_asked_without_reading_the_library(
    library_sources: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Online catalogs are asked by name and the library is not read."""
    asked = []

    def fake_online(
        sky: object, ra: float, dec: float, radius: float, drivers: list[str], limit: float | None
    ) -> tuple[list[SkySource], bool, list[str]]:
        """Record the driver names and the magnitude limit.

        Returns
        -------
        sources : `list` [`SkySource`]
            One global star.
        truncated : `bool`
            True, as a driver that hit its cap would say.
        failed_catalogs : `list` [`str`]
            One failed driver name.
        """
        asked.append((drivers, limit))
        return [sky_source("Gaia 1", magnitude=12.0)], True, ["hipparcos"]

    monkeypatch.setattr(sky_sources, "online_catalog_sources", fake_online)

    answer = make_catalogs().query_sources(
        1.0, 2.0, 0.5, source=["catalogs"], catalog_drivers=["deep_stars"], magnitude_max=14.0
    )

    assert asked == [(["deep_stars"], 14.0)]
    assert library_sources == []
    assert [item.id for item in answer.sources] == ["Gaia 1"]
    assert answer.stars_truncated is True
    assert answer.failed_catalogs == ["hipparcos"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"source": ["catalogs"]},
        {"source": ["stars"], "catalog_drivers": ["deep_stars"]},
        {"source": ["targets"], "magnitude_max": 8.0},
        {"source": ["comets"]},
    ],
)
def test_arguments_that_do_not_go_together_are_refused(arguments: dict) -> None:
    """Bad combinations of arguments raise `InvalidArgumentError`."""
    with pytest.raises(InvalidArgumentError):
        make_catalogs().query_sources(1.0, 2.0, 0.5, **arguments)


def test_a_star_limit_below_one_is_refused() -> None:
    """A star limit below 1 raises; it is never clamped. No upper bound."""
    with pytest.raises(InvalidArgumentError, match="limit"):
        make_catalogs().query_sources(284.0, 32.6, 1.0, limit=0)


def test_every_star_comes_back_by_default(library_sources: list[dict]) -> None:
    """With no limit the query returns every match, as every query does."""
    answer = make_catalogs().query_sources(284.0, 32.6)
    assert answer.total_stars == 5
    assert len(answer.sources) == 6
