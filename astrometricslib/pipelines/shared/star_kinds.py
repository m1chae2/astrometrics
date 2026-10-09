"""Purpose: Tell how a star id can be looked up, and read its position.

Description: Shared by the SIMBAD and variable-star backfill scripts so
neither script has to import the other.
"""

from enum import StrEnum

from astrometricslib.models.stellar_source import StellarObject

_GAIA_PREFIX = "Gaia DR3 "
# The id given to a Gaia star with no source number: made from its position.
_SYNTHETIC_GAIA_PREFIX = "Gaia DR3 J"
_POSITION_ONLY_PREFIX = "FIELD_J"
_SPECTROSCOPY_COPY_SUFFIX = "::spectroscopy"


class StarKind(StrEnum):
    """How a star's id can be looked up in SIMBAD."""

    SIMBAD_NAME = "simbad_name"
    GAIA_SOURCE = "gaia_source"
    SKIPPED = "skipped"


def kind_of_star_id(star_id: str) -> StarKind:
    """Say how a star's id can be looked up in SIMBAD.

    Parameters
    ----------
    star_id : `str`
        The star's id in the catalog.

    Returns
    -------
    kind : `StarKind`
        ``GAIA_SOURCE`` for a Gaia DR3 source number, ``SKIPPED`` for ids that
        carry no identity (position-only, made-up Gaia ids, spectroscopy
        copies), and ``SIMBAD_NAME`` for everything else.
    """
    if star_id.endswith(_SPECTROSCOPY_COPY_SUFFIX) or star_id.startswith(_POSITION_ONLY_PREFIX):
        return StarKind.SKIPPED
    if star_id.startswith(_SYNTHETIC_GAIA_PREFIX):
        return StarKind.SKIPPED
    if star_id.startswith(_GAIA_PREFIX) and star_id[len(_GAIA_PREFIX) :].isdigit():
        return StarKind.GAIA_SOURCE
    return StarKind.SIMBAD_NAME


def stored_position_degrees(star: StellarObject) -> tuple[float, float] | None:
    """Read a star's stored position in degrees.

    Parameters
    ----------
    star : `StellarObject`
        The star.

    Returns
    -------
    position : `tuple` [`float`, `float`] or `None`
        Right ascension and declination, or `None` if the star has no usable
        numeric position.
    """
    try:
        right_ascension = float(star.right_ascension)
        declination = float(star.declination)
    except TypeError, ValueError:
        return None
    if right_ascension != right_ascension or declination != declination:  # NaN
        return None
    return right_ascension, declination
