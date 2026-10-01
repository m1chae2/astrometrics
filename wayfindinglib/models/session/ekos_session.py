"""Purpose: Ekos Session Context Models.

Description: What one Ekos "analyze" log says happened during an observing
session, apart from the guiding samples themselves: where the mount
pointed, how the sensor temperature moved, every exposure that finished
or was aborted, every autofocus run, and the plate-solve and guider state
changes. Ekos is the incumbent control software (Delegated phase,
`Wayfinding_Library_Architecture.md` §2.1.3), so this is recorded
telemetry, not anything this library computed.

`EkosSessionContext` is persisted through the Butler. It keeps only what
later analysis can trust. Ekos writes the mount's right ascension and hour
angle in mixed units (hours in some rows, degrees in others), so those two
fields are deliberately not kept; altitude, azimuth, declination and pier
side are consistent and are enough to place a sample on the sky.
"""

from pydantic import BaseModel, ConfigDict, Field

EKOS_SESSION_CONTEXT_SCHEMA_VERSION = 1
"""Bumped whenever the shape of `EkosSessionContext` changes meaningfully."""


class EkosMountPosition(BaseModel):
    """Where the mount pointed at one moment.

    Attributes
    ----------
    timestamp : `float`
        Seconds since the Unix epoch.
    declination_degrees : `float`
        Declination, in degrees.
    altitude_degrees : `float`
        Altitude above the horizon, in degrees.
    azimuth_degrees : `float`
        Azimuth, in degrees from north through east.
    pier_side : `str` or `None`
        ``"West"`` or ``"East"`` (the side of the pier the telescope is
        on), or `None` if the mount did not say.
    """

    model_config = ConfigDict(populate_by_name=True)

    timestamp: float
    declination_degrees: float = Field(alias="declinationDegrees")
    altitude_degrees: float = Field(alias="altitudeDegrees")
    azimuth_degrees: float = Field(alias="azimuthDegrees")
    pier_side: str | None = Field(default=None, alias="pierSide")


class EkosTemperatureReading(BaseModel):
    """One temperature reading.

    Attributes
    ----------
    timestamp : `float`
        Seconds since the Unix epoch.
    temperature_c : `float`
        Temperature, in degrees Celsius. Ekos takes this from the
        temperature source its Focus module uses, not from the camera
        sensor.
    """

    model_config = ConfigDict(populate_by_name=True)

    timestamp: float
    temperature_c: float = Field(alias="temperatureC")


class EkosCapture(BaseModel):
    """One exposure Ekos finished.

    Attributes
    ----------
    completed_at : `float`
        When the exposure finished, in seconds since the Unix epoch.
    exposure_seconds : `float`
        Exposure length, in seconds.
    filter_name : `str`
        Filter or filter-wheel slot name.
    half_flux_radius_px : `float` or `None`
        Median star half-flux radius Ekos measured, in pixels. `None` if
        Ekos did not measure it (it reports -1 when it could not).
    star_count : `int`
        Number of stars Ekos detected.
    median_adu : `float` or `None`
        Median pixel value of the frame, in ADU.
    eccentricity : `float` or `None`
        Median star eccentricity, from 0 (round) toward 1 (elongated).
        `None` if not measured.
    file_path : `str`
        Where Ekos saved the frame on the telescope computer.
    """

    model_config = ConfigDict(populate_by_name=True)

    completed_at: float = Field(alias="completedAt")
    exposure_seconds: float = Field(alias="exposureSeconds")
    filter_name: str = Field(alias="filterName")
    half_flux_radius_px: float | None = Field(default=None, alias="halfFluxRadiusPx")
    star_count: int = Field(default=0, alias="starCount")
    median_adu: float | None = Field(default=None, alias="medianAdu")
    eccentricity: float | None = None
    file_path: str = Field(default="", alias="filePath")


class EkosAbortedCapture(BaseModel):
    """One exposure that was cancelled before it finished.

    Attributes
    ----------
    timestamp : `float`
        When it was aborted, in seconds since the Unix epoch.
    exposure_seconds : `float`
        The exposure length that was requested, in seconds.
    """

    model_config = ConfigDict(populate_by_name=True)

    timestamp: float
    exposure_seconds: float = Field(alias="exposureSeconds")


