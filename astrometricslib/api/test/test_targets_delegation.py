"""Purpose: Delegation-contract tests for TargetCatalog and TargetFrames.

Description: Each public method of `TargetCatalog` and `TargetFrames` is a
thin wrapper of one owner function in `pipelines.shared` (or
`pipelines.stacking`). The owners have their own tests, so these tests check
the wiring (each method calls its owner with the right arguments and returns
the result unchanged) and the few behaviors of the owners that are simplest
to show through the public objects: adding, updating and deleting targets,
the frame header read, and a frame delete clearing the stored measurements.
"""

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS
from astrometricslib.api.targets import TargetCatalog
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.catalog_queries import TargetCameraCounts
from astrometricslib.models.page import Page
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared import target_overview, target_records
from astrometricslib.pipelines.shared.quality import frame_statistics
from astrometricslib.pipelines.stacking import frame_queries


def _make_catalog() -> TargetCatalog:
    """Build a catalog over a mock config and an empty mock storage.

    Returns
    -------
    catalog : `TargetCatalog`
        A catalog holding no targets.
    """
    config = MagicMock()
    catalog_access = MagicMock()
    catalog_access.get.return_value = []
    del catalog_access.merge_and_record
    return TargetCatalog(config, catalog_access)


def test_query_returns_every_target_as_a_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """``query`` gives the stored targets, in stored order, as a page."""
    catalog = _make_catalog()
    targets = [Target(id="M1"), Target(id="M2"), Target(id="M3")]
    monkeypatch.setattr(target_records, "list_targets", MagicMock(return_value=targets))

    every = catalog.query(include_empty=True)
    page = catalog.query(include_empty=True, limit=1, offset=1)

    assert list(every) == targets and every[0] is targets[0]
    assert (every.total, every.next_offset) == (3, None)
    assert list(page) == [targets[1]] and page.next_offset == 2


def test_query_leaves_empty_targets_out_unless_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """A target with no frames is listed only with ``include_empty``."""
    catalog = _make_catalog()
    full = Target(id="M1", frames=[FrameRecord(path="/a.fits")])
    monkeypatch.setattr(target_records, "list_targets", MagicMock(return_value=[full, Target(id="M2")]))

    assert [target.id for target in catalog.query()] == ["M1"]


def test_query_refuses_a_bad_limit_or_choice() -> None:
    """A ``limit`` or ``offset`` out of range, or an unknown order, raises."""
    catalog = _make_catalog()
    for bad in ({"limit": 0}, {"offset": -1}, {"order_by": "size"}):
        with pytest.raises(InvalidArgumentError):
            catalog.query(**bad)
    with pytest.raises(InvalidArgumentError, match="region"):
        catalog.query(ra_deg=10.0)


def test_query_does_not_mark_the_targets_as_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Listing is not editing: no target is touched by a query."""
    catalog = _make_catalog()

    def listing(api: TargetCatalog) -> list[Target]:
        """Touch every target, as the real listing does.

        Returns
        -------
        targets : `list` [`Target`]
            One target.
        """
        api._touched_target_ids.add("M1")
        return [Target(id="M1")]

    monkeypatch.setattr(target_records, "list_targets", listing)
    catalog.query(include_empty=True)

    assert catalog._touched_target_ids == set()


def test_get_reads_storage_and_forwards_the_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """``get`` refreshes the one target, then finds it."""
    catalog = _make_catalog()
    target = Target(id="M13")
    refresh = MagicMock()
    monkeypatch.setattr(target_records, "refresh_target", refresh)
    monkeypatch.setattr(target_records, "get_target", MagicMock(return_value=target))

    assert catalog.get("M13") is target
    refresh.assert_called_once_with(catalog, "M13")


def test_get_required_raises_for_an_unknown_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """``required=True`` makes a miss raise; an empty id is refused."""
    catalog = _make_catalog()
    monkeypatch.setattr(target_records, "refresh_target", MagicMock())
    monkeypatch.setattr(target_records, "get_target", MagicMock(return_value=None))

    assert catalog.get("M99") is None
    with pytest.raises(NotFoundError):
        catalog.get("M99", required=True)
    with pytest.raises(InvalidArgumentError):
        catalog.get("  ")


def test_create_delegates_to_target_records(monkeypatch: pytest.MonkeyPatch) -> None:
    """``create`` without a catalog lookup makes a bare target."""
    catalog = _make_catalog()
    target = Target(id="M13")
    mock = MagicMock(return_value=target)
    monkeypatch.setattr(target_records, "create_target", mock)

    assert catalog.create("M13") is target
    mock.assert_called_once_with(catalog, "M13")


def test_add_appends_a_new_target_and_saves(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new target is appended, tracked and saved."""
    catalog = _make_catalog()
    save = MagicMock()
    monkeypatch.setattr(target_records, "save_targets", save)
    target = Target(id="M13")

    assert catalog.add(target) is target
    assert catalog._targets == [target]
    assert "M13" in catalog._touched_target_ids
    save.assert_called_once_with(catalog)


