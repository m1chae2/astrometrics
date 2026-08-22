"""Purpose: Device Summary State Domain Models.

Description: A device's lifecycle in one uniform five-state vocabulary,
adopted from the Vera C. Rubin Observatory's commandable-component
summary state (`Wayfinding_Library_Architecture.md` §2.1.1, §2.5.2). A
uniform vocabulary lets a caller reason about readiness without knowing
the device type: an executing session's readiness check asks the same
question of a mount and of a roof.
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DeviceRole(StrEnum):
    """What role a device plays in the imaging train or observatory."""

    MOUNT = "MOUNT"
    PRIMARY_CAMERA = "PRIMARY_CAMERA"
    GUIDE_CAMERA = "GUIDE_CAMERA"
    FILTER_WHEEL = "FILTER_WHEEL"
    FOCUSER = "FOCUSER"
    ENCLOSURE = "ENCLOSURE"
    WEATHER = "WEATHER"


class DeviceSummaryState(StrEnum):
    """A device's lifecycle state, published in one uniform vocabulary.

    `FAULT` is what gives Observation Execution something to recover
    from, and what gives an operator a place to see that a device has
    reported a specific failure rather than merely gone quiet.
    """

    OFFLINE = "OFFLINE"
    STANDBY = "STANDBY"
    DISABLED = "DISABLED"
    ENABLED = "ENABLED"
    FAULT = "FAULT"


class DeviceState(BaseModel):
    """One device's current summary state."""

    model_config = ConfigDict(populate_by_name=True)

    device_id: str
    device_role: DeviceRole
    summary_state: DeviceSummaryState
    fault_detail: str | None = Field(default=None)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
