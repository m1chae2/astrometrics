"""Purpose: Safety checks for the simulated observatory.

Description: A simulated observatory gives the app a telescope that is "on".
Every piece of that telescope (INDI server, PHD2, the telescope computer's
folders) must be a local stand-in that the simulator started, and every
library the app writes must be inside the simulator's own folder. This module
holds the checks that keep it so:

- `check_settings` and `check_settings_text` read a settings file and refuse
  it unless every host is this computer, the INDI and PHD2 ports are the
  ones the simulator started, the picture folder is inside the stand-in
  telescope computer, and the image library, frames, stacks and wayfinding
  library folders are all inside the simulator's folder and none is, or holds,
  one of the user's real library folders. No text in the file may name
  another network address (for example the real StellarMate).
- `check_settings_update` makes the same checks on a settings save that has
  not been written yet.

The checks that watch a running process tree and the network seal belong to
the test harness, which lives in ``build/tpm/``.
"""

import ipaddress
import os
import re
import socket
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path

from astrometricslib.drivers import real_folders
from wayfindinglib.exceptions import GuardError
from wayfindinglib.simulation.settings import OFFLINE_PORT

#: Hosts that mean this computer.
LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1")
#: Ports a simulator never uses for a stand-in: the real backend, a real PHD2
#: and INDI's usual port.
FORBIDDEN_PORTS = (5000, 4400, 7624)
#: Any IPv4 address written anywhere in the settings file.
_IPV4_TEXT = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
#: The folder settings the simulator must keep inside its own folder:
#: ``(section, key, required in a whole settings file)``. The stacks folder
#: may be left out, which means "beside the frames".
LIBRARY_PATH_SETTINGS = (
    ("Image Library", "path", True),
    ("Image Library", "frames_path", True),
    ("Image Library", "stacks_path", False),
    ("Wayfinding Library", "path", True),
)

__all__ = [
    "FORBIDDEN_PORTS",
    "LIBRARY_PATH_SETTINGS",
    "LOOPBACK_NAMES",
    "GuardError",
    "check_offline_settings_text",
    "check_offline_settings_update",
    "check_root",
    "check_settings",
    "check_settings_text",
    "check_settings_update",
    "is_loopback_address",
    "real_protected_paths",
]


def is_loopback_address(text: str) -> bool:
    """Say whether an address text is a loopback address.

    Parameters
    ----------
    text : `str`
        An IP address, possibly in brackets or with an IPv4-mapped prefix.

    Returns
    -------
    is_loopback : `bool`
        `True` for 127.x.x.x, ::1 and ::ffff:127.x.x.x.
    """
    cleaned = text.strip("[]").split("%")[0]
    try:
        address = ipaddress.ip_address(cleaned)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


def _require_loopback_host(name: str, host: str) -> None:
    """Refuse a host that is not this computer.

    The check uses the name and what the name resolves to.

    Parameters
    ----------
    name : `str`
        The setting's name, for the message.
    host : `str`
        The host text from the settings file.

    Raises
    ------
    GuardError
        If the host is not a loopback name or resolves to an address that
        is not loopback.
    """
    if host not in LOOPBACK_NAMES:
        raise GuardError(f"{name} is '{host}'; a simulated observatory allows only 127.0.0.1 or localhost.")
    try:
        resolved = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError as error:
        raise GuardError(f"{name} '{host}' does not resolve: {error}") from error
    bad = [address for address in resolved if not is_loopback_address(address)]
    if bad:
        raise GuardError(f"{name} '{host}' resolves to {', '.join(sorted(bad))}, which is not this computer.")


def real_protected_paths(environ: Mapping[str, str] | None = None) -> tuple[Path, ...]:
    """Give the user's real folders that a simulator must never use.

    Inside the app's simulator world the ``ASTROMETRICS_REAL_*`` variables
    name the real folders, and `real_folders` reads them. A test harness that
    has already moved every folder into a throwaway folder sets none of
    them, so its own settings file must not be taken for the real one: the
    real folders are then the defaults under the user's home.

    Parameters
    ----------
    environ : `~collections.abc.Mapping` [`str`, `str`], optional
        The environment. Default: the process environment.

    Returns
    -------
    paths : `tuple` [`pathlib.Path`]
        The real library, frames, stacks, wayfinding, data and settings
        folders, resolved.
    """
    environment = os.environ if environ is None else environ
    names = ("ASTROMETRICS_REAL_CONFIG_HOME", "ASTROMETRICS_REAL_DATA_HOME", "ASTROMETRICS_REAL_CONFIG_PATH")
    if any(environment.get(name) for name in names):
        return real_folders(environment).protected_paths()
    return real_folders({}).protected_paths()