class EkosFocusSample(BaseModel):
    """One measurement on an autofocus curve.

    Attributes
    ----------
    position : `int`
        Focuser position, in steps.
    half_flux_radius_px : `float` or `None`
        Star size measured at that position, in pixels. `None` if the
        measurement failed.
    """

    model_config = ConfigDict(populate_by_name=True)

    position: int
    half_flux_radius_px: float | None = Field(default=None, alias="halfFluxRadiusPx")


class EkosAutofocusRun(BaseModel):
    """One autofocus run.

    Attributes
    ----------
    timestamp : `float`
        When the run ended, in seconds since the Unix epoch.
    succeeded : `bool`
        `True` if Ekos finished the run and chose a position; `False` if
        it was aborted.
    temperature_c : `float` or `None`
        Temperature when the run ended, in degrees Celsius.
    filter_name : `str`
        Filter the run was done through.
    curve : `list` [`EkosFocusSample`]
        The measurements taken across the focuser's range.
    final_position : `int` or `None`
        Focuser position Ekos chose. `None` for an aborted run.
    final_half_flux_radius_px : `float` or `None`
        Star size at the chosen position, in pixels.
    """

    model_config = ConfigDict(populate_by_name=True)

    timestamp: float
    succeeded: bool
    temperature_c: float | None = Field(default=None, alias="temperatureC")
    filter_name: str = Field(default="", alias="filterName")
    curve: list[EkosFocusSample] = Field(default_factory=list)
    final_position: int | None = Field(default=None, alias="finalPosition")
    final_half_flux_radius_px: float | None = Field(default=None, alias="finalHalfFluxRadiusPx")


class EkosStateEvent(BaseModel):
    """One change of state in an Ekos module.

    Attributes
    ----------
    timestamp : `float`
        Seconds since the Unix epoch.
    state : `str`
        The new state, in Ekos's words (for example ``"Successful"`` for a
        plate solve, or ``"Reacquiring"`` for the guider).
    """

    model_config = ConfigDict(populate_by_name=True)

    timestamp: float
    state: str


class SessionEquipmentAttribution(BaseModel):
    """Which equipment a session used, as the session's own data shows.

    The guide optics come from the guide log, which records the focal
    length and pixel scale the guider used. The imaging telescope and camera
    come from the frames captured during the session. Both are read from
    data, not assumed from today's configuration, so a session keeps the
    right identity after the equipment is changed.

    Attributes
    ----------
    guide_focal_length_mm : `float` or `None`
        Guide scope focal length the guider used, in millimetres.
    guide_pixel_scale_arcsec_per_px : `float` or `None`
        Guide camera plate scale the guider used, in arcseconds per pixel.
    configured_guide_pixel_scale_arcsec_per_px : `float` or `None`
        The plate scale the configuration gives for the active guide
        equipment when the session was read, for comparison.
    guide_scale_matches_configuration : `bool` or `None`
        Whether the two plate scales agree to within 2 percent. `None` if
        either is unknown.
    imaging_telescope_name : `str` or `None`
        The imaging telescope most of the session's frames were taken with.
    imaging_camera_name : `str` or `None`
        The imaging camera most of the session's frames were taken with.
    imaging_frames_matched : `int`
        How many library frames were found inside the session's time span.
    equipment_fingerprint : `str`
        The setup's fingerprint (see `build_equipment_fingerprint`).
    """

    model_config = ConfigDict(populate_by_name=True)

    guide_focal_length_mm: float | None = Field(default=None, alias="guideFocalLengthMm")
    guide_pixel_scale_arcsec_per_px: float | None = Field(default=None, alias="guidePixelScaleArcsecPerPx")
    configured_guide_pixel_scale_arcsec_per_px: float | None = Field(
        default=None, alias="configuredGuidePixelScaleArcsecPerPx"
    )
    guide_scale_matches_configuration: bool | None = Field(
        default=None, alias="guideScaleMatchesConfiguration"
    )
    imaging_telescope_name: str | None = Field(default=None, alias="imagingTelescopeName")
    imaging_camera_name: str | None = Field(default=None, alias="imagingCameraName")
    imaging_frames_matched: int = Field(default=0, alias="imagingFramesMatched")
    equipment_fingerprint: str = Field(alias="equipmentFingerprint")


