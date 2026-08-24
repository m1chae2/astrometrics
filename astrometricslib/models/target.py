"""Purpose: Target Domain Model.

Description: Target & Library Management pure data schemas. Only
derived-field methods (serialize, deserialize, recalculate_total_exposure)
live on Target itself -- all pipeline orchestration (analyze_target,
process_target, run_full_pipeline, reindex_frames, etc.) moved to free
functions in tasks/target_tasks/pipeline_tasks.py, taking a Target
instance as their first argument. This breaks the former circular
import between target.py and targetlib/ (targetlib modules imported
Target back at module level, while target.py imported targetlib
modules at module level too), which previously required ~30 scattered
in-method lazy imports to avoid an ImportError at load time.

# REQ: BKD-5: Data Persistence
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from astrometricslib.models.moving_object import AsteroidRecoveryCandidate
from astrometricslib.models.quality_summary import (
    AsteroidRecoveryQualitySummary,
    AstrometryQualitySummary,
    PhotometryQualitySummary,
    SpectroscopyQualitySummary,
    StackQualitySummary,
)
from astrometricslib.utilities.enums import FilterType

# Declares this module's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "FitsHeaderEntry",
    "FrameRecord",
    "ImageType",
    "MosaicInfo",
    "RenderedImage",
    "Target",
]


class ImageType(StrEnum):
    """Enum representing different types of target images."""

    STAR_FIELD = "star_field"
    TARGET_IMAGE = "target_image"


class FrameRecord(BaseModel):
    """Represents a single image frame on disk with its associated metadata."""

    path: str = Field(alias="path")
    filter: FilterType = Field(default=FilterType.NONE, alias="filter")
    role: str = Field(default="LIGHT", alias="role")  # LIGHT, DARK, FLAT, BIAS
    iso: str = Field(default="800", alias="iso")
    offset: str = Field(default="0", alias="offset")
    exposure: str = Field(default="1.0", alias="exposure")
    timestamp: float | None = Field(default=None, alias="timestamp")
    camera: str = Field(default="Unknown", alias="camera")
    telescope: str = Field(default="Unknown", alias="telescope")
    date: str = Field(default="Unknown", alias="date")

    # Acquisition conditions, read straight from the FITS header at index
    # time. These cost nothing beyond the header parse already happening
    # and describe the sky and equipment state the frame was taken under,
    # which is what separates "the mount misbehaved" from "the target was
    # low" when a later analysis flags a frame.
    pier_side: str | None = Field(default=None, alias="pierSide")
    airmass: float | None = Field(default=None, alias="airmass")
    altitude_degrees: float | None = Field(default=None, alias="altitudeDegrees")
    azimuth_degrees: float | None = Field(default=None, alias="azimuthDegrees")
    pixel_scale_arcsec: float | None = Field(default=None, alias="pixelScaleArcsec")
    # Which optic took this frame, in millimetres. Recorded per frame
    # rather than taken from configuration because a library can span
    # several optics: this one holds 1,596 frames at 300mm and 1,055 at
    # 405mm, and seven targets had both mixed into a single stack, whose
    # scales differ by 1.35x. Frames must be grouped by this before
    # stacking -- see `select_frames_for_configuration`.
    focal_length_mm: float | None = Field(default=None, alias="focalLengthMm")
    binning: int | None = Field(default=None, alias="binning")
    sensor_temperature_c: float | None = Field(default=None, alias="sensorTemperatureC")
    focuser_position: int | None = Field(default=None, alias="focuserPosition")
    focuser_temperature_c: float | None = Field(default=None, alias="focuserTemperatureC")

    # Registration facts (Siril's findstar/registration pass, one .seq line
    # per frame). None until the first pipeline that needs them computes and
    # persists them.
    registration_fwhm_x_px: float | None = Field(default=None, alias="registrationFwhmXPx")
    registration_fwhm_y_px: float | None = Field(default=None, alias="registrationFwhmYPx")
    registration_roundness: float | None = Field(default=None, alias="registrationRoundness")
    registration_rmse: float | None = Field(default=None, alias="registrationRmse")
    registration_star_count: int | None = Field(default=None, alias="registrationStarCount")
    registration_dx_px: float | None = Field(default=None, alias="registrationDxPx")
    registration_dy_px: float | None = Field(default=None, alias="registrationDyPx")

    # Per-frame facts computed directly on the raw frame, independent of
    # whether it was ever registered or stacked.
    background_level: float | None = Field(default=None, alias="backgroundLevel")
    saturated_pixel_fraction: float | None = Field(default=None, alias="saturatedPixelFraction")
    # Kept separate from registration_fwhm_x/y_px because the two are not
    # on the same absolute scale: photutils measures ~1.53x Siril's PSF
    # fit on identical frames. Comparing values across the two fields is
    # meaningless; comparing within either one is valid.
    measured_fwhm_px: float | None = Field(default=None, alias="measuredFwhmPx")

    @field_validator("filter", mode="before")
    @classmethod
    def normalize_filter(cls, v: Any) -> Any:
        """Normalize filter string representations into FilterType.

        Returns
        -------
        normalized_value : `Any`
            The matching `FilterType` member if `v` is a recognized
            filter string; otherwise `v` unchanged.
        """
        if isinstance(v, str):
            mapping = {
                "LUMINANCE": FilterType.L,
                "RED": FilterType.R,
                "GREEN": FilterType.G,
                "BLUE": FilterType.B,
                "HA": FilterType.Ha,
                "OIII": FilterType.OIII,
                "SII": FilterType.SII,
                "SPEC": FilterType.SPEC,
                "SPECTROSCOPY": FilterType.SPEC,
                "NONE": FilterType.NONE,
            }
            norm = v.upper()
            return mapping.get(norm, v)
        return v


class MosaicInfo(BaseModel):
    """Stores information about a mosaic configuration created for a target."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    group_id: str = Field(description="UUID for the mosaic group", alias="groupId")
    name: str = Field(description="Name of the mosaic configuration", alias="name")
    created_at: float = Field(description="Timestamp of creation", alias="createdAt")
    panels: list[str] = Field(default_factory=list, alias="panels")


