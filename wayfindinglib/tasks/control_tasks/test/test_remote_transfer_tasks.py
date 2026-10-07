"""Purpose: Unit tests for remote telescope file transfer.

Description: Verifies the listing, download, sync and log-sync tasks behind
`control.remote`, using a fake `RemoteTransferDriver` (matching
`StellarMateInterface`'s shape) rather than a real SSH-reachable host.
"""

import os
import time
from contextlib import AbstractContextManager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

import pytest

from astrometricslib import ExternalServiceError, NotFoundError
from wayfindinglib.tasks.control_tasks import remote_transfer_tasks as remote_operations


class _FakeObservatory:
    """A stand-in `ControlContext` exposing a `remote_transfer_driver`."""

    def __init__(self, remote_transfer_driver: Mock) -> None:
        """Hold the driver and a mock science library."""
        self.remote_transfer_driver = remote_transfer_driver
        self.astrometrics = Mock()


class _FakeFrame:
    def __init__(self, path):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.path = path


class _FakeTarget:
    def __init__(self, target_id, frame_paths):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.id = target_id
        self.frames = [_FakeFrame(p) for p in frame_paths]
        self.recalculate_total_exposure_calls = 0

    def recalculate_total_exposure(self):  # ruff: ignore[missing-return-type-private-function]
        self.recalculate_total_exposure_calls += 1


def _patched_config(**overrides):  # ruff: ignore[missing-type-kwargs, missing-return-type-private-function]
    class _FakeConfig:
        def get_telescope_hostname(self):  # ruff: ignore[missing-return-type-private-function]
            return overrides.get("host", "stellarmate")

        def get_remote_pictures_path(self):  # ruff: ignore[missing-return-type-private-function]
            return overrides.get("remote_path", "/home/stellarmate/Pictures")

        def get_frames_path(self):  # ruff: ignore[missing-return-type-private-function]
            return overrides.get("frames_path", "/tmp/frames")

    return _FakeConfig()


def test_download_remote_frames_indexes_through_science_astrometrics_on_success() -> None:
    """Verify a successful download reindexes the target through Astrometrics.

    `TargetCatalog.reindex_frames` is the science library's public way to
    find new files on disk; it also recalculates the total exposure and
    saves the target.
    """
    target = _FakeTarget("M 81", frame_paths=[])
    driver = Mock()
    driver.download_target_folder.return_value = True
    observatory = _FakeObservatory(driver)

    with (
        patch(
            "astrometricslib.get_configuration",
            return_value=_patched_config(),
        ),
    ):
        success = remote_operations.download_remote_frames(observatory, target)

    assert success is True
    observatory.astrometrics.targets.reindex_frames.assert_called_once_with(target)


def test_download_remote_frames_returns_false_without_indexing_on_failure() -> None:
    """Verify a failed download does not attempt to index frames."""
    target = _FakeTarget("M 81", frame_paths=[])
    driver = Mock()
    driver.download_target_folder.return_value = False
    observatory = _FakeObservatory(driver)

    with (
        patch(
            "astrometricslib.get_configuration",
            return_value=_patched_config(),
        ),
    ):
        success = remote_operations.download_remote_frames(observatory, target)

    assert success is False
    observatory.astrometrics.targets.reindex_frames.assert_not_called()


class _FakeTargetRecord:
    def __init__(self, target_id, ra="0h 0m 0s", dec="0° 0′ 0″"):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.id = target_id
        self.ra = ra
        self.dec = dec


