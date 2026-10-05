"""Tests for the capped source list and the short coordinate lookup.

The older planning methods return whole target records and every star in a
circle, which overflows an MCP reply. These tests use a fake sky engine and
check that the new methods sort, cut and report the total.
"""

from types import SimpleNamespace

import pytest

from astrometricslib import StellarObject, Target
from wayfindinglib.api.planning import ObservationPlanning


def make_planning(
    stars: list[dict], targets: list[Target], resolved: object | None = None
) -> ObservationPlanning:
    """Build a planning object over a fake sky engine.

    Returns
    -------
    planning : `ObservationPlanning`
        An object whose engine answers from the lists given.
    """

    def resolve(name: str) -> object:
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

    engine = SimpleNamespace(
        get_library_star_summaries=lambda ra, dec, radius, magnitude_range: stars,
        get_sources=lambda ra, dec, radius, catalog, include_stars: targets,
        resolve_target_coordinates=resolve,
    )
    planning = ObservationPlanning.__new__(ObservationPlanning)
    planning._ObservationPlanning__sky_engine = engine  # the lazily built engine
    return planning


def test_stars_come_brightest_first_and_are_cut_with_the_total_reported() -> None:
    """Five stars, limit two: the two brightest, and the cut is stated."""
    stars = [{"id": f"S{n}", "magnitude": m} for n, m in enumerate([9.0, None, 3.0, 5.0, 1.0])]
    answer = make_planning(stars, [Target(id="M 57", ra="18 55 54", dec="32 37 45")]).find_sources(
        284.0, 32.6, 1.0, limit=2
    )
    assert [row["id"] for row in answer["stars"]] == ["S4", "S2"]
    assert answer["stars_total"] == 5
    assert answer["stars_truncated"] is True
    assert answer["targets"][0]["id"] == "M 57"


def test_a_target_is_looked_up_as_a_short_position() -> None:
    """The reply is a position, not the target record."""
    target = Target(id="M 57", ra="18 55 54.17", dec="32 37 45.85")
    answer = make_planning([], [], resolved=target).lookup_coordinates("M 57")
    assert answer["kind"] == "target"
    assert abs(answer["ra_deg"] - 283.9757) < 0.001
    assert "frames" not in answer


def test_a_star_is_looked_up_with_its_magnitude() -> None:
    """A star answers with its position in degrees and its type."""
    star = StellarObject(id="* alf Lyr", name="Vega", right_ascension=279.2, declination=38.8, magnitude=0.03)
    answer = make_planning([], [], resolved=star).lookup_coordinates("Vega")
    assert answer["kind"] == "star"
    assert answer["dec_deg"] == pytest.approx(38.8)


def test_an_unknown_name_is_an_error_not_a_crash() -> None:
    """A failed lookup comes back under `error`."""
    assert "cannot resolve" in make_planning([], []).lookup_coordinates("Nowhere")["error"]
