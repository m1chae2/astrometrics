"""Purpose: Read the launch identity, the facts a launcher states about a run.

Description: A program that starts the app (a launcher) may add a read-only
``[Launch]`` section to the settings file it starts the app with. The section
says what kind of run this is, so the app can show it and respect it. It
changes nothing by itself: the app never builds or removes anything from it.
The keys are::

    [Launch]
    label = "SIMULATOR"        # text for the status chip; empty for none
    live_hardware = false      # true when real devices can be commanded
    disposable_library = true  # true when the libraries can be thrown away
    offline = true             # true when nothing may leave this computer
    locked_keys = ["Observatory.Telescope.hostname"]  # "Section.key" names

A settings file with no ``[Launch]`` section describes an ordinary run, and
`get_launch_identity` gives `None` for it. This module is the one place that
parses the section. `LaunchIdentity.offline` is honored by every outgoing
network client (see `astrometricslib.foundation.offline`).

``offline = true`` means that nothing leaves this computer. That includes the
local network: a telescope computer, a phone link and the internet are all
refused, and only this computer itself (``localhost``) stays reachable. A
setup that wants the local network but not the internet would need a separate
switch, which does not exist yet.
"""

import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

LAUNCH_SECTION = "Launch"

#: Texts that clearly mean "no" for the ``offline`` key. Any other value
#: counts as offline, so a typo keeps the run closed instead of open.
_CLEARLY_FALSE = ("false", "no", "0", "off")

#: A ``[Launch]`` header line, with an optional comment after it. It is used
#: to tell whether a file that cannot be parsed had a ``[Launch]`` section.
_LAUNCH_HEADER = re.compile(r"^\s*\[\s*Launch\s*\]\s*(?:#.*)?$", re.MULTILINE)


class LaunchIdentity(BaseModel):
    """What a launcher says about this run.

    Attributes
    ----------
    label : `str`
        Text for the status chip, for example ``SIMULATOR``. Empty for a run
        that needs no chip.
    live_hardware : `bool`
        Whether real devices can be commanded in this run. It is `True`
        when the section does not say, because that is the safe assumption.
    disposable_library : `bool`
        Whether the libraries of this run can be thrown away. Backups skip a
        disposable library.
    offline : `bool`
        Whether nothing may leave this computer. Every outgoing client
        refuses, except a connection to this computer itself.
    locked_keys : `tuple` [`str`, ...]
        Settings that cannot be changed while the app runs, written
        ``Section.key``.
    """

    model_config = ConfigDict(frozen=True)

    label: str = ""
    live_hardware: bool = True
    disposable_library: bool = False
    offline: bool = False
    locked_keys: tuple[str, ...] = Field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        """Give the identity as plain JSON-safe values.

        Returns
        -------
        description : `dict`
            ``label`` (`str`), ``live_hardware``, ``disposable_library`` and
            ``offline`` (`bool`), and ``locked_keys`` (`list` of `str`).
        """
        return {
            "label": self.label,
            "live_hardware": self.live_hardware,
            "disposable_library": self.disposable_library,
            "offline": self.offline,
            "locked_keys": list(self.locked_keys),
        }

    def is_locked(self, section: str, key: str) -> bool:
        """Say whether a setting is locked.

        Parameters
        ----------
        section, key : `str`
            The setting's section and key.

        Returns
        -------
        locked : `bool`
            `True` if ``section.key`` is in `locked_keys`.
        """
        return f"{section}.{key}" in self.locked_keys


def _as_bool(value: object, default: bool) -> bool:
    """Read a TOML value that may be a bool or text such as ``"true"``.

    Parameters
    ----------
    value : `object`
        The value, or `None` when the key is missing.
    default : `bool`
        What to give for a missing or unreadable value.

    Returns
    -------
    flag : `bool`
        The interpreted value.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _as_offline(value: object) -> bool:
    """Read the ``offline`` key so that a doubtful value means offline.

    Parameters
    ----------
    value : `object`
        The value, or `None` when the key is missing.

    Returns
    -------
    offline : `bool`
        `False` when the key is missing or clearly false (`False`,
        ``"false"``, ``"no"``, ``"0"`` or ``"off"``, in any letter case).
        `True` for every other value, because a launch that wanted to stay
        online would have written one of those.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in _CLEARLY_FALSE


def _unreadable_section_identity() -> LaunchIdentity:
    """Give the identity for a ``[Launch]`` section that cannot be read.

    Returns
    -------
    identity : `LaunchIdentity`
        An identity with ``offline`` true and everything else at its default.
    """
    return LaunchIdentity(offline=True)


def _as_names(value: object) -> tuple[str, ...]:
    """Read a list of names given as a TOML array or as comma-separated text.

    Parameters
    ----------
    value : `object`
        The value, or `None` when the key is missing.

    Returns
    -------
    names : `tuple` [`str`, ...]
        The non-empty names, stripped.
    """
    if value is None:
        return ()
    items = value.split(",") if isinstance(value, str) else list(value)  # type: ignore[call-overload]
    return tuple(text for text in (str(item).strip() for item in items) if text)


def launch_identity_from_settings(settings: Mapping[str, Any]) -> LaunchIdentity | None:
    """Read the ``[Launch]`` section out of parsed settings.

    Parameters
    ----------
    settings : `~collections.abc.Mapping`
        Settings as sections of keys (a parsed TOML file).

    Returns
    -------
    identity : `LaunchIdentity` or `None`
        The identity, or `None` when there is no ``[Launch]`` section. A
        section that is there but is not a table of keys, or whose keys
        cannot be read, gives an identity with ``offline`` true: when the
        launcher's statement is unreadable, the run stays closed. The
        ``offline`` key itself is true unless it is missing or clearly false
        (see `_as_offline`).
    """
    if LAUNCH_SECTION not in settings:
        return None
    section = settings[LAUNCH_SECTION]
    if not isinstance(section, Mapping):
        return _unreadable_section_identity()
    try:
        return LaunchIdentity(
            label=str(section.get("label", "") or "").strip(),
            live_hardware=_as_bool(section.get("live_hardware"), True),
            disposable_library=_as_bool(section.get("disposable_library"), False),
            offline=_as_offline(section.get("offline")),
            locked_keys=_as_names(section.get("locked_keys")),
        )
    except TypeError, ValueError:
        return _unreadable_section_identity()


def read_launch_identity(config_file: str | Path) -> LaunchIdentity | None:
    """Read the launch identity from a settings file on disk.

    Parameters
    ----------
    config_file : `str` or `pathlib.Path`
        The settings file.

    Returns
    -------
    identity : `LaunchIdentity` or `None`
        The identity, or `None` when the file is missing or has no
        ``[Launch]`` section. A file that cannot be parsed but has a
        ``[Launch]`` header gives an identity with ``offline`` true.
    """
    try:
        text = Path(config_file).read_text(encoding="utf-8")
    except OSError, ValueError:
        return None
    try:
        settings = tomllib.loads(text)
    except ValueError:
        return _unreadable_section_identity() if _LAUNCH_HEADER.search(text) else None
    return launch_identity_from_settings(settings)
