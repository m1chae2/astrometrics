"""Purpose: Tests for listing and counting targets.

Description: `TargetCatalog.query` returns a page of targets that pass every
filter, and `count_by_night` counts targets per observing night. These tests
use a small fake store and check the filters, the orders, the night counts and
that looking never marks a target as changed.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.api.targets import TargetCatalog
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.target import FrameRecord, Target

NIGHT_ONE = 1_790_000_000.0
NIGHT_TWO = NIGHT_ONE + 86_400.0


def make_frame(name: str, taken: float, filter_name: str = "Luminance", camera: str = "ZWO") -> FrameRecord:
    """Make a 60 second light frame.

    Returns
    -------
    frame : `FrameRecord`
        The frame.
    """
    return FrameRecord(
        path=f"/lights/{name}",
        role="LIGHT",
        filter=filter_name,
        exposure="60",
        timestamp=taken,
        camera=camera,
    )


@pytest.fixture
def catalog() -> TargetCatalog:
    """Make a catalog of two real targets and an empty placeholder.

    Returns
    -------
    catalog : `TargetCatalog`
        With M 57 (three frames over two nights), M 13 (one frame, another
        camera) and an empty "Bias" placeholder.
    """
    targets = [
        Target(
            id="M 57",
            ra="18 55 54.17",
            dec="32 37 45.85",
            frames=[
                make_frame("a1.fits", NIGHT_ONE),
                make_frame("a2.fits", NIGHT_ONE + 60),
                make_frame("a3.fits", NIGHT_TWO),
            ],
        ),
        Target(
            id="M 13",
            ra="16 41 41.2",
            dec="36 27 37",
            frames=[make_frame("b1.fits", NIGHT_TWO + 600, camera="Nikon")],
        ),
        Target(id="Bias"),
    ]
    access = SimpleNamespace(get=lambda name, default=None: list(targets))
    return TargetCatalog(None, access)


def test_the_list_leaves_out_placeholders_and_can_sort_newest_first(catalog: TargetCatalog) -> None:
    """The empty target is dropped and the newest target leads."""
    answer = catalog.query(order_by="newest")
    assert [target.id for target in answer] == ["M 13", "M 57"]
    assert answer.total == 2
    assert len(answer[1].frames) == 3


def test_placeholders_can_be_asked_for(catalog: TargetCatalog) -> None:
    """include_empty brings the empty target back."""
    ids = [target.id for target in catalog.query(include_empty=True, order_by="name")]
    assert ids == ["Bias", "M 13", "M 57"]


def test_filters_by_text_camera_and_region(catalog: TargetCatalog) -> None:
    """Each filter narrows the list; the region measures the angle."""
    assert [target.id for target in catalog.query(text="57")] == ["M 57"]
    assert [target.id for target in catalog.query(camera="nikon")] == ["M 13"]
    near = catalog.query(ra_deg=283.9, dec_deg=32.6, radius_deg=2.0)
    assert [target.id for target in near] == ["M 57"]


def test_filters_by_object_type(catalog: TargetCatalog) -> None:
    """object_type keeps only that kind; an unknown kind is refused."""
    assert [target.object_type.value for target in catalog.query()] == ["messier", "messier"]
    assert catalog.query(object_type="ngc").total == 0
    answer = catalog.query(object_type="calibration", include_empty=True)
    assert [target.id for target in answer] == ["Bias"]
    with pytest.raises(InvalidArgumentError):
        catalog.query(object_type="galaxy")


def test_a_region_search_can_put_the_nearest_target_first(catalog: TargetCatalog) -> None:
    """Ordering by separation lists the target nearest the centre first."""
    near = catalog.query(ra_deg=283.9, dec_deg=32.6, radius_deg=180.0, order_by="separation")
    assert near[0].id == "M 57"


def test_ordering_by_separation_needs_a_region(catalog: TargetCatalog) -> None:
    """Ordering by separation without a region says what is missing."""
    with pytest.raises(InvalidArgumentError, match="region search"):
        catalog.query(order_by="separation")
    with pytest.raises(InvalidArgumentError, match="ra_deg, dec_deg and radius_deg"):
        catalog.query(ra_deg=1.0)


def test_the_night_counts_count_targets_per_observing_night(catalog: TargetCatalog) -> None:
    """Each observing night lists how many targets have frames from it."""
    nights = catalog.count_by_night().targets_by_night
    assert nights
    assert all(count >= 1 for count in nights.values())
    assert list(nights) == sorted(nights)


def test_looking_does_not_mark_targets_as_touched(catalog: TargetCatalog) -> None:
    """A later save has nothing to write because of a query or a count."""
    catalog.query()
    catalog.count_by_night()
    assert not getattr(catalog, "_touched_target_ids", set())
