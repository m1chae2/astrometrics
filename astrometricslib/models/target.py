"""Data structures for tracking astronomical targets.

This module defines the pure data classes (like Target and FrameRecord)
used to store information about the objects being photographed.
These classes only hold data; the actual work (stacking, analysis)
happens elsewhere to keep the code organized and avoid import errors.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from astrometricslib.foundation.enums import FilterType
from astrometricslib.models.moving_object import AsteroidDetectionCandidate
from astrometricslib.models.quality_summary import (
    AsteroidDetectionQualitySummary,
    AstrometryQualitySummary,
    PhotometryQualitySummary,
    SpectroscopyQualitySummary,
    StackQualitySummary,
)

# Declares this module's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "AsteroidDetectionResult",
    "FitsHeaderEntry",
    "FrameMeasurements",
    "FrameRecord",
    "ImageType",
    "RenderedImage",
    "StretchParameters",
    "Target",
    "TargetObjectType",
    "TargetQualitySummaries",
    "TargetStackingResult",
    "ViewableImage",
]


class ImageType(StrEnum):
    """Lists the different categories of images that can be processed."""

    STAR_FIELD = "star_field"
    TARGET_IMAGE = "target_image"


class FrameMeasurements(BaseModel):
    """Per-frame statistics our own code computed from the pixels.

    Unlike `FrameRecord`'s other fields, which are recorded straight
    from the camera at capture time with zero analysis, every field
    here is the output of some pipeline stage (frame scanning,
    registration) running our own code against the pixels. Split out so
    "what the instrument wrote down" and "what we calculated" are two
    distinct, separately named things rather than fields interleaved in
    one flat model.
    """

    model_config = ConfigDict(populate_by_name=True)

    # Statistics calculated straight from the raw picture, even if it
    # hasn't been aligned or stacked yet.
    background_level: float | None = Field(default=None, alias="backgroundLevel")
    saturated_pixel_fraction: float | None = Field(default=None, alias="saturatedPixelFraction")
    # Star sharpness measured directly by the code, rather than by Siril.
    # This number cannot be directly compared to `registration_fwhm_x_px`.
    measured_fwhm_px: float | None = Field(default=None, alias="measuredFwhmPx")

    # Alignment data. When the pipeline aligns the images (registration),
    # it calculates these values (like how far the stars shifted).
    # They stay None until that pipeline runs.
    registration_fwhm_x_px: float | None = Field(default=None, alias="registrationFwhmXPx")
    registration_fwhm_y_px: float | None = Field(default=None, alias="registrationFwhmYPx")
    # How round the stars look after alignment (1.0 is a perfect circle).
    # A lower number can mean the telescope drifted during the photo.
    registration_roundness: float | None = Field(default=None, alias="registrationRoundness")
    # How far off, on average, the alignment was when lining this
    # picture up with the others, in pixels. Lower is better.
    registration_rmse: float | None = Field(default=None, alias="registrationRmse")
    registration_star_count: int | None = Field(default=None, alias="registrationStarCount")
    registration_dx_px: float | None = Field(default=None, alias="registrationDxPx")
    registration_dy_px: float | None = Field(default=None, alias="registrationDyPx")


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
    # These are read from the image file to help figure out why a picture
    # might be blurry or noisy later on.
    # Which side of the mount the telescope was pointing from. Telescopes
    # on this type of mount have to flip to the other side partway
    # through the night, which can shift the picture.
    pier_side: str | None = Field(default=None, alias="pierSide")
    # How much of Earth's atmosphere the starlight passed through.
    # 1.0 means straight overhead; higher numbers mean closer to the
    # horizon, where more air blurs and dims the picture.
    airmass: float | None = Field(default=None, alias="airmass")
    altitude_degrees: float | None = Field(default=None, alias="altitudeDegrees")
    azimuth_degrees: float | None = Field(default=None, alias="azimuthDegrees")
    # How much sky each pixel covers, in arcseconds. A smaller number
    # means a more zoomed-in picture.
    pixel_scale_arcsec: float | None = Field(default=None, alias="pixelScaleArcsec")
    # The focal length (zoom level) of the telescope, in millimeters.
    # This must be recorded per-picture because a user might photograph the
    # same target with two different telescopes over time, and those pictures
    # cannot be stacked together directly.
    focal_length_mm: float | None = Field(default=None, alias="focalLengthMm")
    binning: int | None = Field(default=None, alias="binning")
    sensor_temperature_c: float | None = Field(default=None, alias="sensorTemperatureC")
    focuser_position: int | None = Field(default=None, alias="focuserPosition")
    focuser_temperature_c: float | None = Field(default=None, alias="focuserTemperatureC")

    # Everything our own code calculated from this frame's pixels
    # (background level, registration facts), as opposed to the fields
    # above, which are recorded straight from the camera at capture time.
    measurements: FrameMeasurements = Field(default_factory=FrameMeasurements, alias="measurements")

    @model_validator(mode="before")
    @classmethod
    def migrate_flat_measurement_fields(cls, data: Any) -> Any:
        """Reshape an old, flat-measurement-fields record into `measurements`.

        Frames stored before `FrameMeasurements` existed have
        ``backgroundLevel``, ``registrationFwhmXPx``, etc. as siblings
        of ``path``/``camera``/etc. instead of nested under
        ``measurements``. This moves them there before validation, so
        an old row loads into the new shape with no database rewrite.

        Returns
        -------
        data : `Any`
            `data` unchanged if it is not an old-shaped mapping,
            otherwise a copy with the measurement fields nested.
        """
        if not isinstance(data, dict) or "measurements" in data:
            return data
        measurement_keys = set(FrameMeasurements.model_fields) | {
            field.alias for field in FrameMeasurements.model_fields.values() if field.alias
        }
        found = {key: data[key] for key in measurement_keys if key in data}
        if not found:
            return data
        migrated = {key: value for key, value in data.items() if key not in found}
        migrated["measurements"] = found
        return migrated

    @field_validator("filter", mode="before")
    @classmethod
    def normalize_filter(cls, v: Any) -> Any:
        """Turn a filter name typed as text into the official FilterType.

        This runs automatically whenever a `FrameRecord` is created, but
        other code also calls it directly to clean up a filter name it
        got from somewhere else -- see
        `astrometricslib.pipelines.stacking.stage` and
        `astrometricslib.pipelines.shared.frame_grouping`.

        Returns
        -------
        normalized : `Any`
            The matching `FilterType` if `v` is a name it recognizes,
            otherwise `v` unchanged.
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


