"""Purpose: Send a file to the user's phone without a window.

Description: Uses GSConnect (the GNOME build of KDE Connect). It sends the
file to a paired, connected phone straight away, with nothing shown on the
computer. If no phone is connected, the send fails with a clear message.
"""

import re
import shutil
import subprocess
from pathlib import Path

from astrometricslib.drivers import network_is_offline
from astrometricslib.exceptions import ConfigurationError, ExternalServiceError

__all__ = ["connected_gsconnect_devices", "send_file_to_phone"]

GSCONNECT_BUS_NAME = "org.gnome.Shell.Extensions.GSConnect"
"""The session-bus name the GSConnect extension answers on."""

GSCONNECT_ROOT_PATH = "/org/gnome/Shell/Extensions/GSConnect"
"""The object path under which GSConnect lists its devices."""

NO_PHONE_MESSAGE = "No phone is connected in GSConnect. Open KDE Connect on your phone and try again."
"""What the user sees when no paired phone is connected."""

DBUS_TIMEOUT_SECONDS = 10
"""How long to wait for the session bus to answer."""

_DEVICE_PATTERN = re.compile(r"objectpath '([^']+/Device/[^']+)'(.*?)(?=objectpath '|\Z)", re.DOTALL)
"""One device entry in the text `gdbus` prints for the managed objects."""


def connected_gsconnect_devices() -> list[dict[str, str]]:
    """List the phones GSConnect has paired and can reach now.

    Returns
    -------
    devices : `list` [`dict`]
        One entry per device, with ``path`` (its bus object path) and
        ``name``. Empty if GSConnect is not running or `gdbus` is missing.
    """
    gdbus = shutil.which("gdbus")
    if not gdbus or network_is_offline():
        return []
    command = [
        gdbus, "call", "--session", "--dest", GSCONNECT_BUS_NAME,
        "--object-path", GSCONNECT_ROOT_PATH,
        "--method", "org.freedesktop.DBus.ObjectManager.GetManagedObjects",
    ]  # fmt: skip
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=DBUS_TIMEOUT_SECONDS, check=False
        )
    except OSError, subprocess.SubprocessError:
        return []
    if result.returncode != 0:
        return []
    devices = []
    for object_path, properties in _DEVICE_PATTERN.findall(result.stdout):
        name = re.search(r"'Name': <'([^']*)'>", properties)
        if "'Connected': <true>" in properties and "'Paired': <true>" in properties and name:
            devices.append({"path": object_path, "name": name.group(1)})
    return devices


def _send_with_gsconnect(path: str, device: dict[str, str]) -> None:
    """Ask GSConnect to share a file with one phone.

    Parameters
    ----------
    path : `str`
        The file to send.
    device : `dict` [`str`, `str`]
        A phone from `connected_gsconnect_devices`.

    Raises
    ------
    ExternalServiceError
        If GSConnect did not accept the request.
    """
    uri = Path(path).resolve().as_uri()
    command = [
        "gdbus", "call", "--session", "--dest", GSCONNECT_BUS_NAME,
        "--object-path", device["path"], "--method", "org.gtk.Actions.Activate",
        "shareFile", f"[<('{uri}', false)>]", "{}",
    ]  # fmt: skip
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=DBUS_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ExternalServiceError(f"Could not reach GSConnect: {error}") from error
    if result.returncode != 0:
        raise ExternalServiceError(f"GSConnect could not send the file: {result.stderr.strip()}")


def send_file_to_phone(path: str) -> dict[str, str]:
    """Send a file to the user's phone with GSConnect.

    Sends it to the first connected, paired phone.

    Parameters
    ----------
    path : `str`
        The file to send.

    Returns
    -------
    result : `dict` [`str`, `str`]
        ``method`` (always ``"gsconnect"``) and the phone's ``device`` name.

    Raises
    ------
    ConfigurationError
        If the launch sends nothing off the computer, or if no phone is
        connected in GSConnect.

    Notes
    -----
    The helpers raise `ExternalServiceError` if the phone link fails.
    """
    if network_is_offline():
        raise ConfigurationError(
            "The phone link is not available: this launch sends nothing off the computer.",
            details={"offline": True},
        )
    devices = connected_gsconnect_devices()
    if not devices:
        raise ConfigurationError(NO_PHONE_MESSAGE)
    _send_with_gsconnect(path, devices[0])
    return {"method": "gsconnect", "device": devices[0]["name"]}
