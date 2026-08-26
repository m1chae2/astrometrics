"""Purpose: Unit tests for StellarMateInterface.

Description: Verifies folder-name resolution and offline-cooldown
fail-fast behavior, without requiring a real SSH-reachable host.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

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
        with pytest.raises(RuntimeError, match="cooldown active"):
            driver._run_command(["ssh", "test-host", "echo", "hi"])
    mock_run.assert_not_called()


def test_check_connection_returns_false_on_failure():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify check_connection() returns False rather than raising."""
    driver = StellarMateInterface(host_alias="test-host")
    with patch.object(driver, "_run_command", side_effect=RuntimeError("unreachable")):
        assert driver.check_connection() is False


def test_list_remote_targets_returns_empty_list_on_failure():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify list_remote_targets() degrades to an empty list on failure."""
    driver = StellarMateInterface(host_alias="test-host")
    with patch.object(driver, "_run_command", side_effect=RuntimeError("unreachable")):
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
    mock_process.stdout = iter([])
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
