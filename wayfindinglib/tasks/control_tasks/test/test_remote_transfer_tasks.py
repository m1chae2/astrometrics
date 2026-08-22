"""Purpose: Unit tests for remote telescope file transfer.

Description: Verifies check_for_new_remote_images's local/remote diffing
and download_remote_frames's science-astrometrics indexing call, using a fake
StellarMateInterface driver rather than a real SSH-reachable host.
"""

from unittest.mock import ANY, patch

from wayfindinglib.tasks.control_tasks import remote_transfer_tasks as remote_operations


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


def test_check_for_new_remote_images_reports_only_new_filenames():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify only remote files absent from the local baseline are reported."""
    target = _FakeTarget("M 81", frame_paths=["/local/frame1.fits"])

    with (
        patch(
            "astrometricslib.get_configuration",
            return_value=_patched_config(),
        ),
        patch("wayfindinglib.drivers.stellarmate_interface.StellarMateInterface") as mock_driver,
    ):
        mock_driver.return_value.list_remote_files.return_value = ["frame1.fits", "frame2.fits"]
        result = remote_operations.check_for_new_remote_images(target)

    assert result == {
        "remote_files_available": True,
        "remote_files_count": 1,
        "remote_files": ["frame2.fits"],
    }


def test_check_for_new_remote_images_no_remote_files():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an empty remote listing reports unavailable, not a raise."""
    target = _FakeTarget("M 81", frame_paths=[])

    with (
        patch(
            "astrometricslib.get_configuration",
            return_value=_patched_config(),
        ),
        patch("wayfindinglib.drivers.stellarmate_interface.StellarMateInterface") as mock_driver,
    ):
        mock_driver.return_value.list_remote_files.return_value = []
        result = remote_operations.check_for_new_remote_images(target)

    assert result == {"remote_files_available": False, "remote_files_count": 0, "remote_files": []}


def test_download_remote_frames_indexes_through_science_astrometrics_on_success():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a successful download scans the directory via Astrometrics.

    Astrometrics.processing.scan_target_directory is the underlying call.

    Astrometrics is the science library's public high-level
    interface; this also recalculates total exposure, rather than
    reaching into astrometricslib.data_access internals directly.
    """
    target = _FakeTarget("M 81", frame_paths=[])

    with (
        patch(
            "astrometricslib.get_configuration",
            return_value=_patched_config(),
        ),
        patch("wayfindinglib.drivers.stellarmate_interface.StellarMateInterface") as mock_driver,
        patch("astrometricslib.Astrometrics") as mock_astrometrics,
    ):
        mock_driver.return_value.download_target_folder.return_value = True
        success = remote_operations.download_remote_frames(target)

    assert success is True
    mock_astrometrics.return_value.processing.scan_target_directory.assert_called_once()
    assert target.recalculate_total_exposure_calls == 1


def test_download_remote_frames_returns_false_without_indexing_on_failure():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a failed download does not attempt to index frames."""
    target = _FakeTarget("M 81", frame_paths=[])

    with (
        patch(
            "astrometricslib.get_configuration",
            return_value=_patched_config(),
        ),
        patch("wayfindinglib.drivers.stellarmate_interface.StellarMateInterface") as mock_driver,
        patch("astrometricslib.Astrometrics") as mock_astrometrics,
    ):
        mock_driver.return_value.download_target_folder.return_value = False
        success = remote_operations.download_remote_frames(target)

    assert success is False
    mock_astrometrics.return_value.processing.scan_target_directory.assert_not_called()
    assert target.recalculate_total_exposure_calls == 0


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
        self.targets = self

    def get(self, target_id):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return self._existing.get(target_id)

    def create(self, target_id):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        target = _FakeTargetRecord(target_id)
        self._existing[target_id] = target
        self.created.append(target_id)
        return target

    def reindex_frames(self, target, prune_missing=True):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        target.reindexed = True

    def save(self):  # ruff: ignore[missing-return-type-private-function]
        self.saved = True


def test_download_remote_targets_downloads_and_reindexes_on_success():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a remote download classifies frames and reindexes the target."""
    fake_astrometrics = _FakeAstrometrics()

    with (
        patch(
            "astrometricslib.get_configuration",
            return_value=_patched_config(),
        ),
        patch("astrometricslib.Astrometrics", return_value=fake_astrometrics),
        patch("wayfindinglib.drivers.stellarmate_interface.StellarMateInterface") as mock_driver,
        patch("astrometricslib.classify_and_sort_fits_files") as mock_classify,
    ):
        mock_driver.return_value.download_target_folder.return_value = True
        success = remote_operations.download_remote_targets("M 81")

    assert success is True
    assert fake_astrometrics.created == ["M 81"]
    mock_classify.assert_called_once()
    assert fake_astrometrics.saved is True


def test_download_remote_targets_local_path_skips_download():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a local_path ingests without touching the remote driver."""
    fake_astrometrics = _FakeAstrometrics(existing=[_FakeTargetRecord("M 81")])

    with (
        patch(
            "astrometricslib.get_configuration",
            return_value=_patched_config(),
        ),
        patch("astrometricslib.Astrometrics", return_value=fake_astrometrics),
        patch("wayfindinglib.drivers.stellarmate_interface.StellarMateInterface") as mock_driver,
        patch("astrometricslib.classify_and_sort_fits_files") as mock_classify,
    ):
        success = remote_operations.download_remote_targets("M 81", local_path="/local/lights/M81")

    assert success is True
    mock_driver.assert_not_called()
    mock_classify.assert_called_once_with(["/local/lights/M81"], "M 81", ANY, "Apertura 75Q")
    assert fake_astrometrics.saved is True


def test_discover_unassociated_remote_targets_fuzzy_matches():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify space/underscore/hyphen variants are treated as associated."""

    class _FakeControl:
        def list_remote_targets(self):  # ruff: ignore[missing-return-type-private-function]
            return ["M_81", "NGC 7000", "Unassociated Target"]

    class _FakeTargets:
        def list(self):  # ruff: ignore[missing-return-type-private-function]
            return [_FakeTarget("M 81", []), _FakeTarget("NGC-7000", [])]

    result = remote_operations.discover_unassociated_remote_targets(_FakeControl(), _FakeTargets())
    assert result == ["Unassociated Target"]


def test_discover_unassociated_remote_targets_empty_on_listing_failure():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a failed remote listing degrades to an empty list."""

    class _FakeControl:
        def list_remote_targets(self):  # ruff: ignore[missing-return-type-private-function]
            raise RuntimeError("unreachable")

    assert remote_operations.discover_unassociated_remote_targets(_FakeControl(), None) == []
