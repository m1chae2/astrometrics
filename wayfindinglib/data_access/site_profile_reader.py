"""Purpose: Site Profile Resolution.

Description: Resolves the active `SiteProfile`, seeding a default on
first use from the location values this library's astronomy
calculation (`Sky`, `wayfindinglib/sky.py`) already falls back to when
no site configuration is present -- the `[Observatory.Location]` config
section, itself falling back to `Sky`'s hardcoded Denver coordinates --
so behavior is unchanged until an operator edits it
(`Wayfinding_Library_Architecture.md` §2.2.2).
"""

from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile

_DEFAULT_SITE_PROFILE_ID = "default"

_FALLBACK_LATITUDE_DEG = 39.7392
_FALLBACK_LONGITUDE_DEG = -104.9903
_FALLBACK_ELEVATION_M = 1600.0
"""Denver coordinates -- the exact fallback `wayfindinglib.sky.Sky.__init__`
uses when `[Observatory.Location]` is unconfigured."""


def _seed_from_config(config) -> SiteProfile:  # ruff: ignore[missing-type-function-argument]
    """Build the default SiteProfile from config, falling back to Denver.

    Mirrors `Sky.__init__`'s exact resolution: read `[Observatory.Location]`
    if the config object exposes `app_config`, otherwise use the same
    hardcoded fallback values `Sky` uses when constructed without one.

    Returns
    -------
    profile : `SiteProfile`
        The default site profile, seeded from config or the fallback.
    """
    if config is not None and hasattr(config, "app_config"):
        latitude_deg = float(
            config.app_config.get("Observatory.Location", "latitude", fallback=str(_FALLBACK_LATITUDE_DEG))
        )
        longitude_deg = float(
            config.app_config.get("Observatory.Location", "longitude", fallback=str(_FALLBACK_LONGITUDE_DEG))
        )
        elevation_m = float(
            config.app_config.get("Observatory.Location", "elevation", fallback=str(_FALLBACK_ELEVATION_M))
        )
    else:
        latitude_deg = _FALLBACK_LATITUDE_DEG
        longitude_deg = _FALLBACK_LONGITUDE_DEG
        elevation_m = _FALLBACK_ELEVATION_M

    return SiteProfile(
        id=_DEFAULT_SITE_PROFILE_ID,
        name="Default Site",
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        elevation_m=elevation_m,
    )


def get_or_seed_default_site_profile(butler, config=None) -> SiteProfile:  # ruff: ignore[missing-type-function-argument]
    """Return the persisted default `SiteProfile`, seeding one if none exists.

    Parameters
    ----------
    butler : `wayfindinglib.drivers.butler.DiskButler`
        The persistence layer to read from and, if seeding, write to.
    config : `AppConfiguration`, optional
        Application configuration to seed the default from. If `None`,
        the Denver fallback is used directly.

    Returns
    -------
    site_profile : `SiteProfile`
        The persisted or newly seeded default site profile.
    """
    existing = butler.get("site_profile", {"id": _DEFAULT_SITE_PROFILE_ID})
    if existing is not None:
        return existing
    seeded = _seed_from_config(config)
    butler.put(seeded, "site_profile", {"id": _DEFAULT_SITE_PROFILE_ID})
    return seeded
