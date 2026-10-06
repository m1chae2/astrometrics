"""Purpose: Unit tests for StellarMateInterface.

Description: Verifies folder-name resolution and offline-cooldown
fail-fast behavior, without requiring a real SSH-reachable host.
"""

import subprocess  # ruff: ignore[suspicious-subprocess-import]
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from astrometricslib import ExternalServiceError
from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface


def test_resolve_remote_folder_name_matches_underscore_variant():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a space-to-underscore variant resolves against the listing."""
    driver = StellarMateInterface(host_alias="test-host")
    with patch.object(driver, "list_remote_targets", return_value=["M_81"]):
        resolved = driver._resolve_remote_folder_name("M 81")
    assert resolved == "M_81"


def test_resolve_remote_folder_name_matches_space_variant():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an underscore-to-space variant resolves against the listing."""
    driver = StellarMateInterface(host_alias="test-host")
    with patch.object(driver, "list_remote_targets", return_value=["M 81"]):
        resolved = driver._resolve_remote_folder_name("M_81")
    assert resolved == "M 81"


def test_resolve_remote_folder_name_returns_unchanged_when_no_match():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the original name is returned when no variant matches."""
    driver = StellarMateInterface(host_alias="test-host")
    with patch.object(driver, "list_remote_targets", return_value=["NGC 7000"]):
        resolved = driver._resolve_remote_folder_name("M 81")
    assert resolved == "M 81"


def test_run_command_fails_fast_within_offline_cooldown():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a command raises immediately, without a subprocess call.

    Applies while the host is within its known-offline cooldown window.
    """
    driver = StellarMateInterface(host_alias="test-host")
    driver._last_connection_status = False
    driver._last_probe_time = time.time()

    with patch("subprocess.run") as mock_run:
        with pytest.raises(ExternalServiceError, match="cooldown active"):
            driver._run_command(["ssh", "test-host", "echo", "hi"])
    mock_run.assert_not_called()


def test_check_connection_returns_false_on_failure():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify check_connection() returns False rather than raising."""
    driver = StellarMateInterface(host_alias="test-host")
    with patch.object(driver, "_run_command", side_effect=ExternalServiceError("unreachable")):
        assert driver.check_connection() is False


def test_check_connection_does_not_hide_a_bug() -> None:
    """Verify an error that is not a connection failure reaches the caller.

    Only a failed command (``ExternalServiceError``) or a missing ``ssh``
    program (``OSError``) means "not connected". A coding mistake must
    show up instead of reading as an offline host.
    """
    driver = StellarMateInterface(host_alias="test-host")
    with (
        patch.object(driver, "_run_command", side_effect=AttributeError("a coding mistake")),
        pytest.raises(AttributeError),
    ):
        driver.check_connection()


def test_list_remote_targets_returns_empty_list_on_failure():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify list_remote_targets() degrades to an empty list on failure."""
    driver = StellarMateInterface(host_alias="test-host")
    with patch.object(driver, "_run_command", side_effect=ExternalServiceError("unreachable")):
        assert driver.list_remote_targets() == []