class TargetStackingResult(BaseModel):
    """One stacking pass's output and quality assessment, nested together.

    `Target` has two of these -- `stacking` for the ordinary imaging
    stack, `spectral_stacking` for the spectroscopy stack -- rather than
    the previous six loosely related flat fields (three of them
    ambiguously named around which stack they belonged to).
    """

    model_config = ConfigDict(populate_by_name=True)

    stacked_image: str = Field(default="", alias="stackedImage")
    processed_image: str = Field(default="", alias="processedImage")
    # A dictionary tracking the finished pictures from every telescope
    # setup used on this target. The key is a label like "CameraName@300mm".
    stacks_by_configuration: dict[str, StackConfigurationResult] = Field(
        default_factory=dict, alias="stacksByConfiguration"
    )
    quality_summary: StackQualitySummary | None = Field(default=None, alias="qualitySummary")


class AsteroidDetectionResult(BaseModel):
    """One asteroid-detection run's candidates and quality assessment."""

    model_config = ConfigDict(populate_by_name=True)

    candidates: list[AsteroidDetectionCandidate] = Field(default_factory=list, alias="candidates")
    quality_summary: AsteroidDetectionQualitySummary | None = Field(default=None, alias="qualitySummary")


class TargetQualitySummaries(BaseModel):
    """The three per-pipeline quality summaries `Target` keeps by itself.

    Astrometry, photometry, and spectroscopy each write their per-star
    findings onto `StellarObject`, not `Target` -- they don't own a
    result the way stacking and asteroid detection do -- so `Target`
    only needs to keep each pipeline's run-level summary, grouped here
    instead of as three flat sibling fields.
    """

    model_config = ConfigDict(populate_by_name=True)

    astrometry: AstrometryQualitySummary | None = Field(default=None, alias="astrometry")
    photometry: PhotometryQualitySummary | None = Field(default=None, alias="photometry")
    spectroscopy: SpectroscopyQualitySummary | None = Field(default=None, alias="spectroscopy")


