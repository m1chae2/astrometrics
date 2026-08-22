"""Purpose: Site Profile Domain Models.

Description: The observing location's coordinates and horizon
obstructions. Per `Wayfinding_Library_Architecture.md` §2.2.2, this is
the architecture's single authoritative source of observer position:
any calculation needing latitude, longitude, or elevation resolves them
from `SiteProfile` rather than from a connected device's reported
position (the "Single Source Of Observer Position" invariant).
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AvoidanceZone(BaseModel):
    """A site-specific azimuth range with a locally raised visibility floor.

    Represents a horizon obstruction -- a tree, a roofline, a
    neighboring structure. `azimuth_start_deg`/`azimuth_end_deg` may
    wrap past 360 degrees where the obstruction crosses due north
    (e.g. start=350, end=10).
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    azimuth_start_deg: float = Field(..., ge=0.0, lt=360.0)
    azimuth_end_deg: float = Field(..., ge=0.0, lt=360.0)
    min_clear_altitude_deg: float = Field(..., ge=-90.0, le=90.0)

    def contains_azimuth(self, azimuth_deg: float) -> bool:
        """Return whether `azimuth_deg` falls within this zone's range.

        Handles the due-north wraparound case where
        `azimuth_start_deg` > `azimuth_end_deg`.

        Returns
        -------
        contains : `bool`
            Whether `azimuth_deg` falls within this zone's range.
        """
        azimuth_deg = azimuth_deg % 360.0
        if self.azimuth_start_deg <= self.azimuth_end_deg:
            return self.azimuth_start_deg <= azimuth_deg <= self.azimuth_end_deg
        return azimuth_deg >= self.azimuth_start_deg or azimuth_deg <= self.azimuth_end_deg


class SiteProfile(BaseModel):
    """The observing location's coordinates and horizon obstructions."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    latitude_deg: float = Field(..., ge=-90.0, le=90.0)
    longitude_deg: float = Field(..., ge=-180.0, le=180.0)
    elevation_m: float = Field(default=0.0)
    avoidance_zones: list[AvoidanceZone] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_avoidance_zone_ids_unique(self) -> SiteProfile:
        """Verify avoidance zone identifiers are unique within this profile.

        Returns
        -------
        profile : `SiteProfile`
            This profile, unchanged, once validated.

        Raises
        ------
        ValueError
            Raised if `avoidance_zones` contains a duplicate `id`.
        """
        ids = [z.id for z in self.avoidance_zones]
        if len(ids) != len(set(ids)):
            raise ValueError("avoidance_zones contains duplicate id values")
        return self
