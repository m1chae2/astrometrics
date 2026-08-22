"""Purpose: Mosaic Panel Set Domain Models.

Description: A multi-panel imaging request is not a package field but a
package *generator*: an operator supplies a tiling of one region and a
shared exposure recipe, and Planning expands the request into one
sibling `ObservationPackage` per panel, each targeting its own panel
sub-target created through astrometricslib's public high-level interface
(`Wayfinding_Library_Architecture.md` §2.3.2). Supersedes the deprecated
`observation.MosaicPanel`, which carried `ra_str`/`dec_str`/`panel_id`
rather than a resolved `panel_target_id`.
"""

from pydantic import BaseModel, ConfigDict, Field


class MosaicGridConfig(BaseModel):
    """The requested tiling of one sky region into a rows x cols grid."""

    model_config = ConfigDict(populate_by_name=True)

    rows: int = Field(..., gt=0)
    cols: int = Field(..., gt=0)
    overlap_percent: float = Field(default=10.0, ge=0.0, lt=100.0)


class MosaicPanel(BaseModel):
    """One generated panel's position and resolved sub-target reference."""

    model_config = ConfigDict(populate_by_name=True)

    row: int = Field(..., ge=0)
    col: int = Field(..., ge=0)
    ra_deg: float = Field(..., ge=0.0, lt=360.0)
    dec_deg: float = Field(..., ge=-90.0, le=90.0)
    panel_target_id: str
