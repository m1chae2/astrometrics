"""Writes moving-object detections in the Minor Planet Center 80-column format.

The Minor Planet Center (MPC) collects position measurements of asteroids.
Its classic report format is a text file with one measurement per line. Each
line is exactly 80 characters, and each piece of information sits in fixed
columns (counted from 1):

=======  =========================================================
Columns  Content
=======  =========================================================
1-5      Packed minor planet number (left blank here)
6-12     Packed or temporary designation, up to 7 characters
13-14    Discovery asterisk and note 1 (left blank here)
15       Note 2; ``C`` means the measurement came from a CCD
16-31    UTC date, ``YYYY MM DD.ddddd`` (day fraction to 1e-5 day)
33-43    Right ascension, ``HH MM SS.ss``
45-55    Declination, ``sDD MM SS.s``
66-69    Magnitude (blank when it was not calibrated)
71       Magnitude band (blank when there is no magnitude)
78-80    MPC observatory code
=======  =========================================================

The functions here only build text from numbers. They read no files and
need no network. Two limits to know about:

* The designation is written as given. It is not converted to the MPC's
  packed form, so the caller passes a ready-to-use packed or temporary one.
* ADES (the newer XML and PSV format the MPC also accepts) is not provided.
"""

from datetime import UTC, datetime, timedelta

from astrometricslib.models.moving_object import AsteroidDetectionCandidate, FrameDetection

_MPC_LINE_LENGTH = 80
_SECONDS_PER_DAY = 86400.0
_DAY_FRACTION_UNITS_PER_DAY = 100_000
_DESIGNATION_WIDTH = 7
_OBSERVATORY_CODE_WIDTH = 3

# Unit sizes used to turn an angle into whole counts of the printed unit.
_ARCSEC_PER_DEGREE = 3600
_SECONDS_OF_TIME_PER_HOUR = 3600
_HOURS_PER_DAY = 24
_DEGREES_PER_HOUR_OF_RA = 15.0


def mid_exposure_unix(start_unix: float, exposure_seconds: float | None) -> float:
    """Find the middle of an exposure.

    ``DATE-OBS`` gives the moment the shutter opened, so the middle of the
    exposure is half the exposure time later.

    Parameters
    ----------
    start_unix : `float`
        The exposure start (``DATE-OBS``), as a Unix timestamp in UTC.
    exposure_seconds : `float` or `None`
        The exposure time (``EXPTIME``) in seconds. When `None`, the start
        time is returned unchanged, because the middle cannot be found.

    Returns
    -------
    mid_unix : `float`
        The middle of the exposure, as a Unix timestamp in UTC.
    """
    if exposure_seconds is None:
        return start_unix
    return start_unix + exposure_seconds / 2.0


def format_mpc_date(unix_time: float) -> str:
    """Write a moment as the MPC date, ``YYYY MM DD.ddddd``.

    The time is rounded to the nearest 1e-5 day (0.864 s). A time that
    rounds up to the end of a day moves to the start of the next day.

    Parameters
    ----------
    unix_time : `float`
        The moment, as a Unix timestamp in UTC.

    Returns
    -------
    date_text : `str`
        The date text, 16 characters.
    """
    total_units = round(unix_time * _DAY_FRACTION_UNITS_PER_DAY / _SECONDS_PER_DAY)
    whole_days, day_fraction_units = divmod(total_units, _DAY_FRACTION_UNITS_PER_DAY)
    date = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(days=whole_days)
    return f"{date.year:04d} {date.month:02d} {date.day:02d}.{day_fraction_units:05d}"


def format_mpc_right_ascension(right_ascension_deg: float) -> str:
    """Write a right ascension as ``HH MM SS.ss`` (hours, minutes, seconds).

    Parameters
    ----------
    right_ascension_deg : `float`
        Right ascension in degrees. Values outside 0 to 360 wrap around.

    Returns
    -------
    right_ascension_text : `str`
        The text, 11 characters, rounded to 0.01 s of time.
    """
    hundredths_of_second = round(
        right_ascension_deg / _DEGREES_PER_HOUR_OF_RA * _SECONDS_OF_TIME_PER_HOUR * 100
    ) % (_HOURS_PER_DAY * _SECONDS_OF_TIME_PER_HOUR * 100)
    seconds_total, hundredths = divmod(hundredths_of_second, 100)
    minutes_total, seconds = divmod(seconds_total, 60)
    hours, minutes = divmod(minutes_total, 60)
    return f"{hours:02d} {minutes:02d} {seconds:02d}.{hundredths:02d}"


