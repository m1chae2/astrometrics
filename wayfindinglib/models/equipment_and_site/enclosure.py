"""Purpose: Enclosure Domain Models.

Description: The observatory's roof or dome, specified alongside the
equipment rather than as part of it, because its constraint is
geometric and mutual: a roof may only close when the mount is within
positions that clear it, and the mount may only leave park when the
enclosure is open (`Wayfinding_Library_Architecture.md` Sections 3.2 and
6.6). Recording
the permitted closure positions as configuration rather than deriving
them makes the interlock checkable without commanding anything.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class EnclosureType(StrEnum):
    """The physical form of the observatory's protective structure."""

    ROLL_OFF_ROOF = "roll_off_roof"
    DOME = "dome"


class EnclosureState(StrEnum):
    """A device's motion state, published uniformly for the interlock.

    `UNKNOWN` is treated as unsafe for both mount motion and enclosure
    motion, per `Wayfinding_Library_Architecture.md` Section 6.6 (an
    unknown enclosure state blocks mount motion; see also Section 6.5,
    "Absence of information is not safety").
    """

    OPEN = "open"
    OPENING = "opening"
    CLOSED = "closed"
    CLOSING = "closing"
    UNKNOWN = "unknown"
    FAULT = "fault"


class Enclosure(BaseModel):
    """The observatory's roof or dome and its mechanical interlock envelope.

    `present=False` distinguishes an observatory with no enclosure from
    one whose enclosure is offline -- a distinction that matters because
    the first permits unattended operation under the remaining safety
    actions while the second is a fault.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    enclosure_type: EnclosureType
    present: bool = Field(default=True)
    park_azimuth_deg: float = Field(..., ge=0.0, lt=360.0)
    park_altitude_deg: float = Field(..., ge=-90.0, le=90.0)
    clearance_tolerance_deg: float = Field(default=2.0, gt=0.0)
    motion_timeout_sec: int = Field(default=180, gt=0)
    protocol: str = Field(
        default="indi",
        description="Hardware-control protocol driving this enclosure.",
    )
