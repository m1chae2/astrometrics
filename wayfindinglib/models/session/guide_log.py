"""Purpose: Guide Log Parse Results.

Description: What `wayfindinglib.drivers.phd2.guide_log_parser` reads out of
one PHD2-format guide log file. PHD2 writes this format itself, and so
does the Ekos internal guider in KStars (its files say "PHD2 log version
2.5"), so one parser covers both.

A log is a series of sections. A calibration section records how the
mount responds to guide pulses. A guiding section records the guide
star's measured position error frame by frame, together with the
equipment and sky position that applied while it ran. These classes
keep that context next to the samples, because the context (for example
the pixel scale) is what turns a pixel offset into arcseconds.

These are plain parse results, not persisted models: the samples go to
the guiding log database, and the context is summarised into the session
record by the ingestion code.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GuideCalibration:
    """One guide calibration the log records.

    Attributes
    ----------
    started_at : `float`
        When the calibration began, in seconds since the Unix epoch.
    ra_rate_arcsec_per_second : `float` or `None`
        Measured mount speed along RA while guiding, in arcseconds per
        second. `None` if the log gave no value.
    dec_rate_arcsec_per_second : `float` or `None`
        Measured mount speed along Dec, in arcseconds per second.
    camera_angle_deg : `float` or `None`
        Angle between the guide camera's x axis and the RA axis, in degrees.
    """

    started_at: float
    ra_rate_arcsec_per_second: float | None = None
    dec_rate_arcsec_per_second: float | None = None
    camera_angle_deg: float | None = None


@dataclass
class GuidingSection:
    """One continuous stretch of guiding, with the context it ran in.

    Attributes
    ----------
    started_at : `float`
        When guiding began, in seconds since the Unix epoch.
    ended_at : `float` or `None`
        When guiding ended, or `None` if the file stops before it says so
        (a log still being written, or one cut off by a crash).
    pixel_scale_arcsec_per_px : `float` or `None`
        Guide camera plate scale the guider used, in arcseconds per pixel.
        `None` if the log did not say; samples cannot be converted to
        arcseconds without it.
    binning : `int` or `None`
        Guide camera binning factor.
    focal_length_mm : `float` or `None`
        Guide scope focal length the guider used, in millimetres.
    right_ascension_hours : `float` or `None`
        Mount right ascension when guiding began, in hours.
    declination_degrees : `float` or `None`
        Mount declination when guiding began, in degrees.
    hour_angle_hours : `float` or `None`
        Mount hour angle when guiding began, in hours.
    pier_side : `str` or `None`
        Which side of the pier the telescope was on (``"East"`` or ``"West"``).
    altitude_degrees : `float` or `None`
        Altitude when guiding began, in degrees.
    azimuth_degrees : `float` or `None`
        Azimuth when guiding began, in degrees.
    ra_rate_arcsec_per_second : `float` or `None`
        Calibrated mount speed along RA, in arcseconds per second.
    dec_rate_arcsec_per_second : `float` or `None`
        Calibrated mount speed along Dec, in arcseconds per second.
    frames_total : `int`
        How many frame rows the section holds.
    frames_with_error_code : `int`
        How many of those rows carry a non-zero error code (for example a
        lost guide star). They are left out of `samples`.
    frames_without_pixel_scale : `int`
        How many rows were left out because no pixel scale was known.
    samples : `list` [`dict`]
        The usable frames, ready for `LoggerInterface.record_guiding_samples`.
    """

    started_at: float
    ended_at: float | None = None
    pixel_scale_arcsec_per_px: float | None = None
    binning: int | None = None
    focal_length_mm: float | None = None
    right_ascension_hours: float | None = None
    declination_degrees: float | None = None
    hour_angle_hours: float | None = None
    pier_side: str | None = None
    altitude_degrees: float | None = None
    azimuth_degrees: float | None = None
    ra_rate_arcsec_per_second: float | None = None
    dec_rate_arcsec_per_second: float | None = None
    frames_total: int = 0
    frames_with_error_code: int = 0
    frames_without_pixel_scale: int = 0
    samples: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ParsedGuideLog:
    """Everything read from one guide log file.

    Attributes
    ----------
    written_by_ekos : `bool`
        `True` if the Ekos internal guider wrote the file (its first line
        starts with "KStars"), `False` for PHD2 itself.
    program_version : `str` or `None`
        The first line of the file, which names the program and version.
    calibrations : `list` [`GuideCalibration`]
        Every calibration the file records.
    sections : `list` [`GuidingSection`]
        Every guiding section, in file order.
    """

    written_by_ekos: bool
    program_version: str | None = None
    calibrations: list[GuideCalibration] = field(default_factory=list)
    sections: list[GuidingSection] = field(default_factory=list)
