"""Tests for treating a star cluster inside a nebula as a nebula.

SIMBAD is not asked: the list of objects in the field is a stand-in.
"""

from collections.abc import Callable

import pytest

from astrometricslib.models.processing_results import BrightObjectChoice, BrightObjectChoices
from astrometricslib.pipelines.stacking.post_processing import cluster_nebula
from astrometricslib.pipelines.stacking.post_processing.cluster_nebula import (
    angular_separation_arcmin,
    find_nebula_around_position,
    refine_object_category,
)
from astrometricslib.pipelines.stacking.post_processing.masked_stretch import TargetAnchor

CLUSTER_RA, CLUSTER_DEC = 98.0, 4.7


def nebula(
    name: str, ra_offset_arcmin: float, major_axis_arcmin: float | None, category: str = "nebula"
) -> BrightObjectChoice:
    """Make one listed object at an offset in right ascension from the cluster.

    Returns
    -------
    choice : `BrightObjectChoice`
        The object.
    """
    return BrightObjectChoice(
        simbad_id=name,
        object_type="HII" if category == "nebula" else "G",
        category=category,
        ra_deg=CLUSTER_RA + ra_offset_arcmin / 60.0,
        dec_deg=CLUSTER_DEC,
        major_axis_arcmin=major_axis_arcmin,
    )


@pytest.fixture
def listed(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[str]]:
    """Replace the SIMBAD object list with one the test sets.

    Returns
    -------
    set_objects : `Callable`
        Call it with the objects (and an optional message) the list returns.
    """
    calls: list[str] = []

    def install(objects: list[BrightObjectChoice], message: str | None = None) -> list[str]:
        """Make the list return these objects.

        Returns
        -------
        calls : `list` [`str`]
            The target ids the list was asked about, to show it was asked.
        """

        def fake(
            target_id: str, stacked_path: str, simbad: object, *, use_cache: bool = True
        ) -> BrightObjectChoices:
            """Stand in for `list_bright_object_choices`.

            Returns
            -------
            choices : `BrightObjectChoices`
                The set objects.
            """
            calls.append(stacked_path)
            return BrightObjectChoices(target_id=target_id, objects=objects, message=message)

        monkeypatch.setattr(cluster_nebula, "list_bright_object_choices", fake)
        return calls

    return install


ANCHOR = TargetAnchor(CLUSTER_RA, CLUSTER_DEC)


def test_the_separation_of_two_positions_is_in_arcminutes() -> None:
    """One degree apart in declination is 60 arcminutes."""
    assert angular_separation_arcmin(10.0, 20.0, 10.0, 21.0) == pytest.approx(60.0)
    assert angular_separation_arcmin(10.0, 0.0, 10.5, 0.0) == pytest.approx(30.0)


def test_a_cluster_inside_a_nebula_is_a_nebula(listed: Callable[..., list[str]]) -> None:
    """A nebula disc that holds the cluster makes it a nebula."""
    listed([nebula("Rosette Nebula", 30.0, 80.0)])

    verdict = refine_object_category("cluster", ANCHOR, "stack.fits")

    assert verdict.category == "nebula"
    assert verdict.catalog_category == "cluster"
    assert verdict.nebula == "Rosette Nebula"
    assert verdict.record()["nebula"] == "Rosette Nebula"


def test_a_cluster_with_no_nebula_stays_a_cluster(listed: Callable[..., list[str]]) -> None:
    """A field with only a galaxy leaves a globular cluster a cluster."""
    listed([nebula("NGC 6207", 40.0, 3.0, category="galaxy")])

    verdict = refine_object_category("cluster", ANCHOR, "stack.fits")

    assert verdict.category == "cluster"
    assert verdict.nebula is None


def test_a_nebula_beside_the_cluster_does_not_count(listed: Callable[..., list[str]]) -> None:
    """A nebula in the frame but not around the cluster is ignored."""
    listed([nebula("Bubble Nebula", 37.0, 9.0)])

    assert refine_object_category("cluster", ANCHOR, "stack.fits").category == "cluster"


def test_the_nebula_edge_decides(listed: Callable[..., list[str]]) -> None:
    """A position just inside the disc counts, just outside does not."""
    objects = [nebula("Edge", 19.0, 40.0)]
    assert find_nebula_around_position(CLUSTER_RA, CLUSTER_DEC, objects) is not None
    assert find_nebula_around_position(CLUSTER_RA, CLUSTER_DEC, [nebula("Edge", 21.0, 40.0)]) is None


def test_a_nebula_with_no_size_has_no_disc() -> None:
    """Without a major axis, the nebula cannot contain anything."""
    assert find_nebula_around_position(CLUSTER_RA, CLUSTER_DEC, [nebula("Unsized", 0.0, None)]) is None


def test_the_nearest_of_several_nebulae_is_named() -> None:
    """When two discs hold the position, the one centered nearer wins."""
    objects = [nebula("Far", 20.0, 80.0), nebula("Near", 5.0, 30.0)]

    assert find_nebula_around_position(CLUSTER_RA, CLUSTER_DEC, objects).simbad_id == "Near"


@pytest.mark.parametrize("category", ["nebula", "galaxy", "star", None])
def test_a_target_that_is_not_a_cluster_is_never_changed(
    listed: Callable[..., list[str]], category: str | None
) -> None:
    """Only a cluster is looked at, and SIMBAD is not asked for the others."""
    calls = listed([nebula("Rosette Nebula", 0.0, 80.0)])

    verdict = refine_object_category(category, ANCHOR, "stack.fits")

    assert verdict.category == category
    assert calls == []


@pytest.mark.parametrize(
    "anchor",
    [None, TargetAnchor(mode="off"), TargetAnchor(mode="object", ra_deg=1.0, dec_deg=1.0), TargetAnchor()],
    ids=["no anchor", "turned off", "chosen object", "no position"],
)
def test_a_cluster_without_a_usable_anchor_stays_a_cluster(
    listed: Callable[..., list[str]], anchor: TargetAnchor | None
) -> None:
    """No catalog position, or a user's choice, leaves the kind alone."""
    calls = listed([nebula("Rosette Nebula", 0.0, 80.0)])

    assert refine_object_category("cluster", anchor, "stack.fits").category == "cluster"
    assert calls == []


def test_an_empty_list_keeps_the_cluster_and_says_why(listed: Callable[..., list[str]]) -> None:
    """No plate solution or no SIMBAD answer is a status, with its message."""
    listed([], message="Run Astrometry first to list objects in the frame")

    verdict = refine_object_category("cluster", ANCHOR, "stack.fits")

    assert verdict.category == "cluster"
    assert "Run Astrometry first" in verdict.reason