def _check_library_path(name: str, text: str, root: Path, protected: Iterable[Path]) -> None:
    """Refuse a library folder that is outside the simulator or is a real one.

    Parameters
    ----------
    name : `str`
        The setting's name, for the message.
    text : `str`
        The folder as written in the settings.
    root : `pathlib.Path`
        The simulator's folder.
    protected : `~collections.abc.Iterable` [`pathlib.Path`]
        The user's real folders, resolved.

    Raises
    ------
    GuardError
        If the folder is empty, relative, not inside `root` (after links are
        followed), or the same as, inside, or around a real folder.
    """
    if not text.strip():
        raise GuardError(f"{name} is empty; it must name a folder inside the simulator folder {root}.")
    path = Path(text)
    if not path.is_absolute():
        raise GuardError(f"{name} '{text}' is not an absolute path.")
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise GuardError(f"{name} {resolved} is not inside the simulator folder {root}.")
    for real in protected:
        if resolved.is_relative_to(real) or real.is_relative_to(resolved):
            raise GuardError(f"{name} {resolved} is, or holds, the real folder {real}.")


def _check_library_paths(settings: Mapping, root: Path, protected: Iterable[Path], whole_file: bool) -> None:
    """Check every library folder setting that is present.

    Parameters
    ----------
    settings : `~collections.abc.Mapping`
        Parsed settings, one mapping per section.
    root : `pathlib.Path`
        The simulator's folder.
    protected : `~collections.abc.Iterable` [`pathlib.Path`]
        The user's real folders.
    whole_file : `bool`
        `True` when `settings` is a whole settings file, so the folders that
        must be named have to be there; `False` for a save that changes only
        some keys.

    Raises
    ------
    GuardError
        On the first folder that fails.
    """
    protected = tuple(protected)
    for section, key, required in LIBRARY_PATH_SETTINGS:
        values = settings.get(section, {})
        values = values if isinstance(values, Mapping) else {}
        if key not in values:
            if whole_file and required:
                raise GuardError(f"[{section}] {key} is not set; it must name a folder inside {root}.")
            continue
        _check_library_path(f"[{section}] {key}", str(values[key]), root, protected)


def check_root(root: Path, protected: Iterable[Path] | None = None) -> None:
    """Refuse a simulator folder that is, or is inside, a real folder.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's root folder.
    protected : `~collections.abc.Iterable` [`pathlib.Path`], optional
        The user's real folders. Default: `real_protected_paths`.

    Raises
    ------
    GuardError
        If `root` is the same as, inside, or around a real folder.
    """
    resolved = root.resolve()
    for real in real_protected_paths() if protected is None else protected:
        if resolved.is_relative_to(real) or real.is_relative_to(resolved):
            raise GuardError(
                f"The simulator folder {resolved} is, holds, or is inside the real folder {real}."
            )


def check_offline_settings_text(text: str, root: Path, protected: Iterable[Path] | None = None) -> None:
    """Refuse offline settings that name a telescope or a real library.

    Parameters
    ----------
    text : `str`
        The settings in TOML.
    root : `pathlib.Path`
        The simulator's root folder.
    protected : `~collections.abc.Iterable` [`pathlib.Path`], optional
        The user's real folders. Default: `real_protected_paths`.

    Raises
    ------
    GuardError
        If the telescope host is not this computer, INDI or PHD2 has a port
        other than the offline one, automatic downloads are on, or a library
        folder is outside `root` or is a real folder.
    """
    for address in _IPV4_TEXT.findall(text):
        if not is_loopback_address(address):
            raise GuardError(f"The settings file names the address {address}, which is not this computer.")
    try:
        settings = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise GuardError(f"The settings file cannot be read: {error}") from error
    telescope = settings.get("Observatory.Telescope", {})
    guiding = settings.get("Observatory.Guiding", {})
    _require_loopback_host("[Observatory.Telescope] hostname", str(telescope.get("hostname", "")))
    for name, value in (
        ("[Observatory.Telescope] indi_port", telescope.get("indi_port")),
        ("[Observatory.Guiding] phd2_port", guiding.get("phd2_port")),
    ):
        if str(value) != str(OFFLINE_PORT):
            raise GuardError(f"{name} is {value}; the offline world uses only port {OFFLINE_PORT}.")
    transfer = settings.get("Observatory.RemoteTransfer", {})
    if str(transfer.get("auto_download", "false")).lower() != "false":
        raise GuardError("[Observatory.RemoteTransfer] auto_download must be false in the offline world.")
    _check_library_paths(
        settings, root, real_protected_paths() if protected is None else protected, whole_file=True
    )