def format_mpc_declination(declination_deg: float) -> str:
    """Write a declination as ``sDD MM SS.s``.

    The parts are the sign, degrees, arcminutes and arcseconds.

    Parameters
    ----------
    declination_deg : `float`
        Declination in degrees, from -90 to +90.

    Returns
    -------
    declination_text : `str`
        The text, 11 characters, rounded to 0.1 arcsec.

    Raises
    ------
    ValueError
        If the declination is outside -90 to +90 degrees.
    """
    if not -90.0 <= declination_deg <= 90.0:
        raise ValueError(f"Declination must be between -90 and +90 degrees, got {declination_deg}.")
    tenths_of_arcsecond = round(abs(declination_deg) * _ARCSEC_PER_DEGREE * 10)
    arcseconds_total, tenths = divmod(tenths_of_arcsecond, 10)
    arcminutes_total, arcseconds = divmod(arcseconds_total, 60)
    degrees, arcminutes = divmod(arcminutes_total, 60)
    sign = "-" if declination_deg < 0 and tenths_of_arcsecond > 0 else "+"
    return f"{sign}{degrees:02d} {arcminutes:02d} {arcseconds:02d}.{tenths}"


def format_mpc_observation(
    designation: str,
    mid_exposure_unix_time: float,
    right_ascension_deg: float,
    declination_deg: float,
    observatory_code: str,
    magnitude: float | None = None,
    band: str = "",
) -> str:
    """Build one 80-column MPC observation line.

    Parameters
    ----------
    designation : `str`
        A packed or temporary designation of at most 7 characters, written
        in columns 6-12. It is not packed here.
    mid_exposure_unix_time : `float`
        The middle of the exposure, as a Unix timestamp in UTC. See
        `mid_exposure_unix`.
    right_ascension_deg : `float`
        Right ascension in degrees.
    declination_deg : `float`
        Declination in degrees.
    observatory_code : `str`
        The three-character MPC observatory code (``500`` is the centre of
        the Earth).
    magnitude : `float`, optional
        The calibrated magnitude. Leave it out when none was measured; the
        magnitude and band columns are then blank.
    band : `str`, optional
        The one-letter magnitude band (for example ``V``). Ignored when
        there is no magnitude.

    Returns
    -------
    line : `str`
        The observation line, exactly 80 characters, with no newline.

    Raises
    ------
    ValueError
        If the designation or observatory code has the wrong length, the
        band is longer than one character, or the declination is out of
        range.
    """
    designation = designation.strip()
    if not designation or len(designation) > _DESIGNATION_WIDTH:
        raise ValueError(f"Designation must be 1 to {_DESIGNATION_WIDTH} characters, got {designation!r}.")
    if len(observatory_code) != _OBSERVATORY_CODE_WIDTH:
        raise ValueError(
            f"Observatory code must be {_OBSERVATORY_CODE_WIDTH} characters, got {observatory_code!r}."
        )
    if len(band) > 1:
        raise ValueError(f"Band must be one character, got {band!r}.")

    magnitude_text = f"{magnitude:4.1f}" if magnitude is not None else "    "
    band_text = band if magnitude is not None and band else " "

    line = (
        " " * 5  # columns 1-5: number
        + designation.ljust(_DESIGNATION_WIDTH)  # columns 6-12
        + " "  # column 13: discovery asterisk
        + " "  # column 14: note 1
        + "C"  # column 15: note 2, CCD
        + format_mpc_date(mid_exposure_unix_time)  # columns 16-31
        + " "  # column 32
        + format_mpc_right_ascension(right_ascension_deg)  # columns 33-43
        + " "  # column 44
        + format_mpc_declination(declination_deg)  # columns 45-55
        + " " * 10  # columns 56-65
        + magnitude_text  # columns 66-69
        + " "  # column 70
        + band_text  # column 71
        + " " * 6  # columns 72-77
        + observatory_code  # columns 78-80
    )
    if len(line) != _MPC_LINE_LENGTH:
        raise ValueError(f"MPC line has {len(line)} characters instead of {_MPC_LINE_LENGTH}.")
    return line


def mpc_lines_for_candidate(
    candidate: AsteroidDetectionCandidate, designation: str, observatory_code: str
) -> tuple[list[str], int]:
    """Write every detection of a candidate as an MPC line, oldest first.

    No magnitude is written: the detections carry an instrumental flux, not
    a calibrated magnitude.

    Parameters
    ----------
    candidate : `AsteroidDetectionCandidate`
        The moving object to report.
    designation : `str`
        A packed or temporary designation of at most 7 characters.
    observatory_code : `str`
        The three-character MPC observatory code.

    Returns
    -------
    lines : `list` [`str`]
        One 80-character line per detection, in time order.
    start_time_lines : `int`
        How many lines used the exposure start instead of the middle,
        because that frame had no exposure time. Those times are early by
        half an exposure.
    """
    detections: list[FrameDetection] = sorted(candidate.frame_detections, key=lambda d: d.timestamp)
    lines = [
        format_mpc_observation(
            designation,
            mid_exposure_unix(detection.timestamp, detection.exposure_seconds),
            detection.right_ascension_deg,
            detection.declination_deg,
            observatory_code,
        )
        for detection in detections
    ]
    start_time_lines = sum(1 for detection in detections if detection.exposure_seconds is None)
    return lines, start_time_lines