def test_add_keeps_the_held_target_of_the_same_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adding a target the catalog holds does not duplicate it."""
    catalog = _make_catalog()
    monkeypatch.setattr(target_records, "save_targets", MagicMock())
    existing = Target(id="M13")
    catalog._targets.append(existing)

    assert catalog.add(Target(id="M13")) is existing
    assert catalog._targets == [existing]


def test_update_changes_fields_saves_and_returns_the_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """``update`` sets the fields, trusts a typed position and saves."""
    catalog = _make_catalog()
    save = MagicMock()
    monkeypatch.setattr(target_records, "save_targets", save)
    target = Target(id="M13", position_unverified=True)
    catalog._targets.append(target)

    changed = catalog.update(target, common_name="Hercules Cluster", ra="16h 41m 41s")

    assert changed is target
    assert (target.common_name, target.ra, target.position_unverified) == (
        "Hercules Cluster",
        "16h 41m 41s",
        False,
    )
    save.assert_called_once_with(catalog)


def test_update_sets_the_processed_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """``processed_image=`` is the target's finished picture."""
    catalog = _make_catalog()
    monkeypatch.setattr(target_records, "save_targets", MagicMock())
    target = Target(id="M13")
    catalog._targets.append(target)

    catalog.update(target, processed_image="/m13.png")

    assert target.stacking.processed_image == "/m13.png"


def test_update_refuses_an_unknown_field_or_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """A name that is not a field, or an unknown target, is refused."""
    catalog = _make_catalog()
    monkeypatch.setattr(target_records, "save_targets", MagicMock())
    target = Target(id="M13")
    catalog._targets.append(target)

    with pytest.raises(InvalidArgumentError, match="no field"):
        catalog.update(target, colour="red")
    with pytest.raises(NotFoundError):
        catalog.update(Target(id="M99"), common_name="x")


def test_delete_removes_the_target_and_refuses_an_unknown_one() -> None:
    """``delete`` drops the stored target; an unknown one raises."""
    catalog = _make_catalog()
    target = Target(id="M13")
    catalog._targets.append(target)
    stored = [target]
    catalog._catalog_access.get.side_effect = lambda *_args: list(stored)
    catalog._catalog_access.put.side_effect = lambda targets, *_args: stored.__setitem__(slice(None), targets)

    catalog.delete(target)

    assert stored == [] and catalog._targets == []
    with pytest.raises(NotFoundError):
        catalog.delete(Target(id="M99"))


def test_count_by_camera_delegates_with_the_full_target_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """``count_by_camera`` counts this catalog's own targets."""
    catalog = _make_catalog()
    catalog._config.get_available_cameras.return_value = []
    targets = [Target(id="M13")]
    monkeypatch.setattr(target_records, "list_targets", lambda _catalog: targets)
    mock = MagicMock(return_value={"ASI294": 3})
    monkeypatch.setattr(frame_statistics, "list_camera_names", mock)

    result = catalog.count_by_camera()

    assert isinstance(result, TargetCameraCounts)
    assert result.frames_by_camera == {"ASI294": 3}
    mock.assert_called_once_with(targets)


