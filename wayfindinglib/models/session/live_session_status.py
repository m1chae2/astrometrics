"""Purpose: Data structures for the live observing-session status.

Description: The live status answers "what is the telescope doing right
now, and is it going well?" from the records Ekos has written so far. It is
computed fresh each time and never stored, so a record here can go stale
the moment the session moves on.

Times are seconds since the Unix epoch. Errors are arcseconds on the sky.
"""

from pydantic import BaseModel, ConfigDict, Field


class DitherEvent(BaseModel):
    """One dither, read from the KStars log.

    Attributes
    ----------
    timestamp : `float`
        When the dither began.
    amplitude_px : `float`
        The requested move, in guide-camera pixels.
    succeeded : `bool` or `None`
        `True` if the guide star reached its new position, `False` if the
        guider gave up after its allowed tries, `None` if the log ends
        before the dither does.
    settle_seconds : `float` or `None`
        The wait the guider applied after the move, before the next
        exposure could start.
    duration_seconds : `float` or `None`
        Time from the start of the dither to its completion message.
    """

    model_config = ConfigDict(populate_by_name=True)

    timestamp: float
    amplitude_px: float
    succeeded: bool | None = None
    settle_seconds: float | None = None
    duration_seconds: float | None = None


class GuidingWindowSummary(BaseModel):
    """Guiding accuracy over a recent stretch of time.

    Attributes
    ----------
    window_seconds : `float`
        The length of the stretch, ending at the latest record.
    sample_count : `int`
        Guiding measurements inside it.
    ra_rms_arcsec, dec_rms_arcsec, total_rms_arcsec : `float` or `None`
        Root-mean-square guide error along RA, along Dec, and combined.
        `None` when there are no samples.
    """

    model_config = ConfigDict(populate_by_name=True)

    window_seconds: float
    sample_count: int
    ra_rms_arcsec: float | None = None
    dec_rms_arcsec: float | None = None
    total_rms_arcsec: float | None = None


class GuidingExcursion(BaseModel):
    """A stretch where the guide error ran far above its normal level.

    Attributes
    ----------
    started_at, ended_at : `float`
        First and last measurement of the excursion.
    peak_error_arcsec : `float`
        The largest total guide error inside it.
    axis : `str`
        ``"RA"`` or ``"Dec"``, whichever carried the larger error.
    kind : `str`
        ``"jump"`` if the error arrived at full size in one step (the
        field moved suddenly), ``"drift"`` if it built up over time.
    drift_rate_arcsec_per_second : `float` or `None`
        How fast the error grew along that axis, from a straight-line fit.
        `None` with fewer than three measurements.
    matches_stopped_ra_axis : `bool` or `None`
        For an RA excursion: whether the drift rate is within 25% of the
        rate a motionless mount drifts at the current declination
        (15.04 arcsec per second times the cosine of the declination).
        `None` for Dec excursions or when the declination is unknown.
    reacquire_count : `int`
        How many times the guider re-locked during the excursion.
    """

    model_config = ConfigDict(populate_by_name=True)

    started_at: float
    ended_at: float
    peak_error_arcsec: float
    axis: str
    kind: str = "drift"
    drift_rate_arcsec_per_second: float | None = None
    matches_stopped_ra_axis: bool | None = None
    reacquire_count: int = 0


class ExposureSummary(BaseModel):
    """One finished exposure and how guiding behaved during it.

    Attributes
    ----------
    frame_name : `str`
        The saved file's name.
    completed_at : `float`
        When the exposure finished.
    exposure_seconds : `float`
        Its length.
    star_count : `int`
        Stars Ekos found in the frame.
    half_flux_radius_px : `float` or `None`
        Median star half-flux radius, in pixels.
    eccentricity : `float` or `None`
        Median star eccentricity (0 is round).
    guide_rms_first_seconds_arcsec : `float` or `None`
        Guide error (total, root-mean-square) in the first stretch of the
        exposure, where a dither's after-effects show.
    guide_rms_rest_arcsec : `float` or `None`
        Guide error over the rest of the exposure.
    follows_dither : `bool`
        Whether a dither finished shortly before this exposure began.
    flags : `list` [`str`]
        One sentence per problem found. Empty for a clean frame.
    """

    model_config = ConfigDict(populate_by_name=True)

    frame_name: str
    completed_at: float
    exposure_seconds: float
    star_count: int
    half_flux_radius_px: float | None = None
    eccentricity: float | None = None
    guide_rms_first_seconds_arcsec: float | None = None
    guide_rms_rest_arcsec: float | None = None
    follows_dither: bool = False
    flags: list[str] = Field(default_factory=list)


class LiveSessionStatus(BaseModel):
    """The state of the current observing session.

    Attributes
    ----------
    analyze_file, kstars_log_file : `str` or `None`
        The files the status was read from.
    as_of : `float`
        Time of the latest record in the analyze file.
    seconds_since_last_record : `float` or `None`
        How long ago that was by the wall clock. A large value means Ekos
        has stopped writing (or the file was not refreshed).
    guider_state : `str` or `None`
        The guider's latest state, such as ``"Guiding"``.
    guider_state_since : `float` or `None`
        When it entered that state.
    declination_degrees : `float` or `None`
        The mount's latest declination.
    temperature_c : `float` or `None`
        The latest temperature reading.
    recent_guiding : `GuidingWindowSummary`
        Guiding accuracy over the requested window.
    exposures : `list` [`ExposureSummary`]
        The most recent finished exposures, oldest first.
    excursions : `list` [`GuidingExcursion`]
        Every excursion in the file.
    dithers : `list` [`DitherEvent`]
        Every dither in the KStars log.
    last_autofocus_at : `float` or `None`
        When the latest successful autofocus ended.
    last_autofocus_temperature_c : `float` or `None`
        The temperature at that time.
    flags : `list` [`str`]
        The session-level problems, one sentence each, most recent first.
    """

    model_config = ConfigDict(populate_by_name=True)

    analyze_file: str
    kstars_log_file: str | None = None
    as_of: float
    seconds_since_last_record: float | None = None
    guider_state: str | None = None
    guider_state_since: float | None = None
    declination_degrees: float | None = None
    temperature_c: float | None = None
    recent_guiding: GuidingWindowSummary
    exposures: list[ExposureSummary] = Field(default_factory=list)
    excursions: list[GuidingExcursion] = Field(default_factory=list)
    dithers: list[DitherEvent] = Field(default_factory=list)
    last_autofocus_at: float | None = None
    last_autofocus_temperature_c: float | None = None
    flags: list[str] = Field(default_factory=list)