class _FakeAstrometrics:
    def __init__(self, existing=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self._existing = {t.id: t for t in (existing or [])}
        self.created = []
        self.saved = False
        self.prune_flags = []
        self.targets = self

    def get(self, target_id, refresh=False):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return self._existing.get(target_id)

    def create(self, target_id):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        target = _FakeTargetRecord(target_id)
        self._existing[target_id] = target
        self.created.append(target_id)
        return target

    def reindex_frames(self, target, prune_missing=True):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        target.reindexed = True
        self.prune_flags.append(prune_missing)

    def save(self):  # ruff: ignore[missing-return-type-private-function]
        self.saved = True


def test_download_remote_targets_downloads_and_reindexes_on_success() -> None:
    """Verify a remote download classifies frames and reindexes the target."""
    fake_astrometrics = _FakeAstrometrics()
    driver = Mock()
    driver.resolve_remote_folder_name.return_value = "M 81"
    driver.list_remote_files_with_sizes.return_value = []
    driver.download_target_folder.return_value = True
    observatory = _FakeObservatory(driver)
    observatory.astrometrics = fake_astrometrics

    with (
        patch(
            "astrometricslib.get_configuration",
            return_value=_patched_config(),
        ),
        patch("astrometricslib.classify_and_sort_fits_files") as mock_classify,
    ):
        success = remote_operations.download_remote_targets(observatory, "M 81")

    assert success is True
    assert fake_astrometrics.created == ["M 81"]
    mock_classify.assert_called_once()
    assert fake_astrometrics.saved is True


def test_download_remote_targets_local_path_skips_download() -> None:
    """Verify a local_path ingests without touching the remote driver."""
    fake_astrometrics = _FakeAstrometrics(existing=[_FakeTargetRecord("M 81")])
    driver = Mock()
    observatory = _FakeObservatory(driver)
    observatory.astrometrics = fake_astrometrics

    with (
        patch(
            "astrometricslib.get_configuration",
            return_value=_patched_config(),
        ),
        patch("astrometricslib.classify_and_sort_fits_files") as mock_classify,
    ):
        success = remote_operations.download_remote_targets(
            observatory, "M 81", local_path="/local/lights/M81"
        )

    assert success is True
    driver.download_target_folder.assert_not_called()
    mock_classify.assert_called_once_with(["/local/lights/M81"], "M 81", ANY, "Apertura 75Q", [])
    assert fake_astrometrics.saved is True


def test_discover_unassociated_remote_targets_fuzzy_matches() -> None:
    """Verify space/underscore/hyphen variants are treated as associated."""
    driver = Mock()
    driver.list_remote_targets.return_value = ["M_81", "NGC 7000", "Unassociated Target"]
    context = _FakeObservatory(driver)
    context.astrometrics.targets.list.return_value = [_FakeTarget("M 81", []), _FakeTarget("NGC-7000", [])]

    result = remote_operations.discover_unassociated_remote_targets(context)
    assert result == ["Unassociated Target"]


def test_discover_unassociated_remote_targets_empty_on_listing_failure() -> None:
    """Verify a failed remote listing degrades to an empty list."""
    driver = Mock()
    driver.list_remote_targets.side_effect = ExternalServiceError("unreachable")

    assert remote_operations.discover_unassociated_remote_targets(_FakeObservatory(driver)) == []


def test_download_remote_targets_stages_into_the_resolved_remote_folder(tmp_path: Path) -> None:
    """Verify staging/classification use the resolved remote folder name.

    A local id of "M 42" resolves to a remote folder of "M_42", which is
    where the driver actually downloads. Deriving the post-download scan
    path from the unresolved id instead left every frame unclassified in
    a parallel directory.
    """
    fake_astrometrics = _FakeAstrometrics()
    driver = Mock()
    driver.resolve_remote_folder_name.return_value = "M_42"
    driver.list_remote_files_with_sizes.return_value = [("Light/Luminance/a.fits", 10)]
    driver.download_target_folder.return_value = True
    observatory = _FakeObservatory(driver)
    observatory.astrometrics = fake_astrometrics

    with (
        patch(
            "astrometricslib.get_configuration",
            return_value=_patched_config(frames_path=str(tmp_path)),
        ),
        patch("astrometricslib.classify_and_sort_fits_files") as mock_classify,
    ):
        success = remote_operations.download_remote_targets(observatory, "M 42")

    assert success is True
    # The driver is asked for the resolved folder, not the raw target id.
    assert driver.download_target_folder.call_args.kwargs["remote_target_name"] == "M_42"
    # Classification scans the same directory the download landed in.
    assert mock_classify.call_args.args[0] == [str(tmp_path / "lights" / "M_42")]


def test_download_remote_targets_transfers_only_files_not_held_locally(tmp_path: Path) -> None:
    """Verify already-held frames are excluded from the rsync file list."""
    fake_astrometrics = _FakeAstrometrics()
    classified_dir = tmp_path / "lights" / "M 42" / "Apertura 75Q" / "ZWO ASI 533MM Pro"
    classified_dir.mkdir(parents=True)
    (classified_dir / "held.fits").write_text("already downloaded")
    driver = Mock()
    driver.resolve_remote_folder_name.return_value = "M_42"
    driver.list_remote_files_with_sizes.return_value = [
        ("Light/Luminance/held.fits", len("already downloaded")),
        ("Light/Luminance/fresh.fits", 999),
    ]
    driver.download_target_folder.return_value = True
    observatory = _FakeObservatory(driver)
    observatory.astrometrics = fake_astrometrics

    with (
        patch(
            "astrometricslib.get_configuration",
            return_value=_patched_config(frames_path=str(tmp_path)),
        ),
        patch("astrometricslib.classify_and_sort_fits_files"),
    ):
        success = remote_operations.download_remote_targets(observatory, "M 42")

    assert success is True
    assert driver.download_target_folder.call_args.kwargs["selected_files"] == ["Light/Luminance/fresh.fits"]


def test_download_remote_targets_incremental_filters_explicit_selected_files(tmp_path: Path) -> None:
    """Verify explicit selected_files list is also filtered.

    Already-held frames must be excluded even if selected_files is passed.
    """
    fake_astrometrics = _FakeAstrometrics()
    darks_dir = tmp_path / "darks" / "ZWO ASI 533MM Pro" / "100" / "60.0"
    darks_dir.mkdir(parents=True)
    (darks_dir / "Dark_001.fits").write_text("existing dark content")
    driver = Mock()
    driver.resolve_remote_folder_name.return_value = "Dark"
    driver.list_remote_files_with_sizes.return_value = [
        ("Dark_001.fits", len("existing dark content")),
        ("Dark_002.fits", 1234),
    ]
    driver.download_target_folder.return_value = True
    observatory = _FakeObservatory(driver)
    observatory.astrometrics = fake_astrometrics

    with (
        patch(
            "astrometricslib.get_configuration",
            return_value=_patched_config(frames_path=str(tmp_path)),
        ),
        patch("astrometricslib.classify_and_sort_fits_files"),
    ):
        success = remote_operations.download_remote_targets(
            observatory, "Dark", selected_files=["Dark_001.fits", "Dark_002.fits"]
        )

    assert success is True
    assert driver.download_target_folder.call_args.kwargs["selected_files"] == ["Dark_002.fits"]


def test_download_remote_targets_skips_transfer_when_nothing_is_new(tmp_path: Path) -> None:
    """Verify a fully-synced target transfers nothing but still reindexes."""
    fake_astrometrics = _FakeAstrometrics()
    staging_dir = tmp_path / "lights" / "M_42"
    staging_dir.mkdir(parents=True)
    (staging_dir / "held.fits").write_text("already downloaded")
    driver = Mock()
    driver.resolve_remote_folder_name.return_value = "M_42"
    driver.list_remote_files_with_sizes.return_value = [
        ("Light/Luminance/held.fits", len("already downloaded"))
    ]
    observatory = _FakeObservatory(driver)
    observatory.astrometrics = fake_astrometrics

    with (
        patch(
            "astrometricslib.get_configuration",
            return_value=_patched_config(frames_path=str(tmp_path)),
        ),
        patch("astrometricslib.classify_and_sort_fits_files") as mock_classify,
    ):
        success = remote_operations.download_remote_targets(observatory, "M 42")

    assert success is True
    driver.download_target_folder.assert_not_called()
    # Still classifies, so staging left by an earlier run gets healed.
    mock_classify.assert_called_once()
    assert fake_astrometrics.saved is True


def test_download_remote_targets_incremental_false_forces_full_transfer(tmp_path: Path) -> None:
    """Verify incremental=False bypasses the local-library diff."""
    fake_astrometrics = _FakeAstrometrics()
    driver = Mock()
    driver.resolve_remote_folder_name.return_value = "M_42"
    driver.download_target_folder.return_value = True
    observatory = _FakeObservatory(driver)
    observatory.astrometrics = fake_astrometrics

    with (
        patch(
            "astrometricslib.get_configuration",
            return_value=_patched_config(frames_path=str(tmp_path)),
        ),
        patch("astrometricslib.classify_and_sort_fits_files"),
    ):
        success = remote_operations.download_remote_targets(observatory, "M 42", incremental=False)

    assert success is True
    driver.list_remote_files_with_sizes.assert_not_called()
    assert driver.download_target_folder.call_args.kwargs["selected_files"] is None


def test_download_remote_targets_transfers_same_name_file_of_different_size(tmp_path: Path) -> None:
    """Verify a same-named but differently-sized remote frame still transfers.

    Separate sessions can reuse a capture-order naming pattern, so a
    filename-only comparison would treat a genuinely new frame as
    already held and silently skip it.
    """
    fake_astrometrics = _FakeAstrometrics()
    classified_dir = tmp_path / "lights" / "M 27" / "Nikkor 300mm"
    classified_dir.mkdir(parents=True)
    (classified_dir / "M_27_Light_001.fits").write_bytes(b"x" * 100)
    driver = Mock()
    driver.resolve_remote_folder_name.return_value = "M 27"
    # Same basename, different size -> a different frame.
    driver.list_remote_files_with_sizes.return_value = [("Light/Luminance/M_27_Light_001.fits", 250)]
    driver.download_target_folder.return_value = True
    observatory = _FakeObservatory(driver)
    observatory.astrometrics = fake_astrometrics

    with (
        patch(
            "astrometricslib.get_configuration",
            return_value=_patched_config(frames_path=str(tmp_path)),
        ),
        patch("astrometricslib.classify_and_sort_fits_files"),
    ):
        success = remote_operations.download_remote_targets(observatory, "M 27")

    assert success is True
    assert driver.download_target_folder.call_args.kwargs["selected_files"] == [
        "Light/Luminance/M_27_Light_001.fits"
    ]


class _EmptyTargetCatalog:
    """Stands in for the target catalog, with nothing in it."""

    def list(self):  # ruff: ignore[missing-return-type-private-function]
        return []


class _EmptyAstrometrics:
    """Stands in for the Astrometrics facade the sync builds for itself."""

    def __init__(self, *args, **kwargs):  # ruff: ignore[missing-return-type-special-method, missing-type-kwargs, missing-type-args]
        self.targets = _EmptyTargetCatalog()


def _our_log_handlers(logger_name: str) -> list:
    """List only the log handlers this library attaches.

    pytest adds its own capture handler while a test runs, and that one
    is not ours to remove.

    Returns
    -------
    handlers : `list`
        The file and database handlers attached to `logger_name`.
    """
    import logging

    from astrometricslib import DbLogHandler

    return [
        handler
        for handler in logging.getLogger(logger_name).handlers
        if isinstance(handler, logging.FileHandler | DbLogHandler)
    ]


def test_sync_all_remote_folders_records_a_job_and_cleans_up_its_log_handlers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the sync records a job and leaves no log handlers behind.

    The handlers are attached to the shared "wayfindinglib" logger so
    progress from the download drivers reaches the job's log. Leaving
    them attached would mean this job's log kept collecting messages from
    every later job, and leaving the file open leaks a file handle for
    the life of the process.

    Nothing is actually downloaded here: the remote listings are stubbed
    empty, so this exercises the job bookkeeping around the sync rather
    than the sync itself.
    """
    import astrometricslib
    from astrometricslib import AppConfiguration, LoggerInterface

    library_path = tmp_path / "library"
    library_path.mkdir()
    logs_path = tmp_path / "logs"
    logs_path.mkdir()

    configuration = AppConfiguration()
    configuration.update_config({"Image Library": {"path": str(library_path)}})
    monkeypatch.setattr(configuration, "get_logs_path", lambda: logs_path)
    monkeypatch.setattr(astrometricslib, "get_configuration", lambda: configuration)
    monkeypatch.setattr(remote_operations, "list_remote_calibration_folders", lambda context: [])
    monkeypatch.setattr(remote_operations, "discover_unassociated_remote_targets", lambda context: [])

    handlers_before = len(_our_log_handlers("wayfindinglib"))

    result = remote_operations.sync_all_remote_folders(
        context=SimpleNamespace(astrometrics=_EmptyAstrometrics()), register_job=True
    )

    assert result["succeeded"] == []
    assert result["failed"] == []
    assert result["job_id"] is not None

    stored_job = LoggerInterface(configuration.get_logs_db_path()).get_job(result["job_id"])
    assert stored_job is not None
    assert stored_job.job_type == "remote_sync"
    assert stored_job.status == "completed"
    # This sync sets a finish time, which the shared helper's simpler
    # status update does not -- proof that behaviour survived the move.
    assert stored_job.completed_at

    assert len(_our_log_handlers("wayfindinglib")) == handlers_before
    assert _our_log_handlers(f"job_{result['job_id']}") == []


def test_sync_all_remote_folders_without_job_registration_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the opt-out skips the job row and attaches no handlers."""
    import astrometricslib
    from astrometricslib import AppConfiguration

    library_path = tmp_path / "library"
    library_path.mkdir()
    configuration = AppConfiguration()
    configuration.update_config({"Image Library": {"path": str(library_path)}})
    monkeypatch.setattr(astrometricslib, "get_configuration", lambda: configuration)
    monkeypatch.setattr(remote_operations, "list_remote_calibration_folders", lambda context: [])
    monkeypatch.setattr(remote_operations, "discover_unassociated_remote_targets", lambda context: [])

    handlers_before = len(_our_log_handlers("wayfindinglib"))

    result = remote_operations.sync_all_remote_folders(
        context=SimpleNamespace(astrometrics=_EmptyAstrometrics()), register_job=False
    )

    assert result["job_id"] is None
    assert len(_our_log_handlers("wayfindinglib")) == handlers_before


def test_sync_calibration_folder_summarises_what_was_added(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the sync reports counts and the folders new frames landed in.

    The remote listing has three files and the library already holds
    one, so two are requested; the stubbed sorting step then drops them
    into a per-exposure folder, which the summary must report.
    """
    import astrometricslib

    frames_path = tmp_path / "library"
    held_folder = frames_path / "darks" / "Cam" / "0.0" / "60.0"
    held_folder.mkdir(parents=True)
    (held_folder / "Dark_001.fits").write_bytes(b"x" * 10)

    class _Configuration:
        def get_frames_path(self) -> Path:
            return frames_path

    class _Catalog:
        def refresh(self, kind: str) -> None:
            pass

        def save(self) -> None:
            pass

    class _Astrometrics:
        def __init__(self, config: object) -> None:
            self.processing = type("Processing", (), {"calibration": _Catalog()})()

    class _Api:
        astrometrics = _Astrometrics(None)

    def fake_classify(
        scan_list: list[str],
        target_id: str,
        config: object,
        telescope_name: str,
        added_paths: list[str] | None = None,
    ) -> int:
        for name in ("Dark_002.fits", "Dark_003.fits"):
            (held_folder / name).write_bytes(b"y" * 10)
            if added_paths is not None:
                added_paths.append(str(held_folder / name))
        return 2

    monkeypatch.setattr(astrometricslib, "get_configuration", lambda: _Configuration())
    monkeypatch.setattr(astrometricslib, "classify_and_sort_fits_files", fake_classify)
    monkeypatch.setattr(remote_operations, "download_remote_frames", lambda context, target, **kwargs: True)
    monkeypatch.setattr(
        remote_operations,
        "list_remote_files_with_sizes",
        lambda context, folder: [("Dark_001.fits", 10), ("Dark_002.fits", 10), ("Dark_003.fits", 10)],
    )

    summary = remote_operations.sync_calibration_folder(_Api(), "Dark")

    assert summary == {
        "success": True,
        "remote_count": 3,
        "already_held_count": 1,
        "transferred_count": 2,
        "added_by_folder": {os.path.join("darks", "Cam", "0.0", "60.0"): 2},
    }


def _sync_observatory(remote_folders, resolved, remote_files) -> tuple[_FakeObservatory, Mock]:  # ruff: ignore[missing-type-function-argument]
    """Build a fake observatory with a listed telescope computer.

    Returns
    -------
    observatory, driver : `tuple`
        The fake observatory and its mock driver.
    """
    driver = Mock()
    driver.list_remote_targets.return_value = remote_folders
    driver.resolve_remote_folder_name.return_value = resolved
    driver.list_remote_files_with_sizes.return_value = remote_files
    driver.download_target_folder.return_value = True
    return _FakeObservatory(driver), driver


def test_plan_target_download_counts_new_and_held_files(tmp_path: Path) -> None:
    """A plan separates held files (same name and size) from new ones."""
    held = tmp_path / "lights" / "M 13"
    held.mkdir(parents=True)
    (held / "a.fits").write_bytes(b"x" * 10)
    observatory, _ = _sync_observatory(
        ["M_13", "Bias"], "M_13", [("Light/a.fits", 10), ("Light/b.fits", 20), ("Light/c.fits", 30)]
    )
    with patch("astrometricslib.get_configuration", return_value=_patched_config(frames_path=str(tmp_path))):
        plan = remote_operations.plan_target_download(observatory, "M 13")
    assert plan["remote_folder"] == "M_13"
    assert (plan["remote_files"], plan["already_held"], plan["to_transfer"]) == (3, 1, 2)
    assert plan["examples"] == ["Light/b.fits", "Light/c.fits"]


def test_plan_refuses_a_name_that_is_not_a_remote_target_folder() -> None:
    """A made-up name cannot reach the shell or create a target."""
    observatory, driver = _sync_observatory(["M_13", "Bias"], "x; rm -rf /", [])
    with (
        patch("astrometricslib.get_configuration", return_value=_patched_config()),
        pytest.raises(NotFoundError, match="No remote target folder matches"),
    ):
        remote_operations.sync_target_frames(observatory, "x; rm -rf /", dry_run=False)
    driver.list_remote_files_with_sizes.assert_not_called()
    driver.download_target_folder.assert_not_called()


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    """A dry run reports the plan and never downloads or indexes."""
    observatory, driver = _sync_observatory(["M_13"], "M_13", [("Light/b.fits", 20)])
    with (
        patch("astrometricslib.get_configuration", return_value=_patched_config(frames_path=str(tmp_path))),
        patch("astrometricslib.Astrometrics") as astrometrics,
    ):
        result = remote_operations.sync_target_frames(observatory, "M 13")
    assert result["dry_run"] is True
    assert result["to_transfer"] == 1
    driver.download_target_folder.assert_not_called()
    astrometrics.assert_not_called()


def test_real_run_downloads_without_pruning(tmp_path: Path) -> None:
    """A real run transfers, sorts and indexes, and never prunes."""
    observatory, driver = _sync_observatory(["M_13"], "M_13", [("Light/b.fits", 20)])
    fake_astrometrics = _FakeAstrometrics(existing=[_FakeTargetRecord("M 13")])
    observatory.astrometrics = fake_astrometrics
    with (
        patch("astrometricslib.get_configuration", return_value=_patched_config(frames_path=str(tmp_path))),
        patch("astrometricslib.classify_and_sort_fits_files"),
        patch("astrometricslib.require_mounted_storage"),
    ):
        result = remote_operations.sync_target_frames(observatory, "M 13", dry_run=False)
    assert (result["success"], result["transferred"]) == (True, 1)
    driver.download_target_folder.assert_called_once()
    assert fake_astrometrics.saved is True
    assert fake_astrometrics.prune_flags == [False]


def test_real_run_with_nothing_new_does_nothing(tmp_path: Path) -> None:
    """When every file is held, nothing is transferred."""
    held = tmp_path / "lights" / "M 13"
    held.mkdir(parents=True)
    (held / "a.fits").write_bytes(b"x" * 10)
    observatory, driver = _sync_observatory(["M_13"], "M_13", [("Light/a.fits", 10)])
    with patch("astrometricslib.get_configuration", return_value=_patched_config(frames_path=str(tmp_path))):
        result = remote_operations.sync_target_frames(observatory, "M 13", dry_run=False)
    assert (result["success"], result["transferred"]) == (True, 0)
    driver.download_target_folder.assert_not_called()


def test_real_run_refuses_when_the_drive_is_not_mounted(tmp_path: Path) -> None:
    """With the frames drive missing, nothing is written."""
    from astrometricslib import StorageNotMountedError

    observatory, driver = _sync_observatory(["M_13"], "M_13", [("Light/b.fits", 20)])
    with (
        patch("astrometricslib.get_configuration", return_value=_patched_config(frames_path=str(tmp_path))),
        patch(
            "astrometricslib.require_mounted_storage", side_effect=StorageNotMountedError("Mount /mnt/nas")
        ),
        pytest.raises(StorageNotMountedError, match="Mount /mnt/nas"),
    ):
        remote_operations.sync_target_frames(observatory, "M 13", dry_run=False)
    driver.download_target_folder.assert_not_called()


class _LogObservatory:
    """A fake observatory that lists remote logs and records ingestion."""

    def __init__(self, tmp_path: Path, reachable: bool = True) -> None:
        """Set up a driver with two guide logs and two Ekos logs."""
        self.ingested: list[tuple[str, bool]] = []
        self.driver = Mock()
        self.driver.check_connection.return_value = reachable
        self.driver._remote_guide_log_sizes.return_value = {
            "/r/guide_log-a.txt": 10,
            "/r/guide_log-b.txt": 20,
        }
        self.driver._remote_ekos_analyze_log_sizes.return_value = {
            "/r/ekos-a.analyze": 5,
            "/r/ekos-b.analyze": 6,
        }
        self.remote_transfer_driver = self.driver
        self.library = tmp_path

    def ekos_log_directory(self) -> str:
        """Return the local Ekos log folder inside the temporary library.

        Returns
        -------
        directory : `str`
            ``ekos_logs`` under the temporary folder.
        """
        return str(self.library / "ekos_logs")

    def record_ingestion(
        self, context: object, destination_dir: str, download: bool = True
    ) -> dict[str, int]:
        """Record the call and return a summary.

        Returns
        -------
        summary : `dict` [`str`, `int`]
            A fixed summary.
        """
        self.ingested.append((destination_dir, download))
        return {"session_contexts_stored": 2}


def _log_patches(observatory: _LogObservatory) -> AbstractContextManager[Mock]:
    """Send the log ingestion to the fake observatory's recorder.

    Returns
    -------
    patcher : `contextlib.AbstractContextManager`
        A patch of `ekos_log_ingestion.ingest_ekos_logs`.
    """
    from wayfindinglib.tasks.control_tasks import ekos_log_ingestion

    return patch.object(ekos_log_ingestion, "ingest_ekos_logs", side_effect=observatory.record_ingestion)


def test_plan_log_sync_compares_names_and_sizes(tmp_path: Path) -> None:
    """A log is new unless a local file has the same name and size."""
    (tmp_path / "guide_log-a.txt").write_bytes(b"x" * 10)
    (tmp_path / "ekos-a.analyze").write_bytes(b"x" * 999)
    plan = remote_operations.plan_log_sync(_LogObservatory(tmp_path), str(tmp_path))
    assert plan["guide_logs"]["to_download"] == 1
    assert plan["guide_logs"]["examples"] == ["guide_log-b.txt"]
    assert plan["ekos_analyze_logs"]["to_download"] == 2
    assert plan["ekos_analyze_logs"]["remote_files"] == 2


def test_plan_log_sync_marks_an_unsupported_driver(tmp_path: Path) -> None:
    """A driver that cannot list a kind of log is reported, not an error."""
    observatory = _LogObservatory(tmp_path)
    observatory.remote_transfer_driver = SimpleNamespaceDriver()
    plan = remote_operations.plan_log_sync(observatory, str(tmp_path))
    assert plan["guide_logs"] == {"supported": False}


class SimpleNamespaceDriver:
    """A driver with no log listing methods."""


def test_log_sync_dry_run_ingests_nothing(tmp_path: Path) -> None:
    """A dry run reports the plan and never calls the ingestion."""
    observatory = _LogObservatory(tmp_path)
    with _log_patches(observatory):
        result = remote_operations.sync_remote_logs(observatory)
    assert result["dry_run"] is True
    assert observatory.ingested == []
    assert result["guide_logs"]["to_download"] == 2


def test_log_sync_real_run_ingests_with_download(tmp_path: Path) -> None:
    """A real run calls the repeat-safe ingestion with downloading on."""
    observatory = _LogObservatory(tmp_path)
    with _log_patches(observatory):
        result = remote_operations.sync_remote_logs(observatory, dry_run=False)
    assert observatory.ingested == [(str(tmp_path / "ekos_logs"), True)]
    assert result["ingested"] == {"session_contexts_stored": 2}


def test_log_sync_refuses_when_the_telescope_computer_is_unreachable(tmp_path: Path) -> None:
    """With no connection, the tool raises and does nothing."""
    observatory = _LogObservatory(tmp_path, reachable=False)
    with _log_patches(observatory), pytest.raises(ExternalServiceError, match="cannot be reached"):
        remote_operations.sync_remote_logs(observatory, dry_run=False)
    assert observatory.ingested == []


def test_download_progress_is_reported_to_the_running_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """New files in the folder show up as the job's progress and message."""
    marks: list[tuple[str, int, int | None, str | None]] = []

    class FakeJob:
        """A job that records the progress it is given."""

        def mark(
            self, status: str, progress: int, *, progress_total: int | None = None, message: str | None = None
        ) -> None:
            """Record one progress report."""
            marks.append((status, progress, progress_total, message))

    monkeypatch.setattr("astrometricslib.get_current_job", lambda: FakeJob())
    monkeypatch.setattr(remote_operations, "PROGRESS_POLL_SECONDS", 0.05)
    with remote_operations.report_download_progress(str(tmp_path), expected=3):
        (tmp_path / "a.fits").write_bytes(b"x")
        (tmp_path / "b.fits").write_bytes(b"x")
        time.sleep(0.3)
    assert marks
    assert marks[-1][1:3] == (2, 3)
    assert marks[-1][3] == "2 of 3 frames transferred"


def test_download_progress_does_nothing_outside_a_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no running job the block just runs."""
    monkeypatch.setattr("astrometricslib.get_current_job", lambda: None)
    with remote_operations.report_download_progress(str(tmp_path), expected=3):
        pass