def test_count_by_night_counts_targets_per_night(monkeypatch: pytest.MonkeyPatch) -> None:
    """``count_by_night`` counts each target once per night."""
    catalog = _make_catalog()
    monkeypatch.setattr(target_records, "list_targets", lambda _catalog: [Target(id="M13")])
    mock = MagicMock(return_value={"2026-10-03": 1})
    monkeypatch.setattr(target_overview, "count_targets_per_night", mock)

    assert catalog.count_by_night().targets_by_night == {"2026-10-03": 1}


def test_frames_refuse_an_id_in_place_of_a_target() -> None:
    """Frame methods take the `Target`, not its id."""
    with pytest.raises(InvalidArgumentError, match=r"targets\.get"):
        _make_catalog().frames.query("M13")  # type: ignore[arg-type]


def test_frame_query_delegates_to_its_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """``frames.query`` forwards the target, settings and filters."""
    catalog = _make_catalog()
    target = Target(id="M13")
    answer = Page.of([])
    mock = MagicMock(return_value=answer)
    monkeypatch.setattr(frame_queries, "query_frames", mock)

    assert catalog.frames.query(target, night="2026-10-03", gain=100) is answer
    assert mock.call_args.args == (target, catalog._config)
    assert mock.call_args.kwargs["night"] == "2026-10-03" and mock.call_args.kwargs["gain"] == 100


def test_frame_query_filters_by_gain_exposure_and_order() -> None:
    """The query owner keeps the light frames that match every filter."""
    stamp = 1_791_000_000.0
    frames = [
        FrameRecord(path="/a_001.fits", iso="100", exposure="300.0", timestamp=stamp),
        FrameRecord(path="/a_002.fits", iso="200", exposure="300.0", timestamp=stamp + 60),
        FrameRecord(path="/a_003.fits", iso="100", exposure="60", timestamp=stamp + 120),
        FrameRecord(path="/a_004.fits", iso="100", exposure="300", timestamp=None),
    ]
    target = Target(id="M13", frames=frames)

    chosen = frame_queries.query_frames(target, MagicMock(), gain=100, exposure_s=300)
    newest = frame_queries.query_frames(target, MagicMock(), order_by="newest", limit=1)

    assert [frame.path for frame in chosen] == ["/a_001.fits"]
    assert [frame.path for frame in newest] == ["/a_003.fits"] and newest.total == 3


def test_get_newest_gives_the_last_timed_light_frame() -> None:
    """``get_newest`` picks the latest start time, ignoring untimed frames."""
    target = Target(
        id="M13",
        frames=[
            FrameRecord(path="/a.fits", timestamp=10.0),
            FrameRecord(path="/b.fits", timestamp=20.0),
            FrameRecord(path="/c.fits", timestamp=None),
        ],
    )

    assert _make_catalog().frames.get_newest(target).path == "/b.fits"
    assert _make_catalog().frames.get_newest(Target(id="M1")) is None


def test_get_rotation_refuses_a_file_of_another_target() -> None:
    """``get_rotation`` reads only the target's own frames and stacks."""
    with pytest.raises(InvalidArgumentError, match="does not belong to target"):
        _make_catalog().frames.get_rotation(Target(id="M13"), "/unrelated.fits")


def test_get_rotation_is_zero_without_a_stack() -> None:
    """A target with no stack shows its frames unturned."""
    target = Target(id="M13", frames=[FrameRecord(path="/a.fits")])

    assert _make_catalog().frames.get_rotation(target, target.frames[0]) == 0


def test_get_nights_groups_frames_by_night() -> None:
    """``get_nights`` gives one session per night and setting."""
    frames = [FrameRecord(path=f"/f{index}.fits", timestamp=1_791_000_000.0 + index) for index in range(3)]
    target = Target(id="M13", frames=frames)

    nights = _make_catalog().frames.get_nights(target)

    assert len(nights) == 1 and len(nights[0].frame_paths) == 3
    assert len(_make_catalog().frames.get_nights(target, night="1999-01-01")) == 0