def check_offline_settings_update(update: dict, root: Path, protected: Iterable[Path] | None = None) -> None:
    """Refuse a settings save that would give the offline world a telescope.

    Parameters
    ----------
    update : `dict`
        The settings to save, one dict per section.
    root : `pathlib.Path`
        The simulator's root folder.
    protected : `~collections.abc.Iterable` [`pathlib.Path`], optional
        The user's real folders. Default: `real_protected_paths`.

    Raises
    ------
    GuardError
        If the save names another host, another INDI or PHD2 port, turns
        automatic downloads on, or moves a library folder out of `root`.
    """
    telescope = dict(update.get("Observatory.Telescope", {}))
    guiding = dict(update.get("Observatory.Guiding", {}))
    transfer = dict(update.get("Observatory.RemoteTransfer", {}))
    for section, values in update.items():
        for key, value in dict(values).items():
            for address in _IPV4_TEXT.findall(str(value)):
                if not is_loopback_address(address):
                    raise GuardError(f"[{section}] {key} names {address}, which is not this computer.")
    if "hostname" in telescope:
        _require_loopback_host("[Observatory.Telescope] hostname", str(telescope["hostname"]))
    for name, values, key in (
        ("[Observatory.Telescope] indi_port", telescope, "indi_port"),
        ("[Observatory.Guiding] phd2_port", guiding, "phd2_port"),
    ):
        if key in values and str(values[key]) != str(OFFLINE_PORT):
            raise GuardError(f"{name} must stay {OFFLINE_PORT} while the simulator is offline.")
    if "phd2_location" in guiding and str(guiding["phd2_location"]).lower() != "local":
        raise GuardError("[Observatory.Guiding] phd2_location must stay 'local' while offline.")
    if str(transfer.get("auto_download", "false")).lower() != "false":
        raise GuardError("[Observatory.RemoteTransfer] auto_download must stay false while offline.")
    _check_library_paths(
        {section: dict(values) for section, values in update.items()},
        root,
        real_protected_paths() if protected is None else protected,
        whole_file=False,
    )


def check_settings(
    config_path: Path,
    indi_port: int,
    phd2_port: int,
    standin: Path,
    sandbox: Path,
    protected: Iterable[Path] | None = None,
) -> None:
    """Refuse a settings file that points beyond the simulator's stand-ins.

    Parameters
    ----------
    config_path : `pathlib.Path`
        The simulator's settings file.
    indi_port : `int`
        The port of the INDI server the simulator started.
    phd2_port : `int`
        The port of the stand-in PHD2 the simulator started.
    standin : `pathlib.Path`
        The stand-in telescope computer's home folder.
    sandbox : `pathlib.Path`
        The simulator's folder.
    protected : `~collections.abc.Iterable` [`pathlib.Path`], optional
        The user's real folders. Default: `real_protected_paths`.

    Notes
    -----
    A problem raises `GuardError`, as in `check_settings_text`.
    """
    check_settings_text(
        config_path.read_text(encoding="utf-8"), indi_port, phd2_port, standin, sandbox, protected
    )