class StackConfigurationResult(BaseModel):
    """One camera-and-optic configuration's stacking result.

    A target imaged through two optics has two valid stacks that must
    not be combined, since their pixel scales differ. This holds the
    result for one of them so the other is not lost.
    """

    model_config = ConfigDict(populate_by_name=True)

    configuration_key: str = Field(alias="configurationKey")
    camera: str = Field(default="", alias="camera")
    focal_length_mm: float | None = Field(default=None, alias="focalLengthMm")
    frames_stacked: int = Field(default=0, alias="framesStacked")
    stacked_image: str = Field(default="", alias="stackedImage")
    is_preferred: bool = Field(default=False, alias="isPreferred")


class Target(BaseModel):
    """Pure data schema for astronomical targets.

    Algorithmic operations (stacking, analysis, pipelines) are free
    functions in `astrometricslib.tasks.target_tasks.pipeline_tasks`
    taking a `Target` as their first argument, and are exposed to
    external callers via `astrometricslib.api.targets.TargetCatalog`.

    """

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    id: str = Field(default="", alias="id")
    common_name: str = Field(default="", alias="commonName")
    image_type: ImageType = Field(default=ImageType.TARGET_IMAGE, alias="imageType")
    ra: str = Field(default="0h 0m 0s", alias="ra")
    dec: str = Field(default="0° 0′ 0′′", alias="dec")
    field_of_view: str = Field(default="0′", alias="fieldOfView")
    main_camera: str = Field(default="ZWO ASI 533MM Pro", alias="mainCamera")
    guide_camera: str = Field(default="", alias="guideCamera")
    main_scope: str = Field(default="Apertura 75Q", alias="mainScope")
    guide_scope: str = Field(default="", alias="guideScope")
    mount: str = Field(default="SW Star Adventurer GTi", alias="mount")
    processed_image: str = Field(default="", alias="processedImage")
    # The preferred configuration's stack, kept single-valued so every
    # existing reader and the UI continue to work unchanged. Which
    # configuration is preferred comes from the observer's primary optic
    # in configuration -- see `AppConfiguration.get_primary_focal_length_mm`.
    stacked_image: str = Field(default="", alias="stackedImage")
    # Every configuration's stack, including the preferred one that
    # `stacked_image` also points at. Additive on purpose: 45 call sites
    # and the UI read `stacked_image`, and none of them need to change
    # for a target to gain a second optic. Keyed by
    # `pipeline_tasks.frame_configuration_key`, e.g.
    # "Nikon DSLR DSC D5300@300mm".
    stacks_by_configuration: dict[str, StackConfigurationResult] = Field(
        default_factory=dict, alias="stacksByConfiguration"
    )
    stacked_spectral_target: str = Field(default="", alias="stackedSpectralTarget")
    stack_quality_summary: StackQualitySummary | None = Field(default=None, alias="stackQualitySummary")
    spectral_stack_quality_summary: StackQualitySummary | None = Field(
        default=None, alias="spectralStackQualitySummary"
    )
    astrometry_quality_summary: AstrometryQualitySummary | None = Field(
        default=None, alias="astrometryQualitySummary"
    )
    photometry_quality_summary: PhotometryQualitySummary | None = Field(
        default=None, alias="photometryQualitySummary"
    )
    spectroscopy_quality_summary: SpectroscopyQualitySummary | None = Field(
        default=None, alias="spectroscopyQualitySummary"
    )
    asteroid_candidates: list[AsteroidRecoveryCandidate] = Field(
        default_factory=list, alias="asteroidCandidates"
    )
    asteroid_recovery_quality_summary: AsteroidRecoveryQualitySummary | None = Field(
        default=None, alias="asteroidRecoveryQualitySummary"
    )
    exposure_sec: float = Field(default=0, alias="exposureTime")
    number_of_stars: int = Field(default=0, alias="numberOfStars")
    frames: list[FrameRecord] = Field(default_factory=list, alias="frames")

    # Mosaic Fields
    mosaic_groups: list[MosaicInfo] = Field(default_factory=list, alias="mosaicGroups")
    parent_group_id: str | None = Field(default=None, alias="parentGroupId")
    panel_name: str = Field(default="", alias="panelName")

    def serialize(self) -> dict[str, Any]:
        """Convert the target object into a dictionary representation.

        Returns
        -------
        data : `dict[str, Any]`
            The target's fields, keyed by alias.
        """
        data = self.model_dump(mode="python", by_alias=True)
        if "stackedSpectralTarget" not in data and hasattr(self, "stacked_spectral_target"):
            data["stackedSpectralTarget"] = self.stacked_spectral_target
        if "stackedImage" not in data and hasattr(self, "stacked_image"):
            data["stackedImage"] = self.stacked_image
        return data

    def deserialize(self, object_info: dict[str, Any]) -> None:
        """Deserialize property values from a dictionary into this target."""
        if not isinstance(object_info, dict):
            return
        # Build mapping from alias to field name
        alias_to_field = {}
        for name, field in self.model_fields.items():
            if field.alias:
                alias_to_field[field.alias] = name

        for property_id, value in object_info.items():
            field_name = alias_to_field.get(property_id, property_id)
            if hasattr(self, field_name) and value:
                if field_name == "image_type" and isinstance(value, str):
                    try:
                        setattr(self, field_name, ImageType(value))
                    except ValueError:
                        setattr(self, field_name, value)
                else:
                    setattr(self, field_name, value)

    def recalculate_total_exposure(self) -> float:
        """Recalculate the total exposure time from the frame records list.

        Returns
        -------
        total : `float`
            The recomputed total exposure time, in seconds.
        """
        total = 0.0
        if self.frames:
            for frame in self.frames:
                try:
                    total += float(frame.exposure)
                except ValueError, TypeError:
                    continue
        self.exposure_sec = total
        return total


class FitsHeaderEntry(BaseModel):
    """Represents a single card entry in a FITS file header."""

    key: str = Field(alias="key")
    value: str = Field(alias="value")
    comment: str = Field(default="", alias="comment")


class RenderedImage(BaseModel):
    """Lightweight schema holding scaled visualization PNG data and stats."""

    id: str = Field(alias="id")
    min: float = Field(alias="min")
    max: float = Field(alias="max")
    image_data: str = Field(alias="imageData")
