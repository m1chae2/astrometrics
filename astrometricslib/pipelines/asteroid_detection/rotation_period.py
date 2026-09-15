"""Looks for a repeating brightness pattern in a tracked moving object.

Many asteroids tumble as they orbit, so the amount of sunlit surface
facing us rises and falls as they spin -- brightening and dimming on a
steady repeating cycle (the asteroid's rotation period, usually a few
hours). This reuses the same repeating-pattern finder already used for
variable stars: feed it "brightness over time" for the tracked object,
and it looks for a cycle the same way either way.
"""

from datetime import datetime

from astrometricslib.models.moving_object import AsteroidDetectionCandidate
from astrometricslib.models.stellar_source import LightCurve, PeriodogramResult, StellarObject


def build_light_curve_from_track(candidate: AsteroidDetectionCandidate) -> LightCurve:
    """Turn one tracked object's per-picture brightness into a light curve.

    Each picture's own brightness is divided by that picture's typical
    brightness level (`FrameDetection.picture_brightness_level`) so
    that changing sky conditions between pictures -- clouds, haze,
    moonlight -- don't get mistaken for the object's own brightness
    changing. Pictures missing either brightness value are skipped.

    Parameters
    ----------
    candidate : `AsteroidDetectionCandidate`
        A tracked object, with one `FrameDetection` per picture it was
        found in.

    Returns
    -------
    light_curve : `LightCurve`
        Timestamps and corrected ("normalized") brightness values, in
        the same picture order as `candidate.frame_detections`. Empty
        if no picture had both brightness values measured.
    """
    timestamps = []
    corrected_brightness = []
    for detection in candidate.frame_detections:
        if detection.brightness is None or not detection.picture_brightness_level:
            continue
        timestamps.append(datetime.fromtimestamp(detection.timestamp))
        corrected_brightness.append(detection.brightness / detection.picture_brightness_level)

    return LightCurve(timestamps=timestamps, fluxes_normalized=corrected_brightness)


def find_rotation_period(candidate: AsteroidDetectionCandidate) -> PeriodogramResult | None:
    """Look for a repeating brightness cycle in a tracked object.

    Parameters
    ----------
    candidate : `AsteroidDetectionCandidate`
        A tracked object to search for a rotation period.

    Returns
    -------
    periodogram : `PeriodogramResult` or `None`
        The best repeating cycle found, or `None` if there wasn't
        enough brightness data to look for one (see
        `VariabilityAnalyzer.run_lomb_scargle_periodogram` for the
        minimum needed).
    """
    from astrometricslib.pipelines.photometry.variability_analyzer import VariabilityAnalyzer

    light_curve = build_light_curve_from_track(candidate)
    star = StellarObject(id=candidate.id, light_curve=light_curve)
    return VariabilityAnalyzer().run_lomb_scargle_periodogram(star)
