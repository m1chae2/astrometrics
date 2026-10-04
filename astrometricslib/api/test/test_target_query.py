"""Tests for the short-record target lookup.

`TargetCatalog.query` replaces tools that returned whole target objects.
These tests use a small fake store and check the rows, the filters, the
grouped single-target record and that looking never marks a target as
changed.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.api.targets import TargetCatalog
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
    return TargetCatalog(config=None, catalog_access=access)


def test_summary_rows_leave_out_placeholders_and_sort_newest_first(catalog: TargetCatalog) -> None:
    """The empty target is dropped and the newest target leads."""
    answer = catalog.query()
    assert [row["id"] for row in answer["targets"]] == ["M 13", "M 57"]
    assert answer["total_matching"] == 2
    assert answer["targets"][1]["light_frames"] == 3
    assert "frames" not in answer["targets"][1]


def test_placeholders_can_be_asked_for(catalog: TargetCatalog) -> None:
    """include_empty brings the empty target back."""
    ids = [row["id"] for row in catalog.query(include_empty=True, sort="name")["targets"]]
    assert ids == ["Bias", "M 13", "M 57"]


def test_filters_by_text_camera_and_region(catalog: TargetCatalog) -> None:
    """Each filter narrows the list; the region measures the angle."""
    assert [row["id"] for row in catalog.query(text="57")["targets"]] == ["M 57"]
    assert [row["id"] for row in catalog.query(camera="nikon")["targets"]] == ["M 13"]
    near = catalog.query(ra=283.9, dec=32.6, radius_deg=2.0)["targets"]
    assert [row["id"] for row in near] == ["M 57"]
    assert near[0]["separation_deg"] < 0.2


def test_the_full_record_groups_frames_by_night(catalog: TargetCatalog) -> None:
    """Three frames over two nights become two groups, with the file range."""
    record = catalog.query(target_id="m 57", detail="full", include_frames=2)["target"]
    assert [(group["frames"], group["first_file"]) for group in record["frame_groups"]] == [
        (2, "a1.fits"),
        (1, "a3.fits"),
    ]
    assert len(record["newest_frames"]) == 2
    assert record["newest_frames"][-1]["file"] == "a3.fits"


def test_bad_requests_are_errors(catalog: TargetCatalog) -> None:
    """A full record needs a target, and an unknown one is not found."""
    assert "needs a target_id" in catalog.query(detail="full")["error"]
    assert "No target matches" in catalog.query(target_id="Nope", detail="full")["error"]
    assert "detail must be" in catalog.query(detail="everything")["error"]
    assert "ra, dec and radius_deg" in catalog.query(ra=1.0)["error"]


def test_looking_does_not_mark_targets_as_touched(catalog: TargetCatalog) -> None:
    """A later save has nothing to write because of a query."""
    catalog.query()
    catalog.query(target_id="M 57", detail="full")
    assert not getattr(catalog, "_touched_target_ids", set())
