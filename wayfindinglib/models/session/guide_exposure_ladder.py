"""Purpose: Guide Exposure Test Models.

Description: The result of a live test of the guide camera. The test takes a
short series of guide frames at each of several exposure lengths while the
mount tracks without guiding. For each length it measures how bright the guide
star is, whether it saturates, and how much the measured star position jumps
from frame to frame. That jump is the noise the guider would chase, so it is
compared directly with the guiding error the equipment can absorb.

The test answers "does my guide camera need a longer exposure?" with
measurements instead of a rule of thumb: the answer is the shortest exposure
whose position noise is small enough and whose star does not saturate.
"""

from pydantic import BaseModel, ConfigDict, Field


class GuideExposureResult(BaseModel):
    """What one exposure length showed.

    Attributes
    ----------
    exposure_seconds : `float`
        The exposure length.
    frames_measured : `int`
        Frames in which the star was found.
    peak_adu : `float` or `None`
        Brightest pixel of the star, in camera counts above nothing (raw).
    flux_adu : `float` or `None`
        Total light of the star above the background, in camera counts. The
        guide log calls this the star mass.
    flux_per_second : `float` or `None`
        `flux_adu` divided by the exposure, for comparing different lengths.
    background_adu : `float` or `None`
        Median pixel value of the frame.
    noise_adu : `float` or `None`
        Scatter of the background pixels, in counts.
    jitter_arcsec : `float` or `None`
        How much the measured star position jumps from frame to frame, per
        axis, in arcseconds. Slow drift is removed, so what remains is seeing
        and measurement noise, the part a guider would chase.
    jitter_fraction_of_limit : `float` or `None`
        `jitter_arcsec` divided by the acceptable guiding error.
    excess_jitter_arcsec : `float` or `None`
        The position noise beyond the best unsaturated exposure tried, added in
        quadrature: ``sqrt(jitter^2 - best^2)``. Seeing moves the star at every
        exposure and no camera setting removes it, so what a short exposure can
        be blamed for is only the noise above the floor the best exposure
        reaches. With a single exposure the floor is zero.
    saturated : `bool`
        Whether the star reaches the camera's ceiling, so its brightness and
        position are not trustworthy.
    acceptable : `bool`
        Whether the star was measured, is not saturated, and its excess
        jitter is within the allowed share of the guiding error.
    """

    model_config = ConfigDict(populate_by_name=True)

    exposure_seconds: float = Field(alias="exposureSeconds")
    frames_measured: int = Field(default=0, alias="framesMeasured")
    peak_adu: float | None = Field(default=None, alias="peakAdu")
    flux_adu: float | None = Field(default=None, alias="fluxAdu")
    flux_per_second: float | None = Field(default=None, alias="fluxPerSecond")
    background_adu: float | None = Field(default=None, alias="backgroundAdu")
    noise_adu: float | None = Field(default=None, alias="noiseAdu")
    jitter_arcsec: float | None = Field(default=None, alias="jitterArcsec")
    jitter_fraction_of_limit: float | None = Field(default=None, alias="jitterFractionOfLimit")
    excess_jitter_arcsec: float | None = Field(default=None, alias="excessJitterArcsec")
    saturated: bool = False
    acceptable: bool = False


class GuideExposureLadder(BaseModel):
    """The outcome of a guide exposure test.

    Attributes
    ----------
    results : `list` [`GuideExposureResult`]
        One per exposure length, shortest first.
    recommended_exposure_seconds : `float` or `None`
        The shortest acceptable exposure, or `None` if none was.
    guiding_rms_limit_arcsec : `float` or `None`
        The acceptable guiding error per axis for this equipment.
    jitter_limit_arcsec : `float` or `None`
        The most excess position noise allowed. Noise adds in quadrature to the
        rest of the error, so this is the noise that widens stars by no more
        than half the blur tolerance.
    plate_scale_arcsec_per_px : `float`
        The guide camera's plate scale.
    ceiling_adu : `float`
        The value at which the guide camera saturates.
    summary : `str`
        The finding in plain language.
    """

    model_config = ConfigDict(populate_by_name=True)

    results: list[GuideExposureResult] = Field(default_factory=list)
    recommended_exposure_seconds: float | None = Field(default=None, alias="recommendedExposureSeconds")
    guiding_rms_limit_arcsec: float | None = Field(default=None, alias="guidingRmsLimitArcsec")
    jitter_limit_arcsec: float | None = Field(default=None, alias="jitterLimitArcsec")
    plate_scale_arcsec_per_px: float = Field(alias="plateScaleArcsecPerPx")
    ceiling_adu: float = Field(alias="ceilingAdu")
    summary: str = ""
