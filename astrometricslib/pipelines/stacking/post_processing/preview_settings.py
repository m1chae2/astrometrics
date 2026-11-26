"""Holds the stack preview's settings and says when a preview is out of date.

`PreviewSettings` holds the choices for one preview run that replace the
saved configuration. `preview_configuration` collects the configuration
values that change how the preview looks. After each run they are written
to a small JSON file beside the stack (`write_preview_settings`), and
`preview_is_out_of_date` compares that file with the current configuration,
so the app can offer to remake a preview whose settings changed.
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from astrometricslib.foundation.config import get_configuration
from astrometricslib.foundation.parameters import resolve
from astrometricslib.pipelines.shared.stack_preview_path import (
    preview_path_for,
    preview_settings_path_for,
)
from astrometricslib.pipelines.stacking.post_processing.sky_plane import (
    SKY_PLANE_GRADIENT_LIMIT,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreviewSettings:
    """Choices for one preview run that replace the saved settings.

    Each field left as `None` uses the app's configured value. The values
    apply to the run they are given to and are never written to the
    configuration.

    Attributes
    ----------
    denoise : `bool` or `None`
        `False` skips Cosmic Clarity even if it is configured. `True` runs it
        even if the settings have it switched off (it cannot run without its
        program path).
    denoise_strength : `float` or `None`
        Cosmic Clarity's denoise strength for this run.
    star_toning : `bool` or `None`
        Turn the star toning on or off for this run.
    starnet : `bool`
        Stretch the stars and the rest of the picture on their own, using
        StarNet through Siril (see `starnet_stretch`). Off by default. It is
        slow, and a failed run falls back to the normal preview. The star
        toning is not used with it.
    star_strength : `float` or `None`
        With StarNet, how much of the stars layer is blended, from 0 to 1.
        `None` uses `DEFAULT_STAR_STRENGTH`.
    star_stretch : `float` or `None`
        With StarNet, the arcsinh strength of the stars' own stretch. `None`
        uses `DEFAULT_STAR_STRETCH`.
    """

    denoise: bool | None = None
    denoise_strength: float | None = None
    star_toning: bool | None = None
    starnet: bool = False
    star_strength: float | None = None
    star_stretch: float | None = None


def preview_configuration() -> dict[str, Any]:
    """Give the saved settings that shape a stack's preview picture.

    These are the settings `_picture_script` reads: the GraXpert and Cosmic
    Clarity programs, the denoise strength, the star width matching, the
    colour calibration, Remove Green Noise and star toning. Program paths are
    kept as text, with an empty string for a program that is not set up.

    Returns
    -------
    configuration : `dict` [`str`, `Any`]
        Setting name -> value, ready to save as JSON.
    """
    configuration = get_configuration()
    return {
        "graxpert_executable": str(configuration.get_graxpert_executable() or ""),
        "cosmic_clarity_executable": str(configuration.get_cosmic_clarity_denoise_executable() or ""),
        "cosmic_clarity_strength": float(configuration.get_cosmic_clarity_denoise_strength()),
        "colour_calibration": bool(configuration.get_preview_colour_calibration_enabled()),
        "colour_calibration_method": str(configuration.get_preview_colour_calibration_method()),
        "remove_green_noise": bool(configuration.get_preview_remove_green_noise_enabled()),
        "star_toning": bool(configuration.get_preview_star_tone_enabled()),
        "star_width_match": bool(configuration.get_preview_star_width_match_enabled()),
        # A change of the limit changes which stacks get a plane instead of
        # GraXpert, so a stack's picture is made again when it changes. A
        # record saved before the gate lacks the key, which also counts as a
        # change.
        "background_plane_limit": float(resolve(SKY_PLANE_GRADIENT_LIMIT).value),
    }


def preview_is_out_of_date(stacked_path: str) -> str | None:
    """Say whether an up-to-date stack's preview picture should be made again.

    The picture is made again when it is missing, or when the picture
    settings saved in its record differ from the settings now in force. A
    picture made with choices for one run (``custom`` in its record) is
    never replaced this way, and neither is a picture whose record is
    missing or older than the saved settings: nothing shows what made it.

    Parameters
    ----------
    stacked_path : `str`
        The stack.

    Returns
    -------
    reason : `str` or `None`
        A plain sentence saying why the picture is made again, or `None`
        when it is kept.
    """
    if not os.path.isfile(preview_path_for(stacked_path)):
        return "the stack has no preview picture"
    record = read_preview_settings(stacked_path)
    if record is None or record.get("custom") or not isinstance(record.get("configuration"), dict):
        return None
    saved = record["configuration"]
    changed = sorted(name for name, value in preview_configuration().items() if saved.get(name) != value)
    if not changed:
        return None
    return "the picture settings changed (" + ", ".join(name.replace("_", " ") for name in changed) + ")"


def write_preview_settings(
    stacked_path: str, settings: PreviewSettings, settings_used: dict[str, Any], steps_run: list[str]
) -> None:
    """Save a small record of what made a stack's preview picture.

    The record sits beside the stack. It is not part of the stack's inputs
    (see `stack_inputs`): a change it shows never restacks. It does hold the
    picture settings in force (`preview_configuration`), so that a stack
    found up to date still gets a new picture when those settings changed
    (see `preview_is_out_of_date`). A failure to write is logged and does
    not fail the preview.

    Parameters
    ----------
    stacked_path : `str`
        The stack whose picture was just made.
    settings : `PreviewSettings`
        The choices this run was given.
    settings_used : `dict`
        The choices the run really used, as `_picture_script` reports them.
    steps_run : `list` [`str`]
        The sentences describing each step and whether it ran.
    """
    record = {
        "made_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "settings_used": settings_used,
        "steps_run": steps_run,
        "custom": settings != PreviewSettings(),
        "configuration": preview_configuration(),
    }
    try:
        with open(preview_settings_path_for(stacked_path), "w", encoding="utf-8") as record_file:
            json.dump(record, record_file, indent=1)
    except OSError as error:
        logger.warning("Could not save the preview settings of '%s': %s", stacked_path, error)


def read_preview_settings(stacked_path: str) -> dict[str, Any] | None:
    """Read the record of what made a stack's preview picture.

    Parameters
    ----------
    stacked_path : `str`
        The stack.

    Returns
    -------
    record : `dict` or `None`
        The record (``made_at``, ``settings_used``, ``steps_run``, ``custom``
        and, for pictures made since it was added, ``configuration``), or
        `None` if the file is missing or unreadable. `None` means unknown:
        a picture made before the record existed has none.
    """
    try:
        with open(preview_settings_path_for(stacked_path), encoding="utf-8") as record_file:
            record = json.load(record_file)
    except OSError, ValueError:
        return None
    return record if isinstance(record, dict) else None
