"""Purpose: Guiding Run Summary Model.

Description: One continuous stretch of guiding, as the guide log recorded
it, reduced to the facts that say how good the data gathering was: how many
frames the guider took, how many it lost, which optics and calibrated mount
speeds it used, and where the telescope pointed. The individual samples live
in the guiding log; this record holds what the samples cannot show, such as
frames that were lost and therefore never became samples.

The session-quality analysis reads these records to judge whether a night's
guiding data can be trusted before it analyses the guiding itself.
"""

from pydantic import BaseModel, ConfigDict, Field

GUIDING_RUN_SCHEMA_VERSION = 1
"""Bumped whenever the shape of `GuidingRunSummary` changes meaningfully."""


class GuidingRunSummary(BaseModel):
    """One continuous stretch of guiding.

    Attributes
    ----------
    id : `str`
        The guide log's file name and the run's position in it, for example
        ``"guide_log-2026-09-24T20-43-58.txt#3"``. Unique per run, so
        reading a file again replaces its runs instead of adding copies.
    schema_version : `int`
        Version of this record's layout.
    session_id : `str`
        The observing night the run began in.
    source_file_name : `str`
        The guide log the run was read from.
    written_by_ekos : `bool`
        `True` if the Ekos internal guider wrote the log, `False` for PHD2.
    started_at : `float`
        When guiding began, in seconds since the Unix epoch.
    ended_at : `float` or `None`
        When guiding ended, or `None` if the log stops before saying so.
    pixel_scale_arcsec_per_px : `float` or `None`
        Guide camera plate scale the guider used, in arcseconds per pixel.
    focal_length_mm : `float` or `None`
        Guide scope focal length the guider used, in millimetres.
    ra_rate_arcsec_per_second : `float` or `None`
        Calibrated mount speed along RA, in arcseconds per second.
    dec_rate_arcsec_per_second : `float` or `None`
        Calibrated mount speed along Dec, in arcseconds per second.
    frames_total : `int`
        Frames the guider took in this run.
    frames_lost : `int`
        Frames the log marked as failed, for example a lost guide star.
    frames_without_pixel_scale : `int`
        Frames that could not become samples because no plate scale was
        known.
    samples_stored : `int`
        Frames that became guiding samples.
    declination_degrees : `float` or `None`
        Mount declination when guiding began, in degrees.
    altitude_degrees : `float` or `None`
        Altitude when guiding began, in degrees.
    azimuth_degrees : `float` or `None`
        Azimuth when guiding began, in degrees.
    pier_side : `str` or `None`
        ``"East"`` or ``"West"``, the side of the pier the telescope was on.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    schema_version: int = Field(default=GUIDING_RUN_SCHEMA_VERSION, alias="schemaVersion")
    session_id: str = Field(alias="sessionId")
    source_file_name: str = Field(alias="sourceFileName")
    written_by_ekos: bool = Field(alias="writtenByEkos")
    started_at: float = Field(alias="startedAt")
    ended_at: float | None = Field(default=None, alias="endedAt")
    pixel_scale_arcsec_per_px: float | None = Field(default=None, alias="pixelScaleArcsecPerPx")
    focal_length_mm: float | None = Field(default=None, alias="focalLengthMm")
    ra_rate_arcsec_per_second: float | None = Field(default=None, alias="raRateArcsecPerSecond")
    dec_rate_arcsec_per_second: float | None = Field(default=None, alias="decRateArcsecPerSecond")
    frames_total: int = Field(default=0, alias="framesTotal")
    frames_lost: int = Field(default=0, alias="framesLost")
    frames_without_pixel_scale: int = Field(default=0, alias="framesWithoutPixelScale")
    samples_stored: int = Field(default=0, alias="samplesStored")
    declination_degrees: float | None = Field(default=None, alias="declinationDegrees")
    altitude_degrees: float | None = Field(default=None, alias="altitudeDegrees")
    azimuth_degrees: float | None = Field(default=None, alias="azimuthDegrees")
    pier_side: str | None = Field(default=None, alias="pierSide")

    @property
    def duration_seconds(self) -> float | None:
        """Length of the run, in seconds.

        Returns
        -------
        duration : `float` or `None`
            `ended_at` minus `started_at`, or `None` if the log never said
            when the run ended.
        """
        return None if self.ended_at is None else self.ended_at - self.started_at

    @property
    def lost_fraction(self) -> float | None:
        """Share of the run's frames that were lost.

        Returns
        -------
        fraction : `float` or `None`
            `frames_lost` over `frames_total`, or `None` for a run with no
            frames.
        """
        return self.frames_lost / self.frames_total if self.frames_total else None
