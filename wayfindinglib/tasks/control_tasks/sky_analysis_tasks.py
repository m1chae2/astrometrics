"""Purpose: Gather the measurements for the sky-position analysis.

Description: Turns the equipment's light frames and its nights' guiding
analyses into `SkySample` records: one measurement, and where the telescope
pointed when it was taken. The analysis stages read nothing; this module is
where frames and guiding runs become one list.

Star measurements come from imaging frames long enough for guiding error to
show in them, the same frames the performance envelope uses. Guiding error
comes from each run of a night whose guiding data was trustworthy: a night
with an impossible calibration or a weak guide star gives error numbers that
describe a bad measurement, not the mount, so its runs are left out.
"""

from collections.abc import Iterable, Sequence

from wayfindinglib.models.session.capture_frame import CaptureFrame
from wayfindinglib.models.session.session_quality import GuidingSessionAnalysis
from wayfindinglib.models.session.sky_quality import SkySample
from wayfindinglib.tasks.control_tasks.capture_analysis_tasks import night_of, usable_star_frames


def _has_position(altitude: float | None, azimuth: float | None, pier_side: str | None) -> bool:
    """Tell whether a pointing is known in any dimension.

    Returns
    -------
    known : `bool`
        `True` if altitude, azimuth or pier side is known.
    """
    return altitude is not None or azimuth is not None or pier_side is not None


def sky_samples_from_frames(
    frames: Sequence[CaptureFrame], minimum_exposure_seconds: float | None
) -> tuple[list[SkySample], int]:
    """Turn the equipment's light frames into star-width and roundness samples.

    Parameters
    ----------
    frames : `Sequence` [`CaptureFrame`]
        The equipment's light frames.
    minimum_exposure_seconds : `float` or `None`
        Shortest exposure whose star measurements count. `None` when the
        equipment has not guided yet, in which case no frame qualifies.

    Returns
    -------
    samples : `list` [`SkySample`]
        A star-width sample for each qualifying frame with a position, and a
        roundness sample when the frame has one.
    without_position : `int`
        Qualifying frames left out because they have no position.
    """
    if minimum_exposure_seconds is None:
        return [], 0
    samples: list[SkySample] = []
    without_position = 0
    for frame in usable_star_frames(frames, minimum_exposure_seconds):
        if not _has_position(frame.altitude_degrees, frame.azimuth_degrees, frame.pier_side):
            without_position += 1
            continue
        common = {
            "night": night_of(frame),
            "altitude_degrees": frame.altitude_degrees,
            "azimuth_degrees": frame.azimuth_degrees,
            "pier_side": frame.pier_side,
        }
        if frame.star_width_arcsec is not None:
            samples.append(SkySample(metric="star_width", value=frame.star_width_arcsec, **common))
        if frame.roundness is not None:
            samples.append(SkySample(metric="star_roundness", value=frame.roundness, **common))
    return samples, without_position


def sky_samples_from_guiding(
    analyses: Iterable[GuidingSessionAnalysis],
) -> tuple[list[SkySample], int, int]:
    """Turn each trustworthy night's guiding runs into guiding-error samples.

    Parameters
    ----------
    analyses : `Iterable` [`GuidingSessionAnalysis`]
        The guiding analysis of each night.

    Returns
    -------
    samples : `list` [`SkySample`]
        One per run that has a guiding error and a position.
    without_position : `int`
        Runs left out because they have no position.
    nights_excluded : `int`
        Nights left out because their guiding data was unreliable. Nights on
        other equipment are not counted, since they were never eligible.
    """
    samples: list[SkySample] = []
    without_position = 0
    nights_excluded = 0
    for analysis in analyses:
        quality = analysis.input_quality
        if quality.limits_equipment_match == "none":
            continue
        if quality.calibration_problems or quality.has_low_signal:
            nights_excluded += 1
            continue
        for run in analysis.performance.runs:
            if run.rms_per_axis_arcsec is None:
                continue
            if not _has_position(run.altitude_degrees, run.azimuth_degrees, run.pier_side):
                without_position += 1
                continue
            samples.append(
                SkySample(
                    night=analysis.session_id,
                    metric="guiding_error",
                    value=run.rms_per_axis_arcsec,
                    altitude_degrees=run.altitude_degrees,
                    azimuth_degrees=run.azimuth_degrees,
                    pier_side=run.pier_side,
                )
            )
    return samples, without_position, nights_excluded
