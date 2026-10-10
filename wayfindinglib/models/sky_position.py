"""Purpose: A point on the sky, given by its right ascension and declination.

Description: `SkyPosition` is the plain coordinate pair the mount
commands take (`control.mount.slew` and `control.mount.sync`). Both
values are in degrees, so the name of each field says its unit. The
model turns into JSON directly, so a remote caller can pass one as
``{"ra_deg": ..., "dec_deg": ...}``.
"""

from pydantic import BaseModel, ConfigDict, Field


class SkyPosition(BaseModel):
    """A sky position in the equatorial frame, in degrees.

    Attributes
    ----------
    ra_deg : `float`
        Right ascension, from 0 up to (but not including) 360 degrees.
    dec_deg : `float`
        Declination, from -90 to +90 degrees.
    """

    model_config = ConfigDict(frozen=True)

    ra_deg: float = Field(..., ge=0.0, lt=360.0, description="Right ascension in degrees.")
    dec_deg: float = Field(..., ge=-90.0, le=90.0, description="Declination in degrees.")

    @property
    def ra_hours(self) -> float:
        """Right ascension in hours, the unit mount drivers take.

        Returns
        -------
        ra_hours : `float`
            ``ra_deg / 15``.
        """
        return self.ra_deg / 15.0