def check_settings_text(
    text: str,
    indi_port: int,
    phd2_port: int,
    standin: Path,
    sandbox: Path,
    protected: Iterable[Path] | None = None,
) -> None:
    """Refuse settings text that points beyond the stand-ins.

    The same checks as `check_settings`, on text that is not yet saved.

    Parameters
    ----------
    text : `str`
        The settings in TOML.
    indi_port, phd2_port : `int`
        The ports of the stand-ins that were started.
    standin : `pathlib.Path`
        The stand-in telescope computer's home folder.
    sandbox : `pathlib.Path`
        The simulator's folder.
    protected : `~collections.abc.Iterable` [`pathlib.Path`], optional
        The user's real folders. Default: `real_protected_paths`.

    Raises
    ------
    GuardError
        On the first problem found.
    """
    for address in _IPV4_TEXT.findall(text):
        if not is_loopback_address(address):
            raise GuardError(f"The settings file names the address {address}, which is not this computer.")
    try:
        settings = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise GuardError(f"The settings file cannot be read: {error}") from error
    telescope = settings.get("Observatory.Telescope", {})
    guiding = settings.get("Observatory.Guiding", {})
    transfer = settings.get("Observatory.RemoteTransfer", {})
    _require_loopback_host("[Observatory.Telescope] hostname", str(telescope.get("hostname", "")))
    if int(telescope.get("indi_port", 0)) != indi_port:
        raise GuardError(
            f"[Observatory.Telescope] indi_port is {telescope.get('indi_port')}, "
            f"not the simulator's {indi_port}."
        )
    if str(guiding.get("phd2_location", "")).lower() != "local":
        raise GuardError("[Observatory.Guiding] phd2_location must be 'local' in a simulated observatory.")
    if int(guiding.get("phd2_port", 0)) != phd2_port:
        raise GuardError(
            f"[Observatory.Guiding] phd2_port is {guiding.get('phd2_port')}, not the simulator's {phd2_port}."
        )
    for port in (indi_port, phd2_port):
        if port in FORBIDDEN_PORTS:
            raise GuardError(f"Port {port} is one of the real ports {FORBIDDEN_PORTS}.")
    pictures = Path(str(telescope.get("remote_pictures_path", ""))).resolve()
    if not pictures.is_relative_to(standin.resolve()):
        raise GuardError(f"remote_pictures_path {pictures} is not inside the stand-in folder {standin}.")
    if not standin.resolve().is_relative_to(sandbox.resolve()):
        raise GuardError(f"The stand-in folder {standin} is not inside the simulator folder {sandbox}.")
    if str(transfer.get("driver", "stellarmate")) != "stellarmate":
        raise GuardError(
            "[Observatory.RemoteTransfer] driver must stay 'stellarmate' so the one real transfer code runs."
        )
    _check_library_paths(
        settings, sandbox, real_protected_paths() if protected is None else protected, whole_file=True
    )


def check_settings_update(
    update: dict,
    indi_port: int,
    phd2_port: int,
    standin: Path,
    root: Path,
    protected: Iterable[Path] | None = None,
) -> None:
    """Refuse a settings change that would aim a simulator at a real device.

    Only the keys present in `update` are checked, because a save changes
    only those.

    Parameters
    ----------
    update : `dict`
        The settings to save, one dict per section.
    indi_port, phd2_port : `int`
        The ports of the stand-ins that were started.
    standin : `pathlib.Path`
        The stand-in telescope computer's home folder.
    root : `pathlib.Path`
        The simulator's folder. Every library folder must stay inside it.
    protected : `~collections.abc.Iterable` [`pathlib.Path`], optional
        The user's real folders. Default: `real_protected_paths`.

    Raises
    ------
    GuardError
        On the first problem found.
    """
    for section, values in update.items():
        for key, value in dict(values).items():
            for address in _IPV4_TEXT.findall(str(value)):
                if not is_loopback_address(address):
                    raise GuardError(f"[{section}] {key} names {address}, which is not this computer.")
    telescope = dict(update.get("Observatory.Telescope", {}))
    guiding = dict(update.get("Observatory.Guiding", {}))
    transfer = dict(update.get("Observatory.RemoteTransfer", {}))
    if "hostname" in telescope:
        _require_loopback_host("[Observatory.Telescope] hostname", str(telescope["hostname"]))
    if "indi_port" in telescope and int(telescope["indi_port"]) != indi_port:
        raise GuardError(f"[Observatory.Telescope] indi_port must stay {indi_port} in the simulator.")
    if "phd2_location" in guiding and str(guiding["phd2_location"]).lower() != "local":
        raise GuardError("[Observatory.Guiding] phd2_location must stay 'local' in the simulator.")
    if "phd2_port" in guiding and int(guiding["phd2_port"]) != phd2_port:
        raise GuardError(f"[Observatory.Guiding] phd2_port must stay {phd2_port} in the simulator.")
    if "remote_pictures_path" in telescope:
        pictures = Path(str(telescope["remote_pictures_path"])).resolve()
        if not pictures.is_relative_to(standin.resolve()):
            raise GuardError(f"remote_pictures_path must stay inside the stand-in folder {standin}.")
    if "driver" in transfer and str(transfer["driver"]) != "stellarmate":
        raise GuardError("[Observatory.RemoteTransfer] driver must stay 'stellarmate' in the simulator.")
    _check_library_paths(
        {section: dict(values) for section, values in update.items()},
        root,
        real_protected_paths() if protected is None else protected,
        whole_file=False,
    )
