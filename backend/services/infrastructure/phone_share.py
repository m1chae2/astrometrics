"""Purpose: Send a file to the user's phone without a window if possible.

Description: Tries GSConnect (the GNOME build of KDE Connect) first. It
sends the file to a paired, connected phone straight away, with nothing
shown on the computer. If no phone is connected, it opens the LocalSend
app with the file queued, and the user picks the phone there.
"""

import logging
import re
import shutil
import subprocess
from pathlib import Path

from astrometricslib import ConfigurationError, ExternalServiceError

__all__ = ["connected_gsconnect_devices", "send_file_to_phone"]

logger = logging.getLogger(__name__)

GSCONNECT_BUS_NAME = "org.gnome.Shell.Extensions.GSConnect"
"""The session-bus name the GSConnect extension answers on."""

GSCONNECT_ROOT_PATH = "/org/gnome/Shell/Extensions/GSConnect"
"""The object path under which GSConnect lists its devices."""

LOCALSEND_FLATPAK_ID = "org.localsend.localsend_app"
"""The Flatpak id of the LocalSend app."""

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
    if not gdbus:
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


def _open_localsend(path: str) -> None:
    """Open LocalSend with a file queued, for the user to send.

    A LocalSend program on the path is used first. Otherwise the Flatpak is
    started with file forwarding, which gives it access to this one file even
    though the Flatpak cannot see the rest of the disk.

    Parameters
    ----------
    path : `str`
        The file to queue.

    Raises
    ------
    ConfigurationError
        If LocalSend is not installed.
    ExternalServiceError
        If LocalSend could not be started.
    """
    program = shutil.which("localsend_app") or shutil.which("localsend")
    if program:
        command = [program, path]
    elif shutil.which("flatpak"):
        command = [
            "flatpak", "run", "--branch=stable", "--command=localsend",
            "--file-forwarding", LOCALSEND_FLATPAK_ID, "@@", path, "@@",
        ]  # fmt: skip
    else:
        raise ConfigurationError(
            "No phone link is available: connect a phone in GSConnect or install LocalSend."
        )
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        raise ExternalServiceError(f"Could not start LocalSend: {error}") from error


def send_file_to_phone(path: str) -> dict[str, str]:
    """Send a file to the user's phone.

    Sends it with GSConnect to the first connected, paired phone. With no
    such phone, opens LocalSend with the file queued instead.

    Parameters
    ----------
    path : `str`
        The file to send.

    Returns
    -------
    result : `dict` [`str`, `str`]
        ``method`` (``"gsconnect"`` or ``"localsend"``) and, for GSConnect,
        the phone's ``device`` name.

    Notes
    -----
    Raises `ConfigurationError` if no phone is connected and LocalSend is
    not installed, and `ExternalServiceError` if the phone link fails.
    """
    devices = connected_gsconnect_devices()
    if devices:
        _send_with_gsconnect(path, devices[0])
        return {"method": "gsconnect", "device": devices[0]["name"]}
    logger.info("No GSConnect phone is connected; opening LocalSend.")
    _open_localsend(path)
    return {"method": "localsend"}