def test_delete_removes_files_records_and_measurements(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``frames.delete`` deletes the file, its record and its stored rows."""
    catalog = _make_catalog()
    monkeypatch.setattr(target_records, "save_targets", MagicMock())
    path = tmp_path / "lights" / "M13" / "scope" / "camera" / "a.fits"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    target = Target(id="M13", frames=[FrameRecord(path=str(path)), FrameRecord(path="/other.fits")])
    catalog._targets.append(target)
    store = MagicMock()
    monkeypatch.setattr(
        "astrometricslib._internal.drivers.quality_store.QualityStore", MagicMock(return_value=store)
    )
    monkeypatch.setattr(
        "astrometricslib._internal.drivers.quality_store.quality_database_path", MagicMock(return_value="db")
    )

    result = catalog.frames.delete(None, [str(path)])

    assert result.deleted == [str(path)] and not path.exists()
    assert [frame.path for frame in target.frames] == ["/other.fits"]
    store.delete_frame_measurements.assert_called_once_with(["M13/scope/camera/a.fits"])


def test_add_refuses_a_picture_and_a_single_string() -> None:
    """A picture goes through ``targets.update``; one string is refused."""
    catalog = _make_catalog()
    with pytest.raises(InvalidArgumentError, match="processed_image"):
        catalog.frames.add(Target(id="M13"), ["/m13.png"])
    with pytest.raises(InvalidArgumentError, match="list"):
        catalog.frames.add(Target(id="M13"), "/a.fits")


def test_get_header_returns_typed_values_from_the_first_header(tmp_path: Path) -> None:
    """The first header comes back with its values typed."""
    path = str(tmp_path / "frame.fits")
    hdu = fits.PrimaryHDU(np.zeros((4, 4), dtype=np.uint16))
    hdu.header["FILTER"] = "SPEC"
    hdu.header["CRVAL1"] = 150.5
    hdu.writeto(path)

    header = _make_catalog().frames.get_header(path)

    assert header["FILTER"] == "SPEC"
    assert header["CRVAL1"] == pytest.approx(150.5)


def test_get_header_raises_not_found_for_a_missing_file(tmp_path: Path) -> None:
    """A missing file raises `NotFoundError`."""
    with pytest.raises(NotFoundError):
        _make_catalog().frames.get_header(str(tmp_path / "missing.fits"))


def test_get_header_raises_a_fits_read_error_for_a_damaged_file(tmp_path: Path) -> None:
    """A file that is not FITS raises a FITS_READ_ERRORS error."""
    path = tmp_path / "broken.fits"
    path.write_bytes(b"not a fits file")
    with pytest.raises(FITS_READ_ERRORS):
        _make_catalog().frames.get_header(str(path))


def test_frame_query_filters_by_stack_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """``status=`` keeps the frames in that state of the newest stack."""
    from astrometricslib.models.quality.frame_status import FrameStatus, FrameStatusReport
    from astrometricslib.pipelines.stacking import frame_status

    frames = [FrameRecord(path=f"/f{index}.fits", timestamp=100.0 + index) for index in range(3)]
    target = Target(id="M13", frames=frames)
    report = FrameStatusReport(
        frames=[
            FrameStatus(path="/f0.fits", name="f0.fits", state="included"),
            FrameStatus(path="/f1.fits", name="f1.fits", state="flagged"),
            FrameStatus(path="/f2.fits", name="f2.fits", state="not_stacked"),
        ]
    )
    monkeypatch.setattr(frame_status, "report_frame_statuses", lambda _target, _config: report)

    flagged = frame_queries.query_frames(target, MagicMock(), status="flagged")

    assert [frame.path for frame in flagged] == ["/f1.fits"]
    with pytest.raises(InvalidArgumentError):
        frame_queries.query_frames(target, MagicMock(), status="lost")
