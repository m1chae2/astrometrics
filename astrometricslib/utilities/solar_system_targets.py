"""Tells whether a target is a body of the solar system rather than a star.

The star catalogs (SIMBAD, Gaia) list fixed objects. A planet, the Moon or the
Sun moves against the stars and has no catalog entry. Code that names the
star at the centre of a frame by looking up the nearest catalog entry must
skip these targets: the nearest entry is a chance background star, and a
spectrum recorded under that star's name would be wrong.

The check works on the target's name. It cannot see asteroids or comets,
which have too many names to list.
"""

# Names are compared after `_normalise_name`, so "Mars", "mars" and "MARS" all
# match. Earth is left out on purpose: no target is ever "Earth".
SOLAR_SYSTEM_BODY_NAMES = frozenset({
    "sun",
    "moon",
    "mercury",
    "venus",
    "mars",
    "jupiter",
    "saturn",
    "uranus",
    "neptune",
    "pluto",
    "ceres",
})


def _normalise_name(target_name: str) -> str:
    """Reduce a target name to a comparable form.

    Parameters
    ----------
    target_name : `str`
        The target's name as stored, for example ``"Mars"`` or ``"mars_"``.

    Returns
    -------
    normalised : `str`
        The name in lower case with underscores and hyphens turned into
        spaces and the ends trimmed.
    """
    return target_name.replace("_", " ").replace("-", " ").strip().lower()


def is_solar_system_target(target_name: str | None) -> bool:
    """Tell whether a target name is a body of the solar system.

    Parameters
    ----------
    target_name : `str` or `None`
        The target's name. `None` and an empty name are not solar-system
        bodies.

    Returns
    -------
    is_solar_system : `bool`
        `True` when the name is one of `SOLAR_SYSTEM_BODY_NAMES`.
    """
    if not target_name:
        return False
    return _normalise_name(target_name) in SOLAR_SYSTEM_BODY_NAMES
