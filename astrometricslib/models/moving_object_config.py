"""Settings for finding moving objects like asteroids."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib.models.tuned_parameter import ParameterRole, ParameterSource, TunedParameter

# The width a star or asteroid is expected to have when the moving-object
# search finds dots, in pixels. A processing setting, so a person can change
# it in ``[Processing.MovingObject]``. 4 px is 1.7 times the reference star
# width (2.33 px); the grid of MOV-01 used it and did not vary it (a sanity
# check, partly measured).
DETECTION_FWHM_PX = TunedParameter(
    name="asteroid_detection.detection_fwhm_px",
    default=4.0,
    unit="pixels",
    description="Width of a star or asteroid the moving-object search expects.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.PROCESSING_SETTING,
    register_entry="MOV-01",
    config_key=("Processing.MovingObject", "detection_fwhm_px"),
)

# How far above the background noise a dot must be to be noticed, in
# standard deviations. The MOV-01 completeness grid ran it at 5 and showed
# the fainter limit it gives; lowering it finds fainter movers and more false
# dots, which the MOV-03 mover-free fields check.
DETECTION_THRESHOLD_SIGMA = TunedParameter(
    name="asteroid_detection.detection_threshold_sigma",
    default=5.0,
    unit="sigma",
    description="Detection threshold above the background noise for the moving-object search.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.PROCESSING_SETTING,
    register_entry="MOV-01",
    config_key=("Processing.MovingObject", "detection_threshold_sigma"),
)

# A track is a straight line when the RMS distance of its dots from the fitted
# line, on each sky axis, is below this many times the error of one position.
# Twice the error keeps a real straight mover (its residuals scatter by about
# one error) and rejects a star whose measured center jumps between two
# points. Confirmed by MOV-03: 0 false tracks in 40 mover-free synthetic
# fields (partly measured; the limit is not validated on real fields).
RESIDUAL_RMS_MAX_MULTIPLE = TunedParameter(
    name="asteroid_detection.residual_rms_max_multiple",
    default=2.0,
    unit="× position error",
    description="Largest RMS distance from the fitted line, in position errors, of a straight track.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.PROCESSING_SETTING,
    register_entry="MOV-03",
    config_key=("Processing.MovingObject", "residual_rms_max_multiple"),
)


class MovingObjectConfig(BaseModel):
    """Settings used when searching for asteroids.

    These values control how strictly moving dots are checked to see if they
    are real asteroids or just noise.

    Attributes
    ----------
    detection_fwhm_px : `float`
        How wide (in pixels) a star or asteroid is expected to be, by
        default 4.0.
    detection_threshold_sigma : `float`
        How much brighter than the background noise an object must be
        to get noticed, by default 5.0.
    min_frames_for_persistence : `int`
        How many pictures in a row the object must be seen in before
        it's trusted as real, by default 3.
    pixel_match_tolerance_px : `float`
        If an object moves less than this many pixels, it's assumed to
        be a dead camera pixel, by default 1.5.
    sky_match_tolerance_arcsec : `float`
        If an object moves less than this much across the sky, it's
        assumed to just be a normal star, by default 3.0.
    rate_min_arcsec_per_hour : `float`
        The slowest an object can move and still be considered an
        asteroid, by default 1.0.
    rate_max_arcsec_per_hour : `float`
        The fastest an object can move. Anything faster is probably a
        satellite, by default 300.0.
    chain_match_radius_max_arcsec : `float`
        The farthest, in arcseconds, a dot may sit from the last dot of a
        track and still be added to it. The search radius grows with the
        time between pictures, and this stops it at a few arcminutes, by
        default 300.0.
    residual_rms_max_multiple : `float`
        A track counts as a straight line when the root-mean-square (RMS)
        distance of its dots from the fitted line, on each sky axis, is
        below this many times the error of one position, by default 2.0.
    min_displacement_error_multiple : `float`
        A track must move, along the fitted line, at least this many times
        the error of one position between its first and last picture. This
        keeps a star that merely jitters by its position error from passing
        as a slow mover, by default 3.0.
    astrometric_error_default_arcsec : `float`
        The error of one position, in arcseconds, to assume for a picture
        whose own error could not be measured, by default 10.0.
    centroid_error_px : `float`
        The error, in pixels, of finding the centre of a faint dot. It is
        added in quadrature (square root of the sum of squares) to the
        measured pointing scatter, by default 0.5.
    ephemeris_cross_match_radius_arcsec : `float`
        How close the object must be to a known asteroid's predicted
        position to count as a match, by default 10.0.
    mpc_observatory_code : `str`
        The official code for where the telescope is located, assigned
        by the Minor Planet Center (the organization that tracks
        asteroids). "500" means the center of the Earth, by default
        "500".
    """

    model_config = ConfigDict(populate_by_name=True)

    detection_fwhm_px: float = Field(default=float(DETECTION_FWHM_PX.default), alias="detectionFwhmPx")
    detection_threshold_sigma: float = Field(
        default=float(DETECTION_THRESHOLD_SIGMA.default), alias="detectionThresholdSigma"
    )
    min_frames_for_persistence: int = Field(default=3, alias="minFramesForPersistence")
    pixel_match_tolerance_px: float = Field(default=1.5, alias="pixelMatchTolerancePx")
    sky_match_tolerance_arcsec: float = Field(default=3.0, alias="skyMatchToleranceArcsec")
    rate_min_arcsec_per_hour: float = Field(default=1.0, alias="rateMinArcsecPerHour")
    rate_max_arcsec_per_hour: float = Field(default=300.0, alias="rateMaxArcsecPerHour")
    chain_match_radius_max_arcsec: float = Field(default=300.0, alias="chainMatchRadiusMaxArcsec")
    residual_rms_max_multiple: float = Field(
        default=float(RESIDUAL_RMS_MAX_MULTIPLE.default), alias="residualRmsMaxMultiple"
    )
    min_displacement_error_multiple: float = Field(default=3.0, alias="minDisplacementErrorMultiple")
    astrometric_error_default_arcsec: float = Field(default=10.0, alias="astrometricErrorDefaultArcsec")
    centroid_error_px: float = Field(default=0.5, alias="centroidErrorPx")
    ephemeris_cross_match_radius_arcsec: float = Field(default=10.0, alias="ephemerisCrossMatchRadiusArcsec")
    mpc_observatory_code: str = Field(default="500", alias="mpcObservatoryCode")


class MovingObjectConfigLoader:
    """Reads moving object settings from the main config file."""

    @staticmethod
    def load_moving_object_config(app_config: Any | None = None) -> MovingObjectConfig:
        """Load settings for finding asteroids.

        Parameters
        ----------
        app_config : `Any`, optional
            The main config object. If None, it loads it automatically.

        Returns
        -------
        moving_object_config : `MovingObjectConfig`
            The loaded settings, using defaults for anything missing.
        """
        if app_config is None:
            from astrometricslib.foundation.config import get_configuration

            app_config = get_configuration()

        defaults = MovingObjectConfig()
        section = "Processing.MovingObject"
        fallback_section = "Processing.AsteroidRecovery"

        def _get_val(key: str, default: Any) -> Any:
            val = app_config.get_value(section, key, fallback=None)
            if val is None:
                val = app_config.get_value(fallback_section, key, fallback=default)
            return val

        return MovingObjectConfig(
            detection_fwhm_px=float(_get_val("detection_fwhm_px", defaults.detection_fwhm_px)),
            detection_threshold_sigma=float(
                _get_val("detection_threshold_sigma", defaults.detection_threshold_sigma)
            ),
            min_frames_for_persistence=int(
                _get_val("min_frames_for_persistence", defaults.min_frames_for_persistence)
            ),
            pixel_match_tolerance_px=float(
                _get_val("pixel_match_tolerance_px", defaults.pixel_match_tolerance_px)
            ),
            sky_match_tolerance_arcsec=float(
                _get_val("sky_match_tolerance_arcsec", defaults.sky_match_tolerance_arcsec)
            ),
            rate_min_arcsec_per_hour=float(
                _get_val("rate_min_arcsec_per_hour", defaults.rate_min_arcsec_per_hour)
            ),
            rate_max_arcsec_per_hour=float(
                _get_val("rate_max_arcsec_per_hour", defaults.rate_max_arcsec_per_hour)
            ),
            chain_match_radius_max_arcsec=float(
                _get_val("chain_match_radius_max_arcsec", defaults.chain_match_radius_max_arcsec)
            ),
            residual_rms_max_multiple=float(
                _get_val("residual_rms_max_multiple", defaults.residual_rms_max_multiple)
            ),
            min_displacement_error_multiple=float(
                _get_val("min_displacement_error_multiple", defaults.min_displacement_error_multiple)
            ),
            astrometric_error_default_arcsec=float(
                _get_val("astrometric_error_default_arcsec", defaults.astrometric_error_default_arcsec)
            ),
            centroid_error_px=float(_get_val("centroid_error_px", defaults.centroid_error_px)),
            ephemeris_cross_match_radius_arcsec=float(
                _get_val("ephemeris_cross_match_radius_arcsec", defaults.ephemeris_cross_match_radius_arcsec)
            ),
            mpc_observatory_code=str(_get_val("mpc_observatory_code", defaults.mpc_observatory_code)),
        )
