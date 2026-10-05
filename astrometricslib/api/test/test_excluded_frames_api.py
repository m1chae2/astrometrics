"""Tests for the public calls that preview, list and restore excluded frames.

They use real files in a temporary library and a stub configuration, with
the frame measurement replaced so no star finding runs.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from astrometricslib.api.processing import ProcessingPipelines
from astrometricslib.foundation.enums import FilterType
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.quarantine_path import QUARANTINE_FOLDER_NAME
from astrometricslib.pipelines.stacking.pre_processing import frame_quarantine
from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import MANIFEST_FILE_NAME

FINDER = "astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.find_frames_to_quarantine"


@pytest.fixture
def pipelines(tmp_path: Path) -> ProcessingPipelines:
    """Make pipelines whose frames folder is under a temporary folder.

    Returns
    -------
    pipelines : `ProcessingPipelines`
        Pipelines on a stub configuration.
    """
    configuration = SimpleNamespace(get_frames_path=lambda: tmp_path / "frames")
    return ProcessingPipelines(configuration, None, targets=MagicMock())


def camera_folder(tmp_path: Path) -> Path:
    """Give the folder a target's lights are stored in.

    Returns
    -------
    folder : `pathlib.Path`
        The folder, created.
    """
    folder = tmp_path / "frames" / "lights" / "M 1" / "Scope" / "Camera"
    folder.mkdir(parents=True)
    return folder


def make_target(folder: Path, count: int, role: str = "LIGHT") -> Target:
    """Make a target with frame records whose files exist.

    Returns
    -------
    target : `Target`
        A target with `count` luminance frames, 120 s apart.
    """
    frames = []
    for index in range(count):
        path = folder / f"frame_{index:03d}.fits"
        path.write_bytes(b"x")
        frames.append(
            FrameRecord(
                path=str(path),
                filter=FilterType.L,
                camera="ZWO ASI 533MM Pro",
                exposure="120.0",
                timestamp=float(index) * 120.0,
                role=role,
            )
        )
    return Target(id="M 1", frames=frames)


def fake_measurement(star_counts: dict[str, int]) -> object:
    """Build a stand-in for the frame measurement.

    Returns
    -------
    measure : `Callable`
        Gives each file the star count in `star_counts`, or 3000.
    """

    def measure(path: str) -> dict[str, object]:
        """Measure one file with a made-up result.

        A frame with few stars gets a bright sky, as cloud would give.

        Returns
        -------
        measurement : `dict`
            The made-up star count, roundness and sky level.
        """
        stars = star_counts.get(Path(path).name, 3000)
        return {"star_count": stars, "roundness": 0.9, "sky_median_adu": 7000.0 if stars < 1000 else 5000.0}

    return measure


def with_measurement(star_counts: dict[str, int]) -> object:
    """Build a stand-in for the finder that measures with made-up results.

    The finder binds its default measurement when the module loads, so a
    test cannot swap that default. This hands the finder the made-up one.

    Returns
    -------
    finder : `Callable`
        Runs the real finder on the frames with `fake_measurement`.
    """
    real_finder = frame_quarantine.find_frames_to_quarantine

    def finder(frames: list[FrameRecord]) -> object:
        """Find the frames to move, using the made-up measurements.

        Returns
        -------
        report : `QuarantineReport`
            What the real finder decided.
        """
        return real_finder(frames, fake_measurement(star_counts))

    return finder


def write_manifest(folder: Path, entries: list[dict[str, object]]) -> Path:
    """Write an `_excluded` folder with a manifest and the moved files.

    Returns
    -------
    quarantine : `pathlib.Path`
        The `_excluded` folder.
    """
    quarantine = folder / QUARANTINE_FOLDER_NAME
    quarantine.mkdir()
    for entry in entries:
        (quarantine / str(entry["file"])).write_bytes(b"x")
    (quarantine / MANIFEST_FILE_NAME).write_text(json.dumps(entries))
    return quarantine


def manifest_entry(folder: Path, name: str) -> dict[str, object]:
    """Make one manifest entry for a frame moved out of `folder`.

    Returns
    -------
    entry : `dict`
        The entry, as the quarantine step writes it.
    """
    return {
        "file": name,
        "original_path": str(folder / name),
        "kind": "clouded",
        "reason": "203 stars against a typical 3348",
        "star_count": 203,
        "typical_star_count": 3348.0,
        "roundness": 0.88,
        "sky_median_adu": 5000.0,
        "moved_at": "2026-10-03T08:00:00+00:00",
    }


def test_preview_names_the_frames_it_would_move_and_moves_none(
    pipelines: ProcessingPipelines, tmp_path: Path
) -> None:
    """A cloudy frame is reported, and its file stays where it is."""
    folder = camera_folder(tmp_path)
    target = make_target(folder, 12)
    cloudy = folder / "frame_011.fits"

    with patch(FINDER, side_effect=with_measurement({"frame_011.fits": 100})):
        preview = pipelines.diagnostics.frame_quality(target, kind="quarantine_preview", register_job=False)

    assert preview.target_id == "M 1"
    assert preview.frames_checked == 12
    assert [frame.file for frame in preview.would_move] == ["frame_011.fits"]
    assert preview.would_move[0].kind == "clouded"
    assert preview.would_move[0].moved_at is None
    assert cloudy.exists()
    assert len(target.frames) == 12


def test_preview_skips_frames_that_are_not_lights(pipelines: ProcessingPipelines, tmp_path: Path) -> None:
    """Calibration frames are not measured."""
    target = make_target(camera_folder(tmp_path), 12, role="FLAT")

    with patch(FINDER, side_effect=with_measurement({})):
        preview = pipelines.diagnostics.frame_quality(target, kind="quarantine_preview", register_job=False)

    assert preview.frames_checked == 0
    assert preview.would_move == []


def test_list_reads_the_manifests(pipelines: ProcessingPipelines, tmp_path: Path) -> None:
    """Listed frames carry the manifest's measurements and their folder."""
    folder = camera_folder(tmp_path)
    quarantine = write_manifest(folder, [manifest_entry(folder, "frame_003.fits")])

    frames = pipelines.restore_excluded_frames(Target(id="M 1")).frames

    assert [frame.file for frame in frames] == ["frame_003.fits"]
    assert frames[0].star_count == 203
    assert frames[0].moved_at == "2026-10-03T08:00:00+00:00"
    assert frames[0].folder == str(quarantine)


