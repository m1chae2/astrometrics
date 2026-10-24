"""Purpose: Performance Envelope Models.

Description: The limits that separate normal from abnormal for the
equipment in use now: how large a guiding error is acceptable, how low a
guide-star signal is suspicious, how fast a mount speed can be before it
must be a bad calibration, and so on.

None of these numbers is stored. They are worked out from the equipment
each time they are asked for, so when the equipment changes, every limit
changes with it and nothing stale is left behind. Each limit is a
`QualityLimit` from the science library: its value, unit, how it was
obtained (`LimitTier`) and the derivation in words. The numbers a limit was
worked out from are kept in the envelope's `limit_inputs`. A limit with not
enough data behind it has the value `None` and says in its derivation what is
missing; it is never replaced by a guess.

`Wayfinding_Library_Architecture.md` Section 3.2 treats equipment as
configured Foundation state (and Section 6.8 describes the envelope).
The envelope is computed Foundation state: derived on demand from that
configuration and from measurements, never persisted.
"""

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib import QualityLimit


class TrackingRiskMap(BaseModel):
    """How risky each part of the sky is for the mount's tracking.

    A grid of scores from 0 (safe) to 1 (likely to trail) over hour angle
    (HA, west of the meridian positive) and declination. It is fixed to the
    mount, so it holds all night. Scores combine a geometric prior (west of
    the meridian, near the pole, low altitude) with the tracking jitter
    measured on earlier targets near each point (see
    `wayfindinglib.analytics.tracking_risk`).

    Attributes
    ----------
    latitude_deg : `float`
        Observer latitude the altitudes were worked out for.
    ha_deg : `list` [`float`]
        Grid hour angles, -180 to 180 degrees, evenly spaced.
    dec_deg : `list` [`float`]
        Grid declinations, -90 to 90 degrees, evenly spaced.
    scores : `list` [`list` [`float`]]
        ``scores[i][j]`` is the score at ``dec_deg[i]`` and ``ha_deg[j]``.
    caution_score : `float`
        Scores from here up are a caution.
    high_score : `float`
        Scores from here up are a high risk.
    ideal_rms_arcsec : `float`
        Jitter up to this keeps stars round (0.75 pixel).
    trailing_rms_arcsec : `float`
        Jitter above this trails stars (1.25 pixels).
    plate_scale_arcsec_per_px : `float` or `None`
        The camera plate scale the jitter limits came from, if known.
    measured_target_count : `int`
        Earlier targets whose measured jitter shaped the scores.
    solve_count : `int`
        Plate solves those targets were measured from.
    """

    model_config = ConfigDict(populate_by_name=True)

    latitude_deg: float = Field(alias="latitudeDeg")
    ha_deg: list[float] = Field(alias="haDeg")
    dec_deg: list[float] = Field(alias="decDeg")
    scores: list[list[float]]
    caution_score: float = Field(alias="cautionScore")
    high_score: float = Field(alias="highScore")
    ideal_rms_arcsec: float = Field(alias="idealRmsArcsec")
    trailing_rms_arcsec: float = Field(alias="trailingRmsArcsec")
    plate_scale_arcsec_per_px: float | None = Field(default=None, alias="plateScaleArcsecPerPx")
    measured_target_count: int = Field(default=0, alias="measuredTargetCount")
    solve_count: int = Field(default=0, alias="solveCount")


class PerformanceEnvelope(BaseModel):
    """Every limit for the equipment in use now.

    Attributes
    ----------
    equipment_fingerprint : `str`
        Identifies the equipment the limits were worked out for (see
        `build_equipment_fingerprint`).
    blur_tolerance_fraction : `float`
        The one policy choice behind the physical-budget limits: the most
        that guiding error and trailing may widen a star image, as a
        fraction of its width.
    thresholds : `dict` [`str`, `QualityLimit`]
        Every limit, by name. A limit whose value is `None` could not be
        worked out; its derivation says why.
    limit_inputs : `dict` [`str`, `dict` [`str`, `float` or `str` or `None`]]
        For each limit, by name, the numbers it was worked out from, so
        anyone can redo it. `QualityLimit` has no place for them.
    tracking_risk : `TrackingRiskMap` or `None`
        How risky each part of the sky is for tracking with this
        equipment. Filled in by `control.history.get_performance_envelope`;
        `None` in the envelopes the night analyses use internally.
    """

    model_config = ConfigDict(populate_by_name=True)

    equipment_fingerprint: str = Field(alias="equipmentFingerprint")
    blur_tolerance_fraction: float = Field(alias="blurToleranceFraction")
    thresholds: dict[str, QualityLimit] = Field(default_factory=dict)
    limit_inputs: dict[str, dict[str, float | str | None]] = Field(default_factory=dict, alias="limitInputs")
    tracking_risk: TrackingRiskMap | None = Field(default=None, alias="trackingRisk")

    def value(self, name: str) -> float | None:
        """Return one limit's value, or `None` if it could not be derived.

        Parameters
        ----------
        name : `str`
            The limit's name.

        Returns
        -------
        value : `float` or `None`
            The value, or `None` if the limit is missing or has
            insufficient data.
        """
        threshold = self.thresholds.get(name)
        return threshold.value if threshold is not None else None

    def limit(self, name: str) -> QualityLimit | None:
        """Return one limit with its derivation, or `None` if it is missing.

        Parameters
        ----------
        name : `str`
            The limit's name.

        Returns
        -------
        limit : `QualityLimit` or `None`
            The limit, whether or not it could be worked out (check its
            `value`), or `None` if the envelope has no limit of that name.
        """
        return self.thresholds.get(name)
