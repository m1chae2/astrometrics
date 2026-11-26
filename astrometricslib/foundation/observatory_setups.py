"""The optics an observatory owns, and which camera is used with which optic.

The config file lists each optic (a telescope or a camera lens) with its focal
length, and then lists the setups: the pairings of one camera with one
optic that are really used. For example::

    [Observatory.Optics]
    available = Apertura 75Q, Nikkor 300mm

    [Observatory.Optic.Apertura 75Q]
    focal_length_mm = 405
    focal_ratio = 5.4

    [Observatory.Setups]
    available = ASI533 on Apertura
    active = ASI533 on Apertura

    [Observatory.Setup.ASI533 on Apertura]
    camera = ZWO ASI533MM Pro
    optic = Apertura 75Q
    mount = Sky-Watcher EQ6-R
    filters = L, SPEC

A camera can appear in several setups, and an optic can be used by several
cameras. One setup is active: planning and telescope control use it.
Processing uses the setup a frame was taken with. The mount and the filters
are optional; they name the ``[Observatory.Mount.<name>]`` and
``[Observatory.Filter.<name>]`` sections that hold their equipment values.

This module only reads the config. Deciding which setup a frame came from is
done by `astrometricslib.pipelines.shared.frame_optics`.
"""

import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib.foundation.camera_names import normalize_camera_name
from astrometricslib.foundation.warn_once import warn_once

logger = logging.getLogger(__name__)


class OpticConfig(BaseModel):
    """One telescope or camera lens.

    Attributes
    ----------
    name : `str`
        The name the optic is listed under. It becomes the telescope name
        stored on each frame.
    focal_length_mm : `float`
        The focal length, in millimeters.
    focal_ratio : `float` or `None`
        The focal ratio (the f-number), or `None` when it is not given.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    focal_length_mm: float = Field(gt=0.0)
    focal_ratio: float | None = Field(default=None, gt=0.0)


class SetupConfig(BaseModel):
    """One pairing of a camera with an optic.

    Attributes
    ----------
    name : `str`
        The name the setup is listed under.
    camera_name : `str`
        The camera, written in any spelling the camera profiles recognise.
    optic_name : `str`
        The name of an optic listed in the config.
    mount_name : `str` or `None`
        The mount, naming an ``[Observatory.Mount.<name>]`` section, or `None`
        when the setup does not say.
    filter_names : `tuple` [`str`, ...]
        The filters and gratings used with this setup, each naming an
        ``[Observatory.Filter.<name>]`` section.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    camera_name: str = Field(min_length=1)
    optic_name: str = Field(min_length=1)
    mount_name: str | None = None
    filter_names: tuple[str, ...] = ()


class ObservatorySetups(BaseModel):
    """Every optic and every camera-and-optic pairing in the config.

    Attributes
    ----------
    optics : `tuple` [`OpticConfig`, ...]
        The optics listed in the config.
    setups : `tuple` [`SetupConfig`, ...]
        The pairings listed in the config. Each one refers to an optic that
        is in `optics`.
    active_setup_name : `str` or `None`
        The setup named by ``active`` in ``[Observatory.Setups]``, or `None`
        when the config names none.
    """

    model_config = ConfigDict(frozen=True)

    optics: tuple[OpticConfig, ...] = ()
    setups: tuple[SetupConfig, ...] = ()
    active_setup_name: str | None = None

    def setup_named(self, setup_name: str) -> SetupConfig | None:
        """Find a setup by name.

        Parameters
        ----------
        setup_name : `str`
            The name to look for.

        Returns
        -------
        setup : `SetupConfig` or `None`
            The setup, or `None` when no setup has that name.
        """
        return next((setup for setup in self.setups if setup.name == setup_name), None)

    def setup_for(
        self,
        camera_name: str,
        optic_name: str,
        camera_key: Callable[[str], str] = normalize_camera_name,
    ) -> SetupConfig | None:
        """Find the setup that pairs a camera with an optic.

        Parameters
        ----------
        camera_name : `str`
            The camera, in any spelling; spaces and case are ignored.
        optic_name : `str`
            The optic's name.
        camera_key : `Callable`, optional
            Turns a camera name into text that is the same for every
            spelling of the camera. The default ignores case, spaces and
            punctuation. A caller passes one that also knows the aliases
            listed in the camera profiles.

        Returns
        -------
        setup : `SetupConfig` or `None`
            The first setup with that camera and optic, or `None`.
        """
        wanted_key = camera_key(camera_name)
        return next(
            (
                setup
                for setup in self.setups
                if setup.optic_name == optic_name and camera_key(setup.camera_name) == wanted_key
            ),
            None,
        )

    def setup_for_focal_length(
        self,
        camera_name: str,
        focal_length_mm: float,
        camera_key: Callable[[str], str] = normalize_camera_name,
    ) -> SetupConfig | None:
        """Find the setup of a camera and an optic by focal length.

        A stack's header gives its camera and focal length but not the
        optic's name, so this finds the setup by those two.

        Parameters
        ----------
        camera_name : `str`
            The camera, in any spelling.
        focal_length_mm : `float`
            The focal length, in millimeters. It is compared to whole
            millimeters.
        camera_key : `Callable`, optional
            As for `setup_for`.

        Returns
        -------
        setup : `SetupConfig` or `None`
            The first setup with that camera whose optic has that focal
            length, or `None`.
        """
        wanted_key = camera_key(camera_name)
        for setup in self.setups:
            optic = self.optic_named(setup.optic_name)
            if (
                optic is not None
                and camera_key(setup.camera_name) == wanted_key
                and round(optic.focal_length_mm) == round(focal_length_mm)
            ):
                return setup
        return None

    def active_setup(self) -> SetupConfig | None:
        """Return the active setup.

        Returns
        -------
        setup : `SetupConfig` or `None`
            The setup named by ``active``; the only setup when the config
            lists exactly one and names none; otherwise `None`.
        """
        if self.active_setup_name is not None:
            return self.setup_named(self.active_setup_name)
        return self.setups[0] if len(self.setups) == 1 else None

    def optic_named(self, optic_name: str) -> OpticConfig | None:
        """Find an optic by name.

        Parameters
        ----------
        optic_name : `str`
            The name to look for.

        Returns
        -------
        optic : `OpticConfig` or `None`
            The optic, or `None` when no optic has that name.
        """
        return next((optic for optic in self.optics if optic.name == optic_name), None)


