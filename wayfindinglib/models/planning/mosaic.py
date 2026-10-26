"""Purpose: Mosaic panel models.

Description: A mosaic covers a region too large for one picture with a
grid of overlapping pictures, called panels. `ObservationPlanning.
calculate_panels` works out each panel's center and returns one
`MosaicPanel` per grid cell. `ObservationPlanning.create_mosaic` then
adds one library target per panel and, when asked, one observation
package per panel, and returns what it made as a `MosaicPlan`.
"""

from pydantic import BaseModel, ConfigDict, Field

from wayfindinglib.models.planning.framing import MosaicPanel
from wayfindinglib.models.planning.observation_package import ObservationPackage


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