def test_list_is_empty_for_a_target_with_nothing_set_aside(
    pipelines: ProcessingPipelines, tmp_path: Path
) -> None:
    """A target with no `_excluded` folder lists nothing."""
    camera_folder(tmp_path)

    assert pipelines.restore_excluded_frames(Target(id="M 1")).frames == []


def test_a_stray_excluded_folder_without_a_manifest_is_not_listed(
    pipelines: ProcessingPipelines, tmp_path: Path
) -> None:
    """Only `_excluded` folders that hold a manifest count."""
    (camera_folder(tmp_path) / QUARANTINE_FOLDER_NAME).mkdir()

    assert pipelines.restore_excluded_frames(Target(id="M 1")).frames == []


def test_restore_without_apply_only_lists(pipelines: ProcessingPipelines, tmp_path: Path) -> None:
    """The default restore moves nothing."""
    folder = camera_folder(tmp_path)
    quarantine = write_manifest(folder, [manifest_entry(folder, "frame_003.fits")])

    report = pipelines.restore_excluded_frames(Target(id="M 1"))

    assert report.applied is False
    assert report.restored_count == 0
    assert [frame.file for frame in report.frames] == ["frame_003.fits"]
    assert (quarantine / "frame_003.fits").exists()


def test_restore_with_apply_moves_frames_back_and_rescans(
    pipelines: ProcessingPipelines, tmp_path: Path
) -> None:
    """With ``apply`` the frame returns and the target is rescanned."""
    folder = camera_folder(tmp_path)
    quarantine = write_manifest(folder, [manifest_entry(folder, "frame_003.fits")])
    target = Target(id="M 1")

    report = pipelines.restore_excluded_frames(target, apply=True)

    assert report.applied is True
    assert report.restored_count == 1
    assert (folder / "frame_003.fits").exists()
    assert not quarantine.exists()
    pipelines._targets.reindex_frames.assert_called_once_with(target)


def test_restore_with_apply_does_not_rescan_when_nothing_moved(
    pipelines: ProcessingPipelines, tmp_path: Path
) -> None:
    """A target with nothing set aside is left alone."""
    camera_folder(tmp_path)

    report = pipelines.restore_excluded_frames(Target(id="M 1"), apply=True)

    assert report.restored_count == 0
    pipelines._targets.reindex_frames.assert_not_called()


def test_frame_quality_can_include_the_frames_already_set_aside(
    pipelines: ProcessingPipelines, tmp_path: Path
) -> None:
    """``include=["excluded"]`` adds the manifest's frames to a preview."""
    folder = camera_folder(tmp_path)
    write_manifest(folder, [manifest_entry(folder, "frame_003.fits")])
    target = make_target(folder, 12)

    with patch(FINDER, side_effect=with_measurement({})):
        preview = pipelines.diagnostics.frame_quality(
            target, kind="quarantine_preview", include=["excluded"], register_job=False
        )

    assert [frame.file for frame in preview.excluded] == ["frame_003.fits"]


def test_target_folder_names_cover_the_spellings_a_folder_may_have() -> None:
    """A folder named with underscores or no spaces still matches."""
    assert frame_quarantine._target_folder_names("M 52") == ["M 52", "M_52", "M52"]
