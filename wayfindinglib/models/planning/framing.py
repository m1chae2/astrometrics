"""Purpose: Framing and mosaic panel models.

Description: Where the camera points and how it is turned. A `MosaicPanel`
is one grid cell of a mosaic. A `Framing` is a field center, a camera
rotation and any panels. Both live here, apart from `mosaic.py` and
`observation_package.py`, because an observation package and an observing
session each keep a `Framing`, and a package module cannot import the
mosaic module without an import cycle.
"""

from pydantic import BaseModel, ConfigDict, Field


class MosaicPanel(BaseModel):
    """One panel of a mosaic grid and where its center is."""

    model_config = ConfigDict(populate_by_name=True)

    row: int = Field(..., ge=0, description="Grid row, counted from 0.")
    col: int = Field(..., ge=0, description="Grid column, counted from 0.")
    ra_str: str = Field(..., description="Right ascension of the center, as hours, minutes and seconds.")
    dec_str: str = Field(..., description="Declination of the center, as degrees, minutes and seconds.")
    ra_deg: float = Field(..., ge=0.0, lt=360.0, description="Right ascension of the center, in degrees.")
    dec_deg: float = Field(..., ge=-90.0, le=90.0, description="Declination of the center, in degrees.")
    panel_id: str = Field(..., description='Short panel name such as "P1_2" (row 1, column 2).')
    rotation_deg: float = Field(
        default=0.0,
        description=(
            "How far the camera is turned, in degrees east of north (the way Ekos and KStars give "
            "it). 0 puts the grid rows along declination and the columns along right ascension."
        ),
    )


class Framing(BaseModel):
    """Where the camera points and how it is turned, with any mosaic panels.

    The rotation is in degrees east of north, the way Ekos and KStars give
    it. The first version shows the numbers only.
    """

    model_config = ConfigDict(populate_by_name=True)

    center_ra_deg: float = Field(ge=0.0, lt=360.0, alias="centerRaDeg")
    center_dec_deg: float = Field(ge=-90.0, le=90.0, alias="centerDecDeg")
    rotation_deg: float = Field(default=0.0, alias="rotationDeg")
    panels: list[MosaicPanel] = Field(default_factory=list, alias="panels")
