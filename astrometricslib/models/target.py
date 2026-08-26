"""Data structures for tracking astronomical targets.

This module defines the pure data classes (like Target and FrameRecord)
used to store information about the objects you are photographing.
These classes only hold data; the actual work (stacking, analysis)
happens elsewhere to keep the code organized and avoid import errors.
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
    TrackingQualitySummary,
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
    """Lists the different categories of images we can process."""

    STAR_FIELD = "star_field"
    TARGET_IMAGE = "target_image"


class FrameRecord(BaseModel):
    """A single raw photograph and its settings (like ISO, exposure)."""

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

    # Information about the equipment and sky conditions when the photo was
    # taken.
    # We read these from the image file to help figure out why a picture
    # might be blurry or noisy later on.
    pier_side: str | None = Field(default=None, alias="pierSide")
    airmass: float | None = Field(default=None, alias="airmass")
    altitude_degrees: float | None = Field(default=None, alias="altitudeDegrees")
    azimuth_degrees: float | None = Field(default=None, alias="azimuthDegrees")
    pixel_scale_arcsec: float | None = Field(default=None, alias="pixelScaleArcsec")
    # The focal length (zoom level) of the telescope, in millimeters.
    # We must record this per-picture because a user might photograph the
    # same target with two different telescopes over time, and those pictures
    # cannot be stacked together directly.
    focal_length_mm: float | None = Field(default=None, alias="focalLengthMm")
    binning: int | None = Field(default=None, alias="binning")
    sensor_temperature_c: float | None = Field(default=None, alias="sensorTemperatureC")
    focuser_position: int | None = Field(default=None, alias="focuserPosition")
    focuser_temperature_c: float | None = Field(default=None, alias="focuserTemperatureC")

    # Alignment data. When the pipeline aligns the images (registration),
    # it calculates these values (like how far the stars shifted).
    # They stay None until that pipeline runs.
    registration_fwhm_x_px: float | None = Field(default=None, alias="registrationFwhmXPx")
    registration_fwhm_y_px: float | None = Field(default=None, alias="registrationFwhmYPx")
    registration_roundness: float | None = Field(default=None, alias="registrationRoundness")
    registration_rmse: float | None = Field(default=None, alias="registrationRmse")
    registration_star_count: int | None = Field(default=None, alias="registrationStarCount")
    registration_dx_px: float | None = Field(default=None, alias="registrationDxPx")
    registration_dy_px: float | None = Field(default=None, alias="registrationDyPx")

    # Statistics calculated straight from the raw picture, even if it
    # hasn't been aligned or stacked yet.
    background_level: float | None = Field(default=None, alias="backgroundLevel")
    saturated_pixel_fraction: float | None = Field(default=None, alias="saturatedPixelFraction")
    # Star sharpness measured directly by our code, rather than by Siril.
    # You cannot directly compare this number to `registration_fwhm_x_px`.
    measured_fwhm_px: float | None = Field(default=None, alias="measuredFwhmPx")

    @field_validator("filter", mode="before")
    @classmethod
    def normalize_filter(cls, v: Any) -> Any:
        """Convert a filter name string into the official FilterType.

        Returns
        -------
        normalized_value : `Any`
            The official `FilterType` enum, or the original string if
            it wasn't recognized.
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
    """Details about a multi-panel picture (mosaic) created for this target."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    group_id: str = Field(description="UUID for the mosaic group", alias="groupId")
    name: str = Field(description="Name of the mosaic configuration", alias="name")
    created_at: float = Field(description="Timestamp of creation", alias="createdAt")
    panels: list[str] = Field(default_factory=list, alias="panels")


class StackConfigurationResult(BaseModel):
    """The final stacked image for a specific telescope/camera setup.

    If a target was shot with two different telescopes, it will produce
    two different stacked images. This structure tracks one of them.
    """

    model_config = ConfigDict(populate_by_name=True)

    configuration_key: str = Field(alias="configurationKey")
    camera: str = Field(default="", alias="camera")
    focal_length_mm: float | None = Field(default=None, alias="focalLengthMm")
    frames_stacked: int = Field(default=0, alias="framesStacked")
    stacked_image: str = Field(default="", alias="stackedImage")
    is_preferred: bool = Field(default=False, alias="isPreferred")


class Target(BaseModel):
    """The main record for an astronomical target (like a galaxy or nebula).

    This class only stores data. If you want to stack images or analyze
    the target, use the tools in the `TargetCatalog`.
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
    # The main, finished picture for this target. If multiple telescopes
    # were used, this points to the picture from the 'primary' telescope.
    stacked_image: str = Field(default="", alias="stackedImage")
    # A dictionary tracking the finished pictures from every telescope
    # setup used on this target. The key is a label like "CameraName@300mm".
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
    tracking_quality_summary: TrackingQualitySummary | None = Field(
        default=None, alias="trackingQualitySummary"
    )
    exposure_sec: float = Field(default=0, alias="exposureTime")
    number_of_stars: int = Field(default=0, alias="numberOfStars")
    frames: list[FrameRecord] = Field(default_factory=list, alias="frames")

    # Mosaic Fields
    mosaic_groups: list[MosaicInfo] = Field(default_factory=list, alias="mosaicGroups")
    parent_group_id: str | None = Field(default=None, alias="parentGroupId")
    panel_name: str = Field(default="", alias="panelName")

    def serialize(self) -> dict[str, Any]:
        """Package the target's data into a basic dictionary format.

        Returns
        -------
        data : `dict[str, Any]`
            The target's fields, using their JSON-friendly names.
        """
        data = self.model_dump(mode="python", by_alias=True)
        if "stackedSpectralTarget" not in data and hasattr(self, "stacked_spectral_target"):
            data["stackedSpectralTarget"] = self.stacked_spectral_target
        if "stackedImage" not in data and hasattr(self, "stacked_image"):
            data["stackedImage"] = self.stacked_image
        return data

    def deserialize(self, object_info: dict[str, Any]) -> None:
        """Load values from a dictionary back into this target object."""
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
        """Add up the exposure times of all the individual frames.

        Returns
        -------
        total : `float`
            The total exposure time in seconds.
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
    """A single piece of metadata (key/value pair) from a FITS image file."""

    key: str = Field(alias="key")
    value: str = Field(alias="value")
    comment: str = Field(default="", alias="comment")


class RenderedImage(BaseModel):
    """A finished PNG image ready to display, plus brightness stats."""

    id: str = Field(alias="id")
    min: float = Field(alias="min")
    max: float = Field(alias="max")
    image_data: str = Field(alias="imageData")
