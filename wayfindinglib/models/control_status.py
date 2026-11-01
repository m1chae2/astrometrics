"""Purpose: The replies of the `status` reads on the `control` children.

Description: Each child of `ObservatoryControl` that has readable state
offers one `status(include=[...])` read. `include` names the sections to
fill. A section that was not asked for stays `None`, and `sections`
lists the ones that were read, so `None` in a section that was read
means "nothing is recorded or configured".
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from wayfindinglib.models.equipment_and_site.enclosure import Enclosure, EnclosureState
from wayfindinglib.models.equipment_and_site.equipment import Camera, GuideScope, Telescope
from wayfindinglib.models.equipment_and_site.focus_model import FocusModel
from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.models.policy.commissioning import CommissioningRun
from wayfindinglib.models.policy.delegation import DelegationPolicy
from wayfindinglib.models.policy.safety import SafetyAssessment, SafetyRuleSet
from wayfindinglib.models.session.telemetry import GuidingSpectrumAnalysis, LiveGuidingStatus


class MountControlStatus(BaseModel):
    """The mount's position, tracking and connection, read now.

    Returned by `control.mount.status`. Right ascension and declination are
    ICRS (J2000), like catalog stars and plate solutions: the mount's own
    current-epoch (JNow) reading is converted. A position the mount does
    not know yet (such as ``"Unknown"``) is passed on as it is. The
    names in quotes are the keys of the reply on the wire.
    """

    model_config = ConfigDict(populate_by_name=True)

    ra: str = Field(description='Right ascension, such as "5h 35m 17.30s".')
    dec: str = Field(description='Declination, such as "-5° 23′ 28.0″".')
    altitude: str = Field(description="Altitude above the horizon, as the mount reports it.")
    azimuth: str = Field(description="Azimuth, as the mount reports it.")
    temperature: str = Field(default="-", description="Ambient temperature the session reports, or a dash.")
    humidity: str = Field(default="-", description="Ambient humidity the session reports, or a dash.")
    tracking_status: str = Field(
        alias="trackingStatus", description='Such as "Tracking", "Idle" or "Parked".'
    )
    connection_status: str = Field(default="Disconnected", alias="connectionStatus")
    target_name: str | None = Field(
        default=None, alias="targetName", description="The target the mount reports."
    )
    pier_side: str | None = Field(
        default=None, alias="pierSide", description="``EAST`` or ``WEST``, or `None` if not reported."
    )
    parked: bool | None = Field(default=None, description="Whether the mount is parked.")
    track_mode: str | None = Field(
        default=None, alias="trackMode", description="The tracking rate, such as ``SIDEREAL``."
    )


class ImagingStatus(BaseModel):
    """The main camera, filter wheel and focuser, and the saved focus model."""

    model_config = ConfigDict(populate_by_name=True)

    sections: list[str] = Field(default_factory=list, description="The sections that were read.")
    filter_names: list[str] | None = Field(default=None, description="Filter wheel slot names, in order.")
    current_filter: str | None = Field(default=None, description="The filter in the beam now.")
    focuser_position: int | None = Field(default=None, description="Focuser position in steps.")
    camera_temperature: str | None = Field(
        default=None, description='The sensor temperature, such as "-10.0°C", or a dash if unknown.'
    )
    camera_status: str | None = Field(default=None, description='What the camera is doing, such as "Idle".')
    focus_model: FocusModel | None = Field(
        default=None, description="Saved focus model for the active telescope and camera."
    )


class GuidingStatus(BaseModel):
    """The saved guider models for the active equipment, and live guiding."""

    model_config = ConfigDict(populate_by_name=True)

    sections: list[str] = Field(default_factory=list, description="The sections that were read.")
    calibration: GuiderCalibration | None = Field(
        default=None, description="Saved guider calibration for the active telescope and camera."
    )
    spectrum_analysis: GuidingSpectrumAnalysis | None = Field(
        default=None, description="Saved periodic error and backlash model of the active mount."
    )
    plate_scale_arcsec_per_px: float | None = Field(
        default=None, description="Guide camera plate scale in arcseconds per pixel."
    )
    live: LiveGuidingStatus | None = Field(
        default=None, description="The guiding going on now, with its RMS error and newest samples."
    )


class SafetyStatus(BaseModel):
    """The weather verdict, the enclosure and who may command the hardware."""

    model_config = ConfigDict(populate_by_name=True)

    sections: list[str] = Field(default_factory=list, description="The sections that were read.")
    rule_set: SafetyRuleSet | None = Field(default=None, description="Saved safety rules.")
    assessment: SafetyAssessment | None = Field(
        default=None, description="Verdict from a fresh weather reading."
    )
    enclosure: Enclosure | None = Field(default=None, description="The configured enclosure.")
    enclosure_state: EnclosureState | None = Field(
        default=None, description="The enclosure's live motion state."
    )
    delegation_policy: DelegationPolicy | None = Field(
        default=None, description="Which capabilities this app may command."
    )
    divergence: dict[str, Any] | None = Field(
        default=None, description="Agreement between this app's advice and what the incumbent did."
    )


class EquipmentStatus(BaseModel):
    """The equipment profile, the site and the device connections."""

    model_config = ConfigDict(populate_by_name=True)

    sections: list[str] = Field(default_factory=list, description="The sections that were read.")
    telescope: Telescope | None = Field(default=None, description="The active telescope.")
    camera: Camera | None = Field(default=None, description="The active main camera.")
    guide_scope: GuideScope | None = Field(default=None, description="The active guide scope.")
    guide_camera: Camera | None = Field(default=None, description="The active guide camera.")
    camera_profiles: list[dict[str, Any]] | None = Field(
        default=None, description="Every camera profile in the configuration."
    )
    configuration: dict[str, Any] | None = Field(
        default=None, description="The active telescope and camera pairing with its field of view."
    )
    observer_location: dict[str, float] | None = Field(
        default=None, description="Latitude, longitude and elevation of the observatory."
    )
    commissioning_runs: list[CommissioningRun] | None = Field(
        default=None, description="Every recorded commissioning drill."
    )
    indi_devices: list[str] | None = Field(default=None, description="Connected INDI device names.")
    indi_properties: dict[str, Any] | None = Field(
        default=None, description="Every INDI property of one device."
    )