def test_full_folder_download_protects_remote_path_from_word_splitting(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A full-folder rsync (no selected_files) must pass -s (--protect-args).

    Without it, a space-named remote folder like "NGC 7023" splits into
    two words on the remote shell and rsync reports "No such file or
    directory" -- the --files-from branch already carried -s, but the
    full-folder branch lost it when the remote-path quoting was dropped
    in favour of relying on -s everywhere.
    """
    driver = StellarMateInterface(host_alias="test-host", remote_pictures_path="/home/stellarmate/Pictures")

    mock_process = MagicMock()
    # iter(process.stdout.readline, "") requires a file-like object with a
    # .readline() method; a bare list iterator has no such attribute.
    mock_process.stdout = MagicMock(readline=MagicMock(return_value=""))
    mock_process.wait.return_value = 0

    with (
        patch.object(driver, "_resolve_remote_folder_name", return_value="NGC 7023"),
        patch("subprocess.Popen", return_value=mock_process) as mock_popen,
    ):
        result = driver.download_target_folder(
            remote_target_name="NGC 7023",
            local_dest_path=str(tmp_path),
        )

    assert result is True
    rsync_cmd = mock_popen.call_args.args[0]
    assert "-s" in rsync_cmd


def _make_driver():  # ruff: ignore[missing-return-type-private-function]
    """Build a driver with a fixed host alias, without touching the network.

    Returns
    -------
    driver : `StellarMateInterface`
        A driver whose commands the test replaces with mocks.
    """
    from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface

    return StellarMateInterface(host_alias="stellarmate-test")


_REMOTE_LISTING = (
    "1200\t/home/stellarmate/.local/share/kstars/guidelogs/guide_log-2026-09-24T20-43-58.txt\n"
    "300\t/home/stellarmate/PHD2/PHD2_GuideLog_2026-09-20_201500.txt\n"
)


def test_guide_log_listing_searches_both_the_phd2_and_the_kstars_folders():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the Ekos internal guider's folder is searched, not just PHD2's.

    Regression test: only PHD2's own folders were searched, so the guide
    logs an Ekos user actually has could never be found.
    """
    driver = _make_driver()
    with patch.object(driver, "_run_command", return_value=_REMOTE_LISTING) as mock_run:
        files = driver.list_remote_guide_logs()

    find_command = mock_run.call_args[0][0][2]
    assert ".local/share/kstars/guidelogs" in find_command
    assert "PHD2_GuideLog_*.txt" in find_command
    assert "guide_log*.txt" in find_command
    assert files == [
        "/home/stellarmate/.local/share/kstars/guidelogs/guide_log-2026-09-24T20-43-58.txt",
        "/home/stellarmate/PHD2/PHD2_GuideLog_2026-09-20_201500.txt",
    ]


def test_ekos_analyze_log_listing_reads_the_kstars_analyze_folder():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify analyze logs are found in the KStars analyze folder."""
    driver = _make_driver()
    listing = "5000\t/home/stellarmate/.local/share/kstars/analyze/ekos-2026-09-23T20-31-48.analyze\n"
    with patch.object(driver, "_run_command", return_value=listing) as mock_run:
        files = driver.list_remote_ekos_analyze_logs()

    assert ".local/share/kstars/analyze" in mock_run.call_args[0][0][2]
    assert files == ["/home/stellarmate/.local/share/kstars/analyze/ekos-2026-09-23T20-31-48.analyze"]


def test_listing_is_empty_when_the_host_cannot_be_reached():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an unreachable telescope computer gives an empty list."""
    driver = _make_driver()
    with patch.object(driver, "_run_command", side_effect=ExternalServiceError("unreachable")):
        assert driver.list_remote_guide_logs() == []
        assert driver.list_remote_ekos_analyze_logs() == []


def _finished_process(return_code: int = 0):  # ruff: ignore[missing-return-type-private-function]
    """Build a stand-in for a finished rsync process.

    Returns
    -------
    process : `MagicMock`
        A process that prints nothing and exits with `return_code`.
    """
    process = MagicMock()
    process.stdout = MagicMock(readline=MagicMock(return_value=""))
    process.wait.return_value = return_code
    return process


def _rsync_writing(files: dict[str, bytes], destination):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Build a ``subprocess.Popen`` stand-in whose rsync run creates files.

    Returns
    -------
    fake_popen : `Callable`
        Writes `files` into `destination` when called, as rsync would.
    """

    def fake_popen(command, **kwargs):  # ruff: ignore[missing-type-function-argument, missing-type-kwargs, missing-return-type-private-function]
        """Write the files and return a finished process stand-in.

        Returns
        -------
        process : `MagicMock`
            A process that exited with status 0.
        """
        for name, content in files.items():
            (destination / name).write_bytes(content)
        return _finished_process()

    return fake_popen


def test_download_skips_files_that_are_already_current(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify rsync is not even started when everything is current."""
    driver = _make_driver()
    (tmp_path / "guide_log-2026-09-24T20-43-58.txt").write_bytes(b"x" * 1200)
    (tmp_path / "PHD2_GuideLog_2026-09-20_201500.txt").write_bytes(b"y" * 300)
    with (
        patch.object(driver, "_run_command", return_value=_REMOTE_LISTING),
        patch("subprocess.Popen") as mock_popen,
    ):
        local_paths = driver.download_guide_logs(str(tmp_path))

    mock_popen.assert_not_called()
    assert len(local_paths) == 2


def test_log_and_image_downloads_share_one_rsync_runner(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify image and log downloads both use `_run_rsync`."""
    driver = StellarMateInterface(host_alias="test-host", remote_pictures_path="/home/stellarmate/Pictures")
    with (
        patch.object(driver, "_run_command", return_value=_REMOTE_LISTING),
        patch.object(driver, "_resolve_remote_folder_name", return_value="M_27"),
        patch("subprocess.Popen", return_value=_finished_process()) as mock_popen,
    ):
        driver.download_target_folder(remote_target_name="M_27", local_dest_path=str(tmp_path / "images"))
        driver.download_guide_logs(str(tmp_path / "logs"))

    commands = [call.args[0] for call in mock_popen.call_args_list]
    assert len(commands) == 3  # one image run, then one per remote log folder
    for command in commands:
        assert command[0] == "rsync"
        assert {"-avz", "--no-p", "--no-g", "--no-o", "-s"} <= set(command)


def test_log_download_filters_by_name_and_stops_if_the_link_stalls(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify folders sync with include filters and an idle timeout."""
    driver = _make_driver()
    with (
        patch.object(driver, "_run_command", return_value=_REMOTE_LISTING),
        patch("subprocess.Popen", return_value=_finished_process()) as mock_popen,
    ):
        driver.download_guide_logs(str(tmp_path))

    commands = [call.args[0] for call in mock_popen.call_args_list]
    assert len(commands) == 2  # one run per remote folder
    for command in commands:
        assert "--include=guide_log*.txt" in command
        assert "--include=PHD2_GuideLog_*.txt" in command
        assert "--exclude=*" in command
        assert any(option.startswith("--timeout=") for option in command)
    assert {command[-2] for command in commands} == {
        "stellarmate-test:/home/stellarmate/.local/share/kstars/guidelogs/",
        "stellarmate-test:/home/stellarmate/PHD2/",
    }


def test_files_from_one_folder_share_one_rsync_run(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify many files cost one rsync run, hence one connection.

    Regression test: fetching one file at a time with ``scp`` took minutes
    against the real telescope computer, which is slow at answering many
    file requests.
    """
    driver = _make_driver()
    names = [f"ekos-{number:03d}.analyze" for number in range(60)]
    listing = "".join(f"10\t/home/stellarmate/analyze/{name}\n" for name in names)
    with (
        patch.object(driver, "_run_command", return_value=listing),
        patch(
            "subprocess.Popen", side_effect=_rsync_writing(dict.fromkeys(names, b"0123456789"), tmp_path)
        ) as mock_popen,
    ):
        local_paths = driver.download_ekos_analyze_logs(str(tmp_path))

    assert mock_popen.call_count == 1
    assert "--include=ekos-*.analyze" in mock_popen.call_args.args[0]
    assert len(local_paths) == 60


def test_a_file_that_grew_is_synced_again(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a log still being written triggers a sync and ends up current."""
    driver = _make_driver()
    (tmp_path / "guide_log-2026-09-24T20-43-58.txt").write_bytes(b"x" * 800)
    (tmp_path / "PHD2_GuideLog_2026-09-20_201500.txt").write_bytes(b"y" * 300)
    fake_popen = _rsync_writing({"guide_log-2026-09-24T20-43-58.txt": b"z" * 1200}, tmp_path)
    with (
        patch.object(driver, "_run_command", return_value=_REMOTE_LISTING),
        patch("subprocess.Popen", side_effect=fake_popen) as mock_popen,
    ):
        local_paths = driver.download_guide_logs(str(tmp_path))

    assert mock_popen.call_count == 1  # only the KStars folder holds the changed file
    assert len(local_paths) == 2


def test_a_failed_rsync_still_returns_the_files_that_are_current(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a failed run is not retried with another tool."""
    driver = _make_driver()
    (tmp_path / "PHD2_GuideLog_2026-09-20_201500.txt").write_bytes(b"y" * 300)
    with (
        patch.object(driver, "_run_command", return_value=_REMOTE_LISTING),
        patch("subprocess.Popen", return_value=_finished_process(return_code=23)) as mock_popen,
    ):
        local_paths = driver.download_guide_logs(str(tmp_path))

    assert mock_popen.call_count == 1
    assert local_paths == [str(tmp_path / "PHD2_GuideLog_2026-09-20_201500.txt")]


def test_listing_commands_tolerate_missing_folders():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify `find` failing on a missing folder does not fail the listing.

    Regression test: an Ekos-only setup has no PHD2 folder, `find` then
    exits non-zero, and the listing command raised instead of returning the
    logs in the folders that do exist.
    """
    driver = _make_driver()
    with patch.object(driver, "_run_command", return_value="") as mock_run:
        driver.list_remote_guide_logs()
        guide_command = mock_run.call_args[0][0][2]
        driver.list_remote_ekos_analyze_logs()
        analyze_command = mock_run.call_args[0][0][2]

    assert guide_command.rstrip().endswith("|| true")
    assert analyze_command.rstrip().endswith("|| true")


def test_kstars_logs_go_into_their_own_folder(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the KStars text logs are fetched into ``kstars_logs`` only."""
    driver = _make_driver()
    (tmp_path / "kstars_logs").mkdir()
    listing = "40\t/home/stellarmate/.local/share/kstars/logs/2026-10-02/log_20-14-00.txt\n"
    with (
        patch.object(driver, "_run_command", return_value=listing) as mock_command,
        patch(
            "subprocess.Popen",
            side_effect=_rsync_writing({"log_20-14-00.txt": b"x" * 40}, tmp_path / "kstars_logs"),
        ) as mock_popen,
    ):
        local_paths = driver.download_kstars_logs(str(tmp_path))

    assert "-mtime -3" in mock_command.call_args.args[0][-1]
    assert "--include=log_*.txt" in mock_popen.call_args.args[0]
    assert local_paths == [str(tmp_path / "kstars_logs" / "log_20-14-00.txt")]


def test_download_refuses_and_creates_nothing_when_the_frames_drive_is_missing(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a download below an unmounted mount point makes no folders."""
    from astrometricslib import StorageNotMountedError

    mount_point = tmp_path / "nas"

    class _Settings:
        def get_frames_mount_point(self) -> Path:
            return mount_point

    monkeypatch.setattr("astrometricslib.foundation.storage.mount.get_configuration", lambda: _Settings())
    driver = StellarMateInterface(host_alias="test-host", remote_pictures_path="/home/stellarmate/Pictures")

    with (
        patch.object(driver, "_resolve_remote_folder_name", return_value="M_27"),
        patch("subprocess.Popen") as mock_popen,
        pytest.raises(StorageNotMountedError),
    ):
        driver.download_target_folder(remote_target_name="M_27", local_dest_path=str(mount_point / "frames"))

    assert not mount_point.exists()
    mock_popen.assert_not_called()


def test_a_host_known_offline_fails_fast_even_after_the_cooldown_and_rechecks_in_the_background():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A caller never waits on an offline host; a background check follows."""
    driver = StellarMateInterface(host_alias="test-host")
    driver._last_connection_status = False
    driver._last_probe_time = time.time() - 60.0
    started = []

    with patch.object(driver, "_start_background_probe", lambda: started.append(True)):
        with patch("subprocess.run") as mock_run:
            with pytest.raises(ExternalServiceError, match="known offline"):
                driver._run_command(["ssh", "test-host", "ls"])
            mock_run.assert_not_called()

    assert started == [True]


def test_the_first_call_probes_once_and_concurrent_calls_share_the_answer() -> None:
    """Many callers at start-up cost one slow probe, not one each."""
    driver = StellarMateInterface(host_alias="test-host")
    calls = []

    def slow_failure(*args: Any, **kwargs: Any) -> None:
        calls.append(1)
        time.sleep(0.3)
        raise subprocess.CalledProcessError(255, "ssh", stderr="Could not resolve hostname")

    errors = []

    def call() -> None:
        """Make one call and keep its error."""
        try:
            driver._run_command(["ssh", "test-host", "ls"])
        except ExternalServiceError as error:
            errors.append(str(error))

    with patch("subprocess.run", side_effect=slow_failure):
        threads = [threading.Thread(target=call) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert len(calls) == 1
    assert len(errors) == 4
