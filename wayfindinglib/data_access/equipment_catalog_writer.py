"""Purpose: Equipment Catalog Writing.

Description: Builds the config changes that store a telescope's safety
limits. It is the write side of `equipment_catalog_reader.py`: it targets
exactly the config section the reader resolves for that telescope, so
what is written is what is read back.

Limits are checked through the `Telescope` model before anything is
built (for example, the minimum altitude cannot exceed the maximum). The
builder never creates a section. If the telescope has no section to
write to, it refuses. A per-telescope minimum or maximum altitude takes
precedence over the global `[Observatory.Constraints]` section.

This module only builds the change. The caller saves it, so the limits
and the active-telescope choice can be stored in one save.
"""

from astrometricslib import AppConfiguration
from wayfindinglib.data_access.equipment_catalog_reader import (
    list_telescopes,
    telescope_section_name_for,
)

TELESCOPE_LIMIT_KEYS = (
    "altitude_limits_enabled",
    "min_altitude_deg",
    "max_altitude_deg",
    "hour_angle_limits_enabled",
    "max_hour_angle_hours",
    "flip_hour_angle_deg",
)
"""The `Telescope` fields that make up the safety limits."""


def build_telescope_limits_update(
    config: AppConfiguration, telescope_id: str, limits: dict[str, float | bool]
) -> dict[str, dict[str, str]]:
    """Validate new safety limits and build the config change that stores them.

    Parameters
    ----------
    config : `AppConfiguration`
        The application configuration. It is read, not changed.
    telescope_id : `str`
        The id of a configured telescope.
    limits : `dict` [`str`, `float` or `bool`]
        Any of `TELESCOPE_LIMIT_KEYS` with new values. Keys left out keep
        their current values.

    Returns
    -------
    update : `dict` [`str`, `dict` [`str`, `str`]]
        ``{section_name: {key: value}}`` for the one section the reader
        resolves for this telescope, with every limit key written so the
        stored limits are complete.

    Raises
    ------
    ValueError
        Raised if a key is not a limit, the telescope is unknown, the
        values fail the `Telescope` checks, or no config section exists
        for this telescope. No section is ever created.
    """
    unknown_keys = sorted(set(limits) - set(TELESCOPE_LIMIT_KEYS))
    if unknown_keys:
        raise ValueError(f"Not safety limits: {', '.join(unknown_keys)}")
    current = next((telescope for telescope in list_telescopes(config) if telescope.id == telescope_id), None)
    if current is None:
        raise ValueError(f"'{telescope_id}' is not a known telescope")
    # Re-running the model checks rejects bad values (for example min > max).
    merged = type(current).model_validate({**current.model_dump(), **limits})
    section_name = telescope_section_name_for(config, telescope_id)
    if section_name is None:
        raise ValueError(
            f"No config section exists for telescope '{telescope_id}'; limits are not saved"
            " because a section is never created."
        )
    return {section_name: {key: str(getattr(merged, key)) for key in TELESCOPE_LIMIT_KEYS}}
