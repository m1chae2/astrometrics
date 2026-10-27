"""Purpose: Sort a target into a broad kind of sky object from its SIMBAD type.

Description: SIMBAD gives every object one main type code, such as "G" for a
galaxy, "GlC" for a globular cluster or "PN" for a planetary nebula. There are
hundreds of codes. The library only needs a few broad kinds, so this module
groups the codes the way SIMBAD's own type tree does:

* ``"galaxy"``: galaxies of every kind, active galaxies and quasars, and
  groups and clusters of galaxies.
* ``"nebula"``: gas and dust clouds, emission, reflection, dark and planetary
  nebulae, supernova remnants and star-forming regions.
* ``"cluster"``: open and globular star clusters and associations of stars.
* ``"star"``: a star of any kind, a double or multiple star, a variable star,
  and the dead remains of a star such as a white dwarf.
* ``"solar_system"``: a planet, the Moon, the Sun or a comet. SIMBAD does not
  type these, so they are found from the target's name.
* `None`: any other code, or no code at all.

A code that ends in a question mark in SIMBAD is a candidate ("PN?"). It is
sorted like the code without the question mark, unless the code is listed on
its own below (for example "C?*", a candidate star cluster).
"""

from astrometricslib.models.target import TargetObjectType
from astrometricslib.utilities.target_classification import classify_target_name

__all__ = ["target_object_category"]

GALAXY_CODES = frozenset({
    "G", "GiC", "GiG", "GiP", "BiC", "IG", "PaG", "SBG", "PoG", "H2G", "LSB", "EmG", "bCG", "rG", "SmG",
    "AGN", "Sy", "Sy1", "Sy2", "SyG", "LIN", "QSO", "Bla", "BLL", "Bz", "mAGN", "GrG", "CGG", "ClG",
    "SCG", "SCl", "Q?", "AG?", "G?", "Gr?", "ClG?",
})  # fmt: skip
"""SIMBAD codes of galaxies, active galaxies and groups or clusters of them."""

NEBULA_CODES = frozenset({
    "PN", "PN?", "HII", "RNe", "ISM", "MoC", "DNe", "SNR", "SNR?", "EmO", "Cld", "GNe", "BNe", "SFR", "HH",
    "glb", "cor", "bub", "sh", "Cir", "HI", "CGb",
})  # fmt: skip
"""SIMBAD codes of nebulae, gas and dust clouds and star-forming regions."""

CLUSTER_CODES = frozenset({"OpC", "GlC", "Cl*", "As*", "C?*", "Gl?", "Op?"})  # fmt: skip
"""SIMBAD codes of star clusters and associations of stars."""

STAR_CODES = frozenset({
    "*", "**", "V*", "*iC", "*iN", "*iA", "*in", "Em*", "Be*", "BS*", "RG*", "AB*", "C*", "S*", "sg*",
    "s*r", "s*y", "s*b", "pA*", "WD*", "N*", "Pec*", "HB*", "Y*O", "Ae*", "TT*", "WR*", "PM*", "HV*",
    "Irr*", "Or*", "RS*", "Pu*", "RR*", "Ce*", "dS*", "RV*", "WV*", "bC*", "cC*", "gD*", "SX*", "LP*",
    "Mi*", "SR*", "pr*", "Al*", "bL*", "EB*", "SB*", "El*", "Sy*", "CV*", "DQ*", "AM*", "NL*", "No*",
    "DN*", "XB*", "LXB", "HXB", "Ro*", "a2*", "Fl*", "Er*", "Ev*", "RC*", "BY*", "FU*", "Ma*", "HS*",
    "BD*", "LM*", "MS*", "OH*", "CH*", "Psr", "NS", "BH",
})  # fmt: skip
"""SIMBAD codes of stars: any star, double or multiple star, variable star,
and the remains of a star (white dwarf, neutron star, black hole)."""


def target_object_category(simbad_object_type: str | None, target_id: str | None = None) -> str | None:
    """Sort a target into a broad kind of sky object.

    Parameters
    ----------
    simbad_object_type : `str` or `None`
        SIMBAD's main object type code for the target, or `None` when it is
        not known.
    target_id : `str`, optional
        The target's id. A planet, the Moon, the Sun, Pluto, Ceres or a comet
        is named by its id, since SIMBAD does not type them. When it names
        one, the answer is ``"solar_system"`` whatever the code is.

    Returns
    -------
    category : `str` or `None`
        ``"galaxy"``, ``"nebula"``, ``"cluster"``, ``"star"``,
        ``"solar_system"``, or `None` when the code is not known or is some
        other kind of object.
    """
    if target_id is not None and classify_target_name(target_id) in (
        TargetObjectType.SOLAR_SYSTEM,
        TargetObjectType.COMET,
    ):
        return "solar_system"
    code = (simbad_object_type or "").strip()
    if not code:
        return None
    for category, codes in (
        ("cluster", CLUSTER_CODES),
        ("galaxy", GALAXY_CODES),
        ("nebula", NEBULA_CODES),
        ("star", STAR_CODES),
    ):
        if code in codes:
            return category
    if code.endswith("?") and len(code) > 1:
        # "PN?" is a candidate planetary nebula; "LP?" a candidate for the
        # star code "LP*".
        return target_object_category(code[:-1]) or target_object_category(code[:-1] + "*")
    return None
