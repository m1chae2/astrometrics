"""Pydantic model and config-file loader for the moving-object pipeline."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MovingObjectConfig(BaseModel):
    """Configuration for the moving object detection/recovery pipeline.

    All numeric defaults below are initial estimates, not empirically
    validated against real recovery attempts -- there is no real
    moving-object run yet to validate them against. They follow
    the same disclosure discipline as the stacking pipeline's own
    not-yet-validated defaults (e.g. its minimum-frames floor).

    Attributes
    ----------
    detection_fwhm_px : `float`
        Expected point-source FWHM, in pixels, passed to
        `SourceDetector` for per-frame morphology detection (cascade
        step 1), by default 4.0.
    detection_threshold_sigma : `float`
        Detection threshold as a multiple of background noise
        standard deviation, passed to `SourceDetector`, by default
        5.0.
    min_frames_for_persistence : `int`
        Minimum number of chained frame detections required for a
        candidate to survive cascade step 2 (persistence); chains
        shorter than this are treated as single-frame cosmic-ray
        apparitions, by default 3.
    pixel_match_tolerance_px : `float`
        Below this native-pixel-coordinate spread across a
        candidate's frame detections, cascade step 3 (reference-frame
        test) treats the candidate as stationary in pixel space (a
        hot pixel/CCD defect), by default 1.5.
    sky_match_tolerance_arcsec : `float`
        Below this sky-coordinate spread across a candidate's frame
        detections, cascade step 3 treats the candidate as stationary
        in the sky (a missed star), by default 3.0.
    rate_min_arcsec_per_hour : `float`
        Minimum total sky-motion rate for cascade step 4
        (rate/linearity) to treat a candidate as a plausible mover
        rather than noise, by default 1.0.
    rate_max_arcsec_per_hour : `float`
        Maximum total sky-motion rate for cascade step 4 to treat a
        candidate as a plausible slow/bright-asteroid mover rather
        than a fast-moving object out of this pipeline's recovery
        scope, by default 300.0.
    rate_linearity_r_squared_min : `float`
        Minimum R² (each sky axis fit independently, the lower of the
        two used) for cascade step 4 to treat a candidate's motion as
        sufficiently linear to be a real mover, by default 0.98.
    ephemeris_cross_match_radius_arcsec : `float`
        Maximum angular separation between a candidate's mean sky
        position and a queried ephemeris position for cascade step 5
        to treat them as the same body, by default 10.0.
    ephemeris_maximum_visual_magnitude : `float`
        Maximum (faintest) visual magnitude included in the ephemeris
        cross-match query, reflecting this pipeline's "bright, known
        body recovery" goal rather than faint-object discovery, by
        default 16.0.
    mpc_observatory_code : `str`
        IAU observatory code used for topocentric ephemeris queries;
        the geocentric default (``"500"``) is used when the observing
        site has no registered code, by default ``"500"``.
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
        """Return a copy of this config with the given fields overridden.

        Parameters
        ----------
        **kwargs
            Field names (by attribute name, not alias) and their
            override values.

        Returns
        -------
        overridden_config : `MovingObjectConfig`
            A new config instance with the requested fields replaced.
        """
        return self.model_copy(update=kwargs)


class MovingObjectConfigLoader:
    """Load `MovingObjectConfig` from the application configuration."""

    @staticmethod
    def load_moving_object_config(app_config: Any | None = None) -> MovingObjectConfig:
        """Load the moving-object pipeline's configuration.

        Reads values from the ``[Processing.MovingObject]`` or fallback
        ``[Processing.AsteroidRecovery]`` config section.

        Parameters
        ----------
        app_config : `Any`, optional
            The loaded application configuration object providing a
            `get_value` accessor, by default `None`, in which case
            the system configuration is loaded automatically via
            `get_configuration`.

        Returns
        -------
        moving_object_config : `MovingObjectConfig`
            The resolved configuration, with defaults applied for any
            unset value.
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
