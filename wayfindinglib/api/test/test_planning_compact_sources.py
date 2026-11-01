"""Tests for the one source query and the short coordinate lookup.

These tests use a fake sky engine and check that `query_sources` sorts, cuts
and reports the total, takes sources from the places asked for, and refuses
arguments that do not go together, and that `lookup_coordinates` answers
with a position.
"""

import pytest

from astrometricslib import InvalidArgumentError, StellarObject, Target
from wayfindinglib.api.planning import ObservationPlanning
from wayfindinglib.models.planning.sky_source import SkySource
from wayfindinglib.tasks.planning_tasks import sky_sources


def make_planning(resolved: object | None = None) -> ObservationPlanning:
    """Build a planning object over a fake sky engine.

    Returns
    -------
    planning : `ObservationPlanning`
        An object whose engine answers name lookups from ``resolved``.
    """

    class FakeSkyEngine:
        """A sky engine that only resolves names."""

        def resolve_target_coordinates(self, name: str) -> object:
            """Answer a name lookup.

            Returns
            -------
            found : `object`
                The prepared answer.

            Raises
            ------
            ValueError
                If nothing was prepared.
            """
            if resolved is None:
                raise ValueError(f"cannot resolve {name}")
            return resolved

    planning = ObservationPlanning.__new__(ObservationPlanning)
    planning._ObservationPlanning__sky_engine = FakeSkyEngine()  # the lazily built engine
    return planning


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
    answer = make_planning().query_sources(284.0, 32.6, 1.0, limit=2)
    assert [item.id for item in answer.sources] == ["M 57", "S4", "S2"]
    assert answer.total_stars == 5
    assert answer.stars_truncated is True


def test_no_limit_keeps_every_star_in_the_order_found(library_sources: list[dict]) -> None:
    """limit=None does not sort or cut."""
    answer = make_planning().query_sources(284.0, 32.6, 1.0, limit=None)
    assert [item.id for item in answer.sources] == ["M 57", "S0", "S1", "S2", "S3", "S4"]
    assert answer.stars_truncated is False


def test_the_places_asked_for_reach_the_library_read(library_sources: list[dict]) -> None:
    """The places and magnitude limits reach the library read."""
    make_planning().query_sources(
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
    ) -> list[SkySource]:
        """Record the driver names and the magnitude limit.

        Returns
        -------
        sources : `list` [`SkySource`]
            One global star.
        """
        asked.append((drivers, limit))
        return [sky_source("Gaia 1", magnitude=12.0)]

    monkeypatch.setattr(sky_sources, "online_catalog_sources", fake_online)

    answer = make_planning().query_sources(
        1.0, 2.0, 0.5, source=["catalogs"], catalog_drivers=["deep_stars"], magnitude_max=14.0
    )

    assert asked == [(["deep_stars"], 14.0)]
    assert library_sources == []
    assert [item.id for item in answer.sources] == ["Gaia 1"]


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
        make_planning().query_sources(1.0, 2.0, 0.5, **arguments)


def test_a_target_is_looked_up_as_a_short_position() -> None:
    """The reply is a position, not the target record."""
    target = Target(id="M 57", ra="18 55 54.17", dec="32 37 45.85")
    answer = make_planning(target).lookup_coordinates("M 57")
    assert answer.kind == "target"
    assert abs(answer.ra_deg - 283.9757) < 0.001
    assert not hasattr(answer, "frames")


def test_a_star_is_looked_up_with_its_magnitude() -> None:
    """A star answers with its position in degrees and its type."""
    star = StellarObject(id="* alf Lyr", name="Vega", right_ascension=279.2, declination=38.8, magnitude=0.03)
    answer = make_planning(star).lookup_coordinates("Vega")
    assert answer.kind == "star"
    assert answer.dec_deg == pytest.approx(38.8)


def test_an_unknown_name_raises_the_lookup_error() -> None:
    """A failed lookup raises; it is not turned into an error reply."""
    with pytest.raises(ValueError, match="cannot resolve"):
        make_planning().lookup_coordinates("Nowhere")