class TargetObjectType(StrEnum):
    """The kind of sky object a target is, read from its name.

    See `astrometricslib.pipelines.shared.target_classification` for the
    rules. `STAR` is every name that fits no other kind.
    """

    SOLAR_SYSTEM = "solar_system"
    MESSIER = "messier"
    NGC = "ngc"
    IC = "ic"
    COMET = "comet"
    CALIBRATION = "calibration"
    STAR = "star"


class Target(BaseModel):
    """The main record for an astronomical target (like a galaxy or nebula).

    This class only stores data. If stacking images or analyzing
    the target, use the tools in the `TargetCatalog`.
    """

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    id: str = Field(default="", alias="id")
    common_name: str = Field(default="", alias="commonName")
    image_type: ImageType = Field(default=ImageType.TARGET_IMAGE, alias="imageType")
    ra: str = Field(default="0h 0m 0s", alias="ra")
    dec: str = Field(default="0° 0′ 0′′", alias="dec")
    field_of_view: str = Field(default="0′", alias="fieldOfView")
    main_camera: str = Field(default="", alias="mainCamera")
    main_scope: str = Field(default="", alias="mainScope")
    # The ordinary imaging stack -- its finished picture(s) and quality
    # summary, nested together (see `TargetStackingResult`).
    stacking: TargetStackingResult = Field(default_factory=TargetStackingResult, alias="stacking")
    # The spectroscopy stack -- same shape as `stacking`, kept separate
    # since a target can be both imaged and spectroscoped independently.
    spectral_stacking: TargetStackingResult = Field(
        default_factory=TargetStackingResult, alias="spectralStacking"
    )
    asteroid_detection: AsteroidDetectionResult = Field(
        default_factory=AsteroidDetectionResult, alias="asteroidDetection"
    )
    # The astrometry/photometry/spectroscopy run-level quality summaries,
    # grouped together (see `TargetQualitySummaries`).
    quality: TargetQualitySummaries = Field(default_factory=TargetQualitySummaries, alias="quality")
    exposure_sec: float = Field(default=0, alias="exposureTime")
    number_of_stars: int = Field(default=0, alias="numberOfStars")
    frames: list[FrameRecord] = Field(default_factory=list, alias="frames")

    @model_validator(mode="before")
    @classmethod
    def migrate_flat_result_fields(cls, data: Any) -> Any:
        """Reshape an old, flat-field target record into the tiered shape.

        A `Target` stored before this reorganization has its stacking,
        spectral-stacking, asteroid-detection, and per-pipeline quality
        fields as top-level siblings of `id`/`ra`/`dec` instead of
        nested under `stacking`/`spectral_stacking`/`asteroid_detection`/
        `quality`. This reshapes an old-shaped dict into the new one
        before pydantic validates it, so a target stored under the old
        field layout loads correctly with no database rewrite -- it is
        written back out in the new shape the next time it is saved.

        Returns
        -------
        data : `Any`
            `data` unchanged if it is not an old-shaped mapping,
            otherwise a copy reshaped into the tiered structure.
        """
        if not isinstance(data, dict):
            return data
        if any(key in data for key in ("stacking", "spectral_stacking", "asteroid_detection", "quality")):
            return data

        migrated = dict(data)

        def pop_any(*names: str) -> Any:
            for name in names:
                if name in migrated:
                    return migrated.pop(name)
            return None

        stacking = {
            key: value
            for key, value in (
                ("stackedImage", pop_any("stackedImage", "stacked_image")),
                ("processedImage", pop_any("processedImage", "processed_image")),
                ("stacksByConfiguration", pop_any("stacksByConfiguration", "stacks_by_configuration")),
                ("qualitySummary", pop_any("stackQualitySummary", "stack_quality_summary")),
            )
            if value is not None
        }
        if stacking:
            migrated["stacking"] = stacking

        spectral_stacking = {
            key: value
            for key, value in (
                ("stackedImage", pop_any("stackedSpectralTarget", "stacked_spectral_target")),
                (
                    "qualitySummary",
                    pop_any("spectralStackQualitySummary", "spectral_stack_quality_summary"),
                ),
            )
            if value is not None
        }
        if spectral_stacking:
            migrated["spectral_stacking"] = spectral_stacking

        asteroid_detection = {
            key: value
            for key, value in (
                ("candidates", pop_any("asteroidCandidates", "asteroid_candidates")),
                (
                    "qualitySummary",
                    pop_any("asteroidDetectionQualitySummary", "asteroid_detection_quality_summary"),
                ),
            )
            if value is not None
        }
        if asteroid_detection:
            migrated["asteroid_detection"] = asteroid_detection

        quality = {
            key: value
            for key, value in (
                ("astrometry", pop_any("astrometryQualitySummary", "astrometry_quality_summary")),
                ("photometry", pop_any("photometryQualitySummary", "photometry_quality_summary")),
                (
                    "spectroscopy",
                    pop_any("spectroscopyQualitySummary", "spectroscopy_quality_summary"),
                ),
            )
            if value is not None
        }
        if quality:
            migrated["quality"] = quality

        return migrated

    @computed_field(alias="objectType")
    @property
    def object_type(self) -> TargetObjectType:
        """The kind of sky object this is, read from the target's id.

        A planet, the Sun or the Moon; a Messier, NGC or IC number; a comet
        or asteroid; a calibration folder; or otherwise a star.
        """
        from astrometricslib.pipelines.shared.target_classification import classify_target_name

        return classify_target_name(self.id)

    def serialize(self) -> dict[str, Any]:
        """Package the target's data into a basic dictionary format.

        Returns
        -------
        data : `dict[str, Any]`
            The target's fields, using their JSON-friendly names.
        """
        return self.model_dump(mode="python", by_alias=True)

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


