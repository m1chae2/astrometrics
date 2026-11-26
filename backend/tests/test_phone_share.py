"""Unit tests for phone_share.

GSConnect is replaced, so nothing is sent and no process starts.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from astrometricslib.exceptions import ConfigurationError
from backend.services.infrastructure import phone_share

MANAGED_OBJECTS = (
    "({objectpath '/org/gnome/Shell/Extensions/GSConnect/Device/aaa': "
    "{'org.gnome.Shell.Extensions.GSConnect.Device': {'Connected': <true>, 'Id': <'aaa'>, "
    "'Name': <'Pixel 9'>, 'Paired': <true>}}, "
    "objectpath '/org/gnome/Shell/Extensions/GSConnect/Device/bbb': "
    "{'org.gnome.Shell.Extensions.GSConnect.Device': {'Connected': <false>, 'Id': <'bbb'>, "
    "'Name': <'Old tablet'>, 'Paired': <true>}}},)"
)
"""What `gdbus` prints for one connected phone and one that is out of reach."""


def _run_returning(stdout: str, returncode: int = 0) -> MagicMock:
    """Make a stand-in for `subprocess.run` that always gives one result.

    Parameters
    ----------
    stdout : `str`
        The text the command prints.
    returncode : `int`, optional
        The exit status.

    Returns
    -------
    run : `MagicMock`
        The stand-in.
    """
    return MagicMock(
        return_value=phone_share.subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="failed")
    )


def test_only_connected_paired_devices_are_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A phone that is not connected is left out."""
    monkeypatch.setattr(phone_share.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(phone_share.subprocess, "run", _run_returning(MANAGED_OBJECTS))

    assert phone_share.connected_gsconnect_devices() == [
        {"path": "/org/gnome/Shell/Extensions/GSConnect/Device/aaa", "name": "Pixel 9"}
    ]


def test_no_devices_when_gsconnect_is_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed `gdbus` call means no devices, not an error."""
    monkeypatch.setattr(phone_share.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(phone_share.subprocess, "run", _run_returning("", returncode=1))

    assert phone_share.connected_gsconnect_devices() == []


def test_a_connected_phone_gets_the_file_through_gsconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file is shared as a `file://` address by the share action."""
    picture = tmp_path / "m57.jpg"
    picture.write_bytes(b"jpeg")
    run = _run_returning(MANAGED_OBJECTS)
    popen = MagicMock()
    monkeypatch.setattr(phone_share.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(phone_share.subprocess, "run", run)
    monkeypatch.setattr(phone_share.subprocess, "Popen", popen)

    result = phone_share.send_file_to_phone(str(picture))

    assert result == {"method": "gsconnect", "device": "Pixel 9"}
    send_command = run.call_args.args[0]
    assert "org.gtk.Actions.Activate" in send_command
    assert "shareFile" in send_command
    assert f"[<('{picture.resolve().as_uri()}', false)>]" in send_command
    popen.assert_not_called()


def test_with_no_phone_connected_a_configuration_error_is_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no GSConnect phone, the user is told and no process starts."""
    popen = MagicMock()
    run = MagicMock()
    monkeypatch.setattr(phone_share, "connected_gsconnect_devices", lambda: [])
    monkeypatch.setattr(phone_share.subprocess, "Popen", popen)
    monkeypatch.setattr(phone_share.subprocess, "run", run)

    with pytest.raises(ConfigurationError, match="No phone is connected in GSConnect"):
        phone_share.send_file_to_phone("/stacks/m57.jpg")

    popen.assert_not_called()
    run.assert_not_called()