class EkosSessionContext(BaseModel):
    """Everything an Ekos analyze log records except guiding samples.

    Attributes
    ----------
    id : `str`
        The analyze file's own timestamp name, for example
        ``"2026-09-23T20-31-48"``. Unique per file, so re-reading a file
        replaces its record instead of adding a second one.
    schema_version : `int`
        Version of this record's layout.
    session_id : `str`
        The observing night the log began in (see
        `astrometricslib.utilities.observing_night`).
    started_at : `float`
        When the log began, in seconds since the Unix epoch.
    ended_at : `float`
        Time of the last event in the log.
    source_file_name : `str`
        Name of the file this was read from.
    mount_positions : `list` [`EkosMountPosition`]
        Mount pointing over time.
    temperatures : `list` [`EkosTemperatureReading`]
        Temperature over time.
    captures : `list` [`EkosCapture`]
        Finished exposures.
    aborted_captures : `list` [`EkosAbortedCapture`]
        Exposures cancelled before finishing.
    autofocus_runs : `list` [`EkosAutofocusRun`]
        Autofocus runs, finished or aborted.
    align_events : `list` [`EkosStateEvent`]
        Plate-solve state changes.
    guide_state_events : `list` [`EkosStateEvent`]
        Guider state changes (selecting a star, calibrating, guiding,
        reacquiring and so on).
    mount_state_events : `list` [`EkosStateEvent`]
        Mount state changes (parked, slewing, tracking and so on).
    equipment : `SessionEquipmentAttribution` or `None`
        Which equipment the session used. `None` until it has been worked
        out from the session's logs and frames.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    schema_version: int = Field(default=EKOS_SESSION_CONTEXT_SCHEMA_VERSION, alias="schemaVersion")
    session_id: str = Field(alias="sessionId")
    started_at: float = Field(alias="startedAt")
    ended_at: float = Field(alias="endedAt")
    source_file_name: str = Field(default="", alias="sourceFileName")
    mount_positions: list[EkosMountPosition] = Field(default_factory=list, alias="mountPositions")
    temperatures: list[EkosTemperatureReading] = Field(default_factory=list)
    captures: list[EkosCapture] = Field(default_factory=list)
    aborted_captures: list[EkosAbortedCapture] = Field(default_factory=list, alias="abortedCaptures")
    autofocus_runs: list[EkosAutofocusRun] = Field(default_factory=list, alias="autofocusRuns")
    align_events: list[EkosStateEvent] = Field(default_factory=list, alias="alignEvents")
    guide_state_events: list[EkosStateEvent] = Field(default_factory=list, alias="guideStateEvents")
    mount_state_events: list[EkosStateEvent] = Field(default_factory=list, alias="mountStateEvents")
    equipment: SessionEquipmentAttribution | None = None


class EkosGuideStat(BaseModel):
    """One guiding measurement from an Ekos analyze log.

    Attributes
    ----------
    timestamp : `float`
        Seconds since the Unix epoch.
    ra_error_arcsec : `float`
        Guide star offset along RA, in arcseconds.
    dec_error_arcsec : `float`
        Guide star offset along Dec, in arcseconds.
    ra_pulse : `float`
        Signed RA correction Ekos issued. Its unit is not stated by Ekos;
        the guide log's correction in milliseconds is the reliable source.
    dec_pulse : `float`
        Signed Dec correction Ekos issued, in the same unit.
    snr : `float`
        Guide star signal-to-noise ratio.
    sky_background : `float`
        Sky background level in the guide frame.
    star_count : `int`
        Stars found in the guide frame.
    """

    model_config = ConfigDict(populate_by_name=True)

    timestamp: float
    ra_error_arcsec: float = Field(alias="raErrorArcsec")
    dec_error_arcsec: float = Field(alias="decErrorArcsec")
    ra_pulse: float = Field(alias="raPulse")
    dec_pulse: float = Field(alias="decPulse")
    snr: float
    sky_background: float = Field(alias="skyBackground")
    star_count: int = Field(alias="starCount")