class StretchParameters(BaseModel):
    """The automatic stretch used to draw a picture, so a viewer can redo it.

    The stretch maps a pixel value v to ``(v - black_point) / (white_point
    - black_point)``, clipped to 0..1, and then applies the midtones
    transfer function (MTF) with the balance ``midtones``. The black point
    sits 2.8 noise levels below the sky (the median), with the noise taken
    from the median absolute deviation (MAD); the white point is the
    brightest pixel; and the balance puts the sky at 25% brightness. These
    are the PixInsight and Siril defaults.

    Attributes
    ----------
    black_point : `float`
        The pixel value drawn black.
    white_point : `float`
        The pixel value drawn white.
    midtones : `float`
        The midtones balance, between 0 and 1. 0.5 leaves values unchanged.
    """

    model_config = ConfigDict(populate_by_name=True)

    black_point: float = Field(alias="blackPoint")
    white_point: float = Field(alias="whitePoint")
    midtones: float = Field(alias="midtones")


class RenderedImage(BaseModel):
    """A finished picture ready to display in the app, plus brightness stats.

    `Visualization.render_fits(kind="data_url")` returns it.
    """

    model_config = ConfigDict(populate_by_name=True)

    # What the picture shows, such as the target id or the frame's folder.
    id: str = Field(default="", alias="id")
    # The brightness range shown, in pixel values (0 and 255 for a JPEG).
    min: float = Field(alias="min")
    max: float = Field(alias="max")
    # The picture as a ``data:image/png;base64,...`` (or JPEG) URL.
    image_data: str = Field(alias="imageData")
    # The FITS header of the file that was drawn.
    headers: list[FitsHeaderEntry] = Field(default_factory=list, alias="headers")
    # The file that was drawn.
    path: str | None = Field(default=None, alias="path")
    # The automatic stretch the picture was drawn with, or `None` when it
    # was drawn without one (a manual range, a linear view, an image with
    # no measurable sky, or Siril's own preview picture).
    stretch_parameters: StretchParameters | None = Field(default=None, alias="stretchParameters")


class ViewableImage(BaseModel):
    """A picture a client should show as an image, not as text.

    `Visualization.render_fits(kind="image")` returns it. It holds the PNG
    picture and a text description to show beside it.
    """

    # The PNG file.
    png_bytes: bytes
    # What the picture shows: the file, the brightness range, the size and
    # the crop.
    description: dict[str, Any] = Field(default_factory=dict)
    # The automatic stretch the picture was drawn with, or `None` when it
    # was drawn without one.
    stretch_parameters: StretchParameters | None = None