def _read_name_list(app_config: Any, section: str) -> list[str]:
    """Read the comma-separated ``available`` list of a config section.

    Parameters
    ----------
    app_config : `Any`
        The loaded config, offering ``get_value(section, key, fallback)``.
    section : `str`
        The section holding the ``available`` key.

    Returns
    -------
    names : `list` [`str`]
        The names, with spaces trimmed and empty entries removed.
    """
    text = app_config.get_value(section, "available", "") or ""
    return [name.strip() for name in str(text).split(",") if name.strip()]


def _read_optic(app_config: Any, optic_name: str) -> OpticConfig | None:
    """Read one optic's section.

    Parameters
    ----------
    app_config : `Any`
        The loaded config.
    optic_name : `str`
        The name of the optic.

    Returns
    -------
    optic : `OpticConfig` or `None`
        The optic, or `None` (after one warning) when its section is missing
        or its numbers are not valid.
    """
    section = f"Observatory.Optic.{optic_name}"
    focal_length_text = app_config.get_value(section, "focal_length_mm", None)
    focal_ratio_text = app_config.get_value(section, "focal_ratio", None)
    if focal_length_text is None:
        warn_once(
            logger, f"The config lists the optic {optic_name!r}, but [{section}] has no focal_length_mm."
        )
        return None
    try:
        return OpticConfig(
            name=optic_name,
            focal_length_mm=float(focal_length_text),
            focal_ratio=float(focal_ratio_text) if focal_ratio_text not in (None, "") else None,
        )
    except ValueError as problem:
        warn_once(
            logger, f"The config lists the optic {optic_name!r}, but [{section}] is not usable ({problem})."
        )
        return None


def load_observatory_setups(app_config: Any) -> ObservatorySetups:
    """Read the optics and setups from the config.

    An optic or setup that is listed but cannot be used (a missing or invalid
    section, or a setup naming an optic that does not exist) is skipped with
    one warning, so a typo does not stop a scan.

    Parameters
    ----------
    app_config : `Any`
        The loaded config, offering ``get_value(section, key, fallback)``.

    Returns
    -------
    observatory_setups : `ObservatorySetups`
        What the config describes. It is empty when the config has no
        ``[Observatory.Optics]`` or ``[Observatory.Setups]`` section.
    """
    optics = tuple(
        optic
        for optic in (
            _read_optic(app_config, name) for name in _read_name_list(app_config, "Observatory.Optics")
        )
        if optic is not None
    )
    known_optic_names = {optic.name for optic in optics}

    setups = []
    for setup_name in _read_name_list(app_config, "Observatory.Setups"):
        section = f"Observatory.Setup.{setup_name}"
        camera_name = (app_config.get_value(section, "camera", "") or "").strip()
        optic_name = (app_config.get_value(section, "optic", "") or "").strip()
        if not camera_name or optic_name not in known_optic_names:
            warn_once(
                logger,
                f"The config lists the setup {setup_name!r}, but [{section}] needs a camera and the "
                f"name of an optic that is listed under [Observatory.Optics] (got camera={camera_name!r}, "
                f"optic={optic_name!r}). The setup is ignored.",
            )
            continue
        mount_name = (app_config.get_value(section, "mount", "") or "").strip() or None
        filter_text = app_config.get_value(section, "filters", "") or ""
        filter_names = tuple(name.strip() for name in str(filter_text).split(",") if name.strip())
        setups.append(
            SetupConfig(
                name=setup_name,
                camera_name=camera_name,
                optic_name=optic_name,
                mount_name=mount_name,
                filter_names=filter_names,
            )
        )

    active_text = (app_config.get_value("Observatory.Setups", "active", "") or "").strip()
    active_setup_name = active_text or None
    if active_setup_name is not None and active_setup_name not in {setup.name for setup in setups}:
        warn_once(
            logger,
            f"[Observatory.Setups] names {active_setup_name!r} as active, but no usable setup has that "
            "name. No setup is active.",
        )
        active_setup_name = None
    return ObservatorySetups(optics=optics, setups=tuple(setups), active_setup_name=active_setup_name)
