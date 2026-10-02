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

    [Observatory.Setup.ASI533 on Apertura]
    camera = ZWO ASI533MM Pro
    optic = Apertura 75Q

A camera can appear in several setups, and an optic can be used by several
cameras. This module only reads the config. Deciding which setup a frame
came from is done by `astrometricslib.pipelines.shared.frame_optics`.
"""

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib.utilities.warn_once import warn_once

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
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    camera_name: str = Field(min_length=1)
    optic_name: str = Field(min_length=1)


class ObservatorySetups(BaseModel):
    """Every optic and every camera-and-optic pairing in the config.

    Attributes
    ----------
    optics : `tuple` [`OpticConfig`, ...]
        The optics listed in the config.
    setups : `tuple` [`SetupConfig`, ...]
        The pairings listed in the config. Each one refers to an optic that
        is in `optics`.
    """

    model_config = ConfigDict(frozen=True)

    optics: tuple[OpticConfig, ...] = ()
    setups: tuple[SetupConfig, ...] = ()

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
        setups.append(SetupConfig(name=setup_name, camera_name=camera_name, optic_name=optic_name))

    return ObservatorySetups(optics=optics, setups=tuple(setups))
