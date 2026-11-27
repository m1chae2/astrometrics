"""Data structures for tracking astronomical targets.

This module defines the pure data classes (like Target and FrameRecord)
used to store information about the objects being photographed.
These classes only hold data; the actual work (stacking, analysis)
happens elsewhere to keep the code organized and avoid import errors.
"""

from enum import StrEnum
from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from astrometricslib.foundation.enums import FilterType
from astrometricslib.models.moving_object import AsteroidDetectionCandidate
from astrometricslib.models.quality.quality_summary import (
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
    "ImageCrop",
    "ImageType",
    "RenderedImage",
    "StretchParameters",
    "Target",
    "TargetObjectType",
    "TargetQualitySummaries",
    "TargetStackingResult",
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
    # The version of the method that gave `background_level`
    # (`BACKGROUND_LEVEL_METHOD` in `background_measurement.py`). A level
    # stored with no version, or with an older one, was measured another way
    # (version 1 mixed the color pixels of a raw color mosaic), so it is
    # measured again before it is compared with a newer level.
    background_level_method: int | None = Field(default=None, alias="backgroundLevelMethod")
    saturated_pixel_fraction: float | None = Field(default=None, alias="saturatedPixelFraction")
    # The level, in ADU, at or above which a pixel was counted as saturated
    # when `saturated_pixel_fraction` was measured. Records written before
    # this field existed leave it empty, so the stacking stage measures the
    # fraction again when this is missing or no longer matches the camera
    # profile's pixel-saturation level.
    saturated_pixel_fraction_limit_adu: float | None = Field(
        default=None, alias="saturatedPixelFractionLimitAdu"
    )
    # Star sharpness measured directly by the code, rather than by Siril.
    # This number cannot be directly compared to `registration_fwhm_x_px`.
    measured_fwhm_px: float | None = Field(default=None, alias="measuredFwhmPx")

    # Alignment data. When the pipeline aligns the images (registration),
    # it calculates these values (like how far the stars shifted).
    # They stay None until that pipeline runs.
    # Siril's plain FWHM in pixels. The name says "x" for history; Siril
    # reports one FWHM, not one per axis.
    registration_fwhm_x_px: float | None = Field(default=None, alias="registrationFwhmXPx")
    # Siril's weighted FWHM in pixels (stars counted by fit quality).
    registration_weighted_fwhm_px: float | None = Field(default=None, alias="registrationWeightedFwhmPx")
    # How round the stars look after alignment (1.0 is a perfect circle).
    # A lower number can mean the telescope drifted during the photo.
    registration_roundness: float | None = Field(default=None, alias="registrationRoundness")
    # Siril's registration quality score for this picture.
    registration_quality: float | None = Field(default=None, alias="registrationQuality")
    # Sky background level in the picture, normalized to 0 to 1.
    registration_background_level: float | None = Field(default=None, alias="registrationBackgroundLevel")
    # Rotation of this picture relative to the reference, in degrees,
    # from the registration transform.
    registration_rotation_deg: float | None = Field(default=None, alias="registrationRotationDeg")
    registration_star_count: int | None = Field(default=None, alias="registrationStarCount")
    registration_dx_px: float | None = Field(default=None, alias="registrationDxPx")
    registration_dy_px: float | None = Field(default=None, alias="registrationDyPx")


class FrameRecord(BaseModel):
    """A single raw photograph and its settings (like gain and exposure)."""

    path: str = Field(alias="path")
    filter: FilterType = Field(default=FilterType.NONE, alias="filter")
    role: str = Field(default="LIGHT", alias="role")  # LIGHT, DARK, FLAT, BIAS
    # The camera's gain setting, or the ISO of a DSLR (a plain number, not
    # electrons per ADU), or None when it is not known. Records saved before
    # the rename hold it as text under ``iso``.
    gain: int | None = Field(default=800, alias="gain", validation_alias=AliasChoices("gain", "iso"))
    # The constant the camera adds to every pixel.
    offset: int = Field(default=0, alias="offset")
    # The exposure time of the frame, in seconds. Records saved before the
    # rename hold it as text under ``exposure``.
    exposure_s: float = Field(
        default=1.0, alias="exposureS", validation_alias=AliasChoices("exposureS", "exposure_s", "exposure")
    )
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
    altitude_deg: float | None = Field(
        default=None,
        alias="altitudeDeg",
        validation_alias=AliasChoices("altitudeDeg", "altitude_deg", "altitudeDegrees", "altitude_degrees"),
    )
    azimuth_deg: float | None = Field(
        default=None,
        alias="azimuthDeg",
        validation_alias=AliasChoices("azimuthDeg", "azimuth_deg", "azimuthDegrees", "azimuth_degrees"),
    )
    # How much sky each pixel covers, in arcseconds. A smaller number
    # means a more zoomed-in picture.
    pixel_scale_arcsec: float | None = Field(default=None, alias="pixelScaleArcsec")
    # The focal length (zoom level) of the telescope, in millimeters.
    # This must be recorded per-picture because a user might photograph the
    # same target with two different telescopes over time, and those pictures
    # cannot be stacked together directly.
    focal_length_mm: float | None = Field(default=None, alias="focalLengthMm")
    # The id of the observatory configuration (the set of equipment) the
    # frame was taken with, matched by camera, focal length and night. None
    # until the library's configurations were seeded, or when no stored
    # configuration has this camera and focal length.
    configuration_id: str | None = Field(default=None, alias="configurationId")
    binning: int | None = Field(default=None, alias="binning")
    sensor_temperature_c: float | None = Field(default=None, alias="sensorTemperatureC")
    focuser_position: int | None = Field(default=None, alias="focuserPosition")
    focuser_temperature_c: float | None = Field(default=None, alias="focuserTemperatureC")

    # The file's size in bytes and last-change time in nanoseconds, from
    # `os.stat`, when the header was last read. Re-indexing compares them
    # with the file now and skips reading a file that has not changed.
    # Records saved before these existed have None and are read once.
    file_size: int | None = Field(default=None, alias="fileSize")
    file_modified_ns: int | None = Field(default=None, alias="fileModifiedNs")
    # Which set of header fields the last header read filled in
    # (`frame_scanning.HEADER_FIELDS_VERSION`). A record read before a field
    # was added to that set is read once more even when its file is unchanged,
    # so the new field is filled in. None for a record saved before this
    # existed.
    header_fields_version: int | None = Field(default=None, alias="headerFieldsVersion")

    # The file size and last-change time the frame had when its quality
    # measurements were last recorded (`record_frame_measurements`). A frame
    # whose file still matches these has nothing new to measure. Both are None
    # for a frame that was never measured; they are kept apart from
    # `file_size` and `file_modified_ns` because a header re-read and a pixel
    # measurement happen at different times.
    quality_measured_file_size: int | None = Field(default=None, alias="qualityMeasuredFileSize")
    quality_measured_file_modified_ns: int | None = Field(default=None, alias="qualityMeasuredFileModifiedNs")
    # A short fingerprint of the frame metrics (their names and definition
    # versions) the frame's last measurement covered
    # (`frame_metric_set_fingerprint`). A frame whose fingerprint differs from
    # the current one was measured before a metric was added or redefined, so
    # it is measured again. None for a frame measured before this field
    # existed.
    quality_measured_metric_set: str | None = Field(default=None, alias="qualityMeasuredMetricSet")

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

    @field_validator("gain", mode="before")
    @classmethod
    def read_gain_number(cls, value: Any) -> Any:
        """Read a gain stored as text, such as ``"800"``, as a number.

        Records saved before this field was a number hold it as text under
        ``iso``. Text that holds no number, such as ``"Auto"``, means the
        gain is not known.

        Returns
        -------
        gain : `int` or `None`
            The gain as a whole number, or `None` when it is not known.
        """
        from astrometricslib.utilities.iso_text import setting_number

        return setting_number(value)

    @field_validator("offset", mode="before")
    @classmethod
    def read_offset_number(cls, value: Any) -> Any:
        """Read an offset stored as text, such as ``"10"``, as a number.

        Records saved before this field was a number hold it as text. A
        missing offset, or text that holds no number, counts as 0, the way
        the calibration library files a frame with no ``OFFSET`` card.

        Returns
        -------
        offset : `int`
            The offset as a whole number.
        """
        from astrometricslib.utilities.iso_text import setting_number

        number = setting_number(value)
        return 0 if number is None else number

    @field_validator("exposure_s", mode="before")
    @classmethod
    def read_exposure_number(cls, value: Any) -> Any:
        """Read an exposure stored as text, such as ``"30.0"``, as a number.

        Records saved before this field was a number hold it as text. Text
        that holds no number gives the 1 second stand-in a header with no
        ``EXPTIME`` gets.

        Returns
        -------
        exposure_s : `Any`
            The exposure in seconds.
        """
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return 1.0
        return 1.0 if value is None else value

    @property
    def is_spectral(self) -> bool:
        """Whether this is a spectroscopy frame.

        This is worked out from the filter every time and is not saved with
        the record.

        Returns
        -------
        is_spectral : `bool`
            `True` when the filter is the spectroscopy grating.
        """
        return self.filter == FilterType.SPEC

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
    # The observatory configuration the stacked frames point at (their
    # ``configuration_id``), or `None` for a stack recorded before frames
    # had one, or made from frames of several configurations. Such a stack
    # is matched to a configuration by ``configuration_key`` instead.
    configuration_id: str | None = Field(default=None, alias="configurationId")
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
    # The color picture of a monochrome stack, with its color taken from a
    # color stack of another setup (see `color_from_other_stack`). Empty when
    # there is none. It sits beside the stack's own picture and never
    # replaces it.
    color_preview_image: str = Field(default="", alias="colorPreviewImage")
    # A dictionary tracking the finished pictures from every telescope
    # setup used on this target. The key is a label like "CameraName@300mm".
    stacks_by_configuration: dict[str, StackConfigurationResult] = Field(
        default_factory=dict, alias="stacksByConfiguration"
    )
    quality_summary: StackQualitySummary | None = Field(default=None, alias="qualitySummary")

    @property
    def preview_path(self) -> str | None:
        """Where the stack's preview picture (JPEG) is, or would be.

        The name comes from the stack's file name, so this is worked out
        every time and is not saved. The file may not exist.

        Returns
        -------
        preview_path : `str` or `None`
            The preview picture's path, or `None` when there is no stack.
        """
        if not self.stacked_image:
            return None
        from astrometricslib.utilities.stack_preview_path import preview_path_for

        return preview_path_for(self.stacked_image)


class AsteroidDetectionResult(BaseModel):
    """One asteroid-detection run's candidates and quality assessment."""

    model_config = ConfigDict(populate_by_name=True)

    candidates: list[AsteroidDetectionCandidate] = Field(default_factory=list, alias="candidates")
    quality_summary: AsteroidDetectionQualitySummary | None = Field(default=None, alias="qualitySummary")


class TargetQualitySummaries(BaseModel):
    """The per-pipeline quality summaries `Target` keeps by itself.

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
    # The summary of the spectral variation stage, which follows the stars
    # across the raw spectral frames. It is kept apart from `spectroscopy`,
    # which describes the master stack, so neither replaces the other.
    spectroscopy_variation: SpectroscopyQualitySummary | None = Field(
        default=None, alias="spectroscopyVariation"
    )


class TargetObjectType(StrEnum):
    """The kind of sky object a target is, read from its name.

    See `astrometricslib.utilities.target_classification` for the
    rules. `STAR` is every name that fits no other kind.
    """

    SOLAR_SYSTEM = "solar_system"
    MESSIER = "messier"
    NGC = "ngc"
    IC = "ic"
    COMET = "comet"
    CALIBRATION = "calibration"
    STAR = "star"


class PreviewBrightObject(BaseModel):
    """Which object the preview's masked stretch protects, as the user chose.

    Attributes
    ----------
    mode : `str`
        ``"auto"`` (the default) lets the stretch use the target's own
        catalog position and kind. ``"off"`` never uses the masked stretch.
        ``"object"`` protects the object named below, whatever the target's
        kind.
    simbad_id : `str` or `None`
        SIMBAD's main name of the chosen object (mode ``"object"``).
    ra_deg : `float` or `None`
        The chosen object's right ascension in degrees (ICRS, J2000).
    dec_deg : `float` or `None`
        The chosen object's declination in degrees.
    major_axis_arcmin : `float` or `None`
        SIMBAD's major axis of the chosen object in arcminutes, or `None`
        when SIMBAD gives none (then only its position is checked).
    """

    model_config = ConfigDict(populate_by_name=True)

    mode: str = Field(default="auto", alias="mode", pattern="^(auto|off|object)$")
    simbad_id: str | None = Field(default=None, alias="simbadId")
    ra_deg: float | None = Field(default=None, alias="raDeg")
    dec_deg: float | None = Field(default=None, alias="decDeg")
    major_axis_arcmin: float | None = Field(default=None, alias="majorAxisArcmin")


class Target(BaseModel):
    """The main record for an astronomical target (like a galaxy or nebula).

    This class only stores data. If stacking images or analyzing
    the target, use the tools in the `TargetCatalog`.
    """

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    id: str = Field(default="", alias="id")
    common_name: str = Field(default="", alias="commonName")
    image_type: ImageType = Field(default=ImageType.TARGET_IMAGE, alias="imageType")
    # The object's position as ICRS coordinates at epoch J2000 (what SIMBAD
    # gives). A double star holds its primary; a companion needs a target id
    # of its own. Setting `ra` or `dec` by hand clears `position_unverified`.
    ra: str = Field(default="0h 0m 0s", alias="ra")
    dec: str = Field(default="0° 0′ 0′′", alias="dec")
    # True when `ra`/`dec` came from the center of a solved image because
    # the target's name could not be trusted in a catalog (not found, found
    # outside the image, or its id and common name disagreed). That point is
    # where the camera looked, not necessarily where the object is.
    position_unverified: bool = Field(default=False, alias="positionUnverified")
    # SIMBAD's main object type code for the object ("G" galaxy, "GlC"
    # globular cluster, "PN" planetary nebula, "*" star, ...), or None when it
    # is not known. It is filled in when the position is looked up in SIMBAD,
    # and by `astrometricslib.scripts.backfill_target_object_types` for targets
    # saved before it was recorded. `target_object_category` groups the codes.
    simbad_object_type: str | None = Field(default=None, alias="simbadObjectType")
    # SIMBAD's major axis of the object (``galdim_majaxis``), in arcminutes,
    # or None for a point source, an object SIMBAD gives no size for, or a
    # target saved before it was recorded
    # (`astrometricslib.scripts.backfill_target_sizes` fills those in). A run
    # records it as the target-size basis (issue #175), so the values sized to
    # the target follow it.
    major_axis_arcmin: float | None = Field(default=None, alias="majorAxisArcmin")
    # Which object the preview's masked stretch protects (see
    # `astrometricslib.pipelines.stacking.post_processing.masked_stretch`).
    # The default, "auto", is the rule used before the choice existed. The
    # choice is read the next time the stack's preview is made.
    preview_bright_object: PreviewBrightObject = Field(
        default_factory=PreviewBrightObject, alias="previewBrightObject"
    )
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
    # The total exposure time of all the frames, in seconds. Records saved
    # before the rename hold it under ``exposureTime``.
    exposure_s: float = Field(
        default=0,
        alias="exposureS",
        validation_alias=AliasChoices("exposureS", "exposure_s", "exposureTime", "exposure_sec"),
    )
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
        from astrometricslib.utilities.target_classification import classify_target_name

        return classify_target_name(self.id)

    @property
    def object_category(self) -> str | None:
        """The broad kind of sky object, from SIMBAD's type code and the id.

        This is worked out every time and is not saved.

        Returns
        -------
        category : `str` or `None`
            ``"galaxy"``, ``"nebula"``, ``"cluster"``, ``"star"``,
            ``"solar_system"``, or `None` when the type is not known.
        """
        from astrometricslib.utilities.target_object_category import target_object_category

        return target_object_category(self.simbad_object_type, self.id)

    @property
    def ra_deg(self) -> float:
        """The right ascension (the east-west sky coordinate) in degrees.

        This is read from the text in `ra` every time and is not saved.

        Returns
        -------
        ra_degrees : `float`
            The right ascension, in decimal degrees.

        Notes
        -----
        Raises `InvalidArgumentError` if `ra` cannot be read as an angle.
        """
        from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string

        return parse_coordinate_string(self.ra, is_ra=True)

    @property
    def dec_deg(self) -> float:
        """The declination (the north-south sky coordinate) in degrees.

        This is read from the text in `dec` every time and is not saved.

        Returns
        -------
        dec_degrees : `float`
            The declination, in decimal degrees.

        Notes
        -----
        Raises `InvalidArgumentError` if `dec` cannot be read as an angle
        or lies outside -90 to 90 degrees.
        """
        from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string

        return parse_coordinate_string(self.dec, is_ra=False)

    def frame_subject_id(self, frame: FrameRecord | str) -> str:
        """Name one of this target's frames the way quality rows name it.

        The id is the target's id followed by the part of the frame's path
        below the target's lights folder, so it does not change when the
        frames root moves to another mount. It is the ``subject_id`` of the
        frame's quality rows.

        Parameters
        ----------
        frame : `FrameRecord` or `str`
            The frame, or its path.

        Returns
        -------
        subject_id : `str`
            ``"<target id>/<path below the target's lights folder>"``.
        """
        from astrometricslib.utilities.frame_identity import frame_subject_id

        return frame_subject_id(frame if isinstance(frame, str) else frame.path, self.id)

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
        total = float(sum(frame.exposure_s for frame in self.frames))
        self.exposure_s = total
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


class ImageCrop(BaseModel):
    """The piece of an image a picture shows, in full-image pixels.

    Attributes
    ----------
    left_px, top_px : `int`
        The column and row of the piece's first pixel, counted from 1.
    width_px, height_px : `int`
        The piece's size in pixels.
    """

    model_config = ConfigDict(frozen=True)

    left_px: int
    top_px: int
    width_px: int
    height_px: int


class RenderedImage(BaseModel):
    """A picture of an image, as the bytes of a PNG or JPEG file.

    `Visualization.render` returns it. A Jupyter notebook shows it as a
    picture. A client that needs text, such as a web page, turns
    ``picture`` into a data URL itself.

    Attributes
    ----------
    picture : `bytes`
        The picture file.
    media_type : `str`
        ``"image/png"``, or ``"image/jpeg"`` for the stretched picture the
        enhancement stage saved beside a stack.
    path : `str` or `None`
        The file that was drawn, or `None` for an array.
    black, white : `float`
        The pixel values drawn black and white. 0 and 255 for the saved
        JPEG picture.
    width_px, height_px : `int`
        The picture's size.
    full_width_px, full_height_px : `int` or `None`
        The whole image's size. `None` for the saved JPEG picture.
    crop : `ImageCrop` or `None`
        The piece of the image shown, or `None` for the whole image.
    stretched : `bool`
        Whether the automatic stretch (or the saved stretched picture) was
        used.
    stretch_parameters : `StretchParameters` or `None`
        The automatic stretch the picture was drawn with, or `None` when it
        was drawn without one (a manual range, a linear view, an image with
        no measurable sky, or the saved JPEG picture).
    """

    model_config = ConfigDict(frozen=True)

    picture: bytes
    media_type: str = "image/png"
    path: str | None = None
    black: float
    white: float
    width_px: int
    height_px: int
    full_width_px: int | None = None
    full_height_px: int | None = None
    crop: ImageCrop | None = None
    stretched: bool = True
    stretch_parameters: StretchParameters | None = None

    def __repr__(self) -> str:
        """Describe the picture in one short line.

        Returns
        -------
        text : `str`
            The file name, the picture's size and its type.
        """
        name = self.path.replace("\\", "/").rsplit("/", 1)[-1] if self.path else "array"
        return f"RenderedImage({name!r}, {self.width_px}x{self.height_px} px, {self.media_type})"

    def _repr_mimebundle_(self, include: Any = None, exclude: Any = None) -> dict[str, bytes]:
        """Let Jupyter show the picture itself.

        Parameters
        ----------
        include, exclude : `Any`, optional
            Jupyter's choice of formats. Not used.

        Returns
        -------
        bundle : `dict` [`str`, `bytes`]
            The picture under its media type.
        """
        return {self.media_type: self.picture}
