"""Settings for finding moving objects like asteroids."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MovingObjectConfig(BaseModel):
    """Settings used when searching for asteroids.

    These values control how strictly we check moving dots to see if they
    are real asteroids or just noise.

    Attributes
    ----------
    detection_fwhm_px : `float`
        How wide (in pixels) we expect a star or asteroid to be, by default
        4.0.
    detection_threshold_sigma : `float`
        How much brighter than the background noise an object must be to get
        noticed, by default 5.0.
    min_frames_for_persistence : `int`
        How many pictures in a row we need to see the object in before we
        trust it's real, by default 3.
    pixel_match_tolerance_px : `float`
        If an object moves less than this many pixels, we assume it's a dead
        camera pixel, by default 1.5.
    sky_match_tolerance_arcsec : `float`
        If an object moves less than this much across the sky, we assume
        it's
        just a normal star, by default 3.0.
    rate_min_arcsec_per_hour : `float`
        The slowest an object can move and still be considered an asteroid, by
        default 1.0.
    rate_max_arcsec_per_hour : `float`
        The fastest an object can move. Anything faster is probably a
        satellite, by default 300.0.
    rate_linearity_r_squared_min : `float`
        How perfectly straight the object's path must be (1.0 is perfectly
        straight), by default 0.98.
    ephemeris_cross_match_radius_arcsec : `float`
        How close our object must be to a known asteroid's predicted
        position
        to count as a match, by default 10.0.
    ephemeris_maximum_visual_magnitude : `float`
        The faintest known asteroids we'll bother checking against, by default
        16.0.
    mpc_observatory_code : `str`
        The official code for where the telescope is located. "500" means
        the
        center of the Earth, by default "500".
    """

    model_config = ConfigDict(populate_by_name=True)

    detection_fwhm_px: float = Field(default=4.0, alias="detectionFwhmPx")
    detection_threshold_sigma: float = Field(default=5.0, alias="detectionThresholdSigma")
    min_frames_for_persistence: int = Field(default=3, alias="minFramesForPersistence")
    pixel_match_tolerance_px: float = Field(default=1.5, alias="pixelMatchTolerancePx")
    sky_match_tolerance_arcsec: float = Field(default=3.0, alias="skyMatchToleranceArcsec")
    rate_min_arcsec_per_hour: float = Field(default=1.0, alias="rateMinArcsecPerHour")
    rate_max_arcsec_per_hour: float = Field(default=300.0, alias="rateMaxArcsecPerHour")
    rate_linearity_r_squared_min: float = Field(default=0.98, alias="rateLinearityRSquaredMin")
    ephemeris_cross_match_radius_arcsec: float = Field(default=10.0, alias="ephemerisCrossMatchRadiusArcsec")
    ephemeris_maximum_visual_magnitude: float = Field(default=16.0, alias="ephemerisMaximumVisualMagnitude")
    mpc_observatory_code: str = Field(default="500", alias="mpcObservatoryCode")

    def with_overrides(self, **kwargs) -> MovingObjectConfig:  # ruff: ignore[missing-type-kwargs]
        """Make a copy of these settings, changing specific values if needed.

        Parameters
        ----------
        **kwargs
            The setting names and their new values.

        Returns
        -------
        overridden_config : `MovingObjectConfig`
            A new settings object with your changes applied.
        """
        return self.model_copy(update=kwargs)


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
            from astrometricslib.utilities.config_loader import get_configuration

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
            rate_linearity_r_squared_min=float(
                _get_val("rate_linearity_r_squared_min", defaults.rate_linearity_r_squared_min)
            ),
            ephemeris_cross_match_radius_arcsec=float(
                _get_val("ephemeris_cross_match_radius_arcsec", defaults.ephemeris_cross_match_radius_arcsec)
            ),
            ephemeris_maximum_visual_magnitude=float(
                _get_val("ephemeris_maximum_visual_magnitude", defaults.ephemeris_maximum_visual_magnitude)
            ),
            mpc_observatory_code=str(_get_val("mpc_observatory_code", defaults.mpc_observatory_code)),
        )
