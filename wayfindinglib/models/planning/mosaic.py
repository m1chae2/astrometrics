"""Purpose: Mosaic panel models.

Description: A mosaic covers a region too large for one picture with a
grid of overlapping pictures, called panels. `ObservationPlanning.
calculate_panels` works out each panel's center and returns one
`MosaicPanel` per grid cell. `ObservationPlanning.create_mosaic` then
adds one library target per panel and, when asked, one observation
package per panel, and returns what it made as a `MosaicPlan`.
"""

from pydantic import BaseModel, ConfigDict, Field

from wayfindinglib.models.planning.observation_package import ObservationPackage


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


class MosaicPlan(BaseModel):
    """What `create_mosaic` added: a target, and maybe a package, per panel."""

    model_config = ConfigDict(populate_by_name=True)

    parent_target_id: str = Field(..., description="The target the mosaic covers.")
    panels: list[MosaicPanel] = Field(default_factory=list, description="The panels, in the order given.")
    target_ids: list[str] = Field(
        default_factory=list, description="The id of the library target made for each panel."
    )
    packages: list[ObservationPackage] = Field(
        default_factory=list, description="One recorded observation package per panel, when asked for."
    )
