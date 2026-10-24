"""Purpose: Efficiency of a set of frames, found from raw measurements.

Description: Efficiency (written eta) is the effective exposure time of a set
of frames divided by the time a reference frame would need for the same
result, from 0 to 1 (`QUALITY_METRICS_PLAN.md` section 4). This module builds
the two named references, finds the factors for each frame, combines them,
and splits the lost time among the causes.

Everything here is pure: the caller reads the measurements and passes them
in as `FrameObservation` values, and the functions return results. Nothing
is stored; the results are recomputed each time they are read.

How a frame's efficiency is found
---------------------------------
Each frame has up to three factors, each from 0 to 1:

* Seeing. The reference star width over the frame's effective star width,
  raised to a power that depends on the science goal. The width is the
  registered effective width (`frame.star_width_effective_arcsec`), which
  already includes the pixel blur. No new constant is fitted. That width
  reads about 3 to 6 percent low for stars with wings (validation register
  entry AST-18), so the seeing factor reads slightly high.
* Sky brightness. The reference sky rate over the frame's sky rate. Only
  where the reference has a sky rate.
* Read noise. The share of the frame's noise that is sky photon noise.
  A short frame has a lower factor than a long one.

The sky and read-noise factors come from one exact formula. For a frame of
exposure t with sky rate B and read noise RN, the information per second is
proportional to 1 / (B + RN^2 / t). Against a reference sky rate B0 this is
B0 / (B + RN^2 / t), which splits as (B0 / B) times B / (B + RN^2 / t). The
first part is the sky factor and the second the read-noise factor. A factor
above 1 (a frame better than the reference) is capped at 1 and says so.

A cause that cannot be measured is listed as "not included" with the reason.
No value is invented for it. Transparency (cloud and haze) is never included
until a zero point that is not exploratory exists.

Two effective times exist: the stacker's weighted time
(`weight_effective_seconds`) and the time found here
(`efficiency_effective_seconds`). They are never mixed.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from astrometricslib import (
    PIXEL_BLUR_FWHM_SQUARED_FACTOR,
    UNKNOWN_CAPTURE_SETTING_CAMERA,
    CaptureSetting,
    MeasurementStatus,
    QualityMeasurement,
)
from wayfindinglib.models.equipment_and_site.equipment import Telescope
from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile
from wayfindinglib.models.quality.efficiency import (
    EfficiencyFactor,
    EfficiencyReference,
    EfficiencyReferenceKind,
    EfficiencyResult,
    LossShare,
    NoiseRegime,
    ScienceGoal,
)

# Names of the registered metrics this module reads. Each is pinned to the
# definition version this code was written for. A row of another version is
# not used (decision S13): the frame is reported as "remeasure" instead.
STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC = "frame.star_width_effective_arcsec"
SKY_RATE_METRIC = "frame.sky_rate_e_per_s_per_px"
SKY_NOISE_METRIC = "frame.sky_noise_e"
READ_NOISE_METRIC = "frame.read_noise_e"
PINNED_METRIC_VERSIONS: dict[str, int] = {
    STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC: 1,
    SKY_RATE_METRIC: 2,
    SKY_NOISE_METRIC: 2,
    READ_NOISE_METRIC: 1,
}
"""The definition version of each metric that this module's formulas assume."""

# --- Reference names and versions. Bump the version when the way a reference
# is built changes.
PHYSICAL_REFERENCE_NAME = "physical"
PHYSICAL_REFERENCE_VERSION = 1
BASELINE_REFERENCE_NAME = "baseline"
BASELINE_REFERENCE_VERSION = 1

PHYSICAL_REFERENCE_ENTRY = "WAY-D21"
"""Register row behind the physical reference (diffraction, seeing and pixel
blur
combined)."""
BASELINE_REFERENCE_ENTRY = "WAY-D22"
"""Register row behind the equipment baseline (10th percentile of per-night
medians)."""
EFFICIENCY_MODEL_ENTRY = "WAY-20"
"""Register entry behind the factor formulas and the loss split."""

# --- Reference width
REFERENCE_WAVELENGTH_NM = 550.0
"""Wavelength used for the diffraction limit, in nanometers. 550 nm is the
middle
of the visible band, so it suits a luminance or color filter. A narrowband
filter would use its own wavelength. Design estimate, register row WAY-D20."""

AIRY_FWHM_FACTOR = 1.029
"""Full width at half maximum of the Airy pattern of an unobstructed circular
aperture, in units of wavelength over aperture diameter (1.029 lambda / D, from
the Airy function). A central obstruction narrows the core a little; that is
ignored. Derived, register row WAY-D20."""

ARCSEC_PER_RADIAN = 206264.806
"""Arcseconds in one radian (180 * 3600 / pi)."""


# --- Goals and exponents
SEEING_EXPONENT_POINT_SOURCE = 2.0
"""Faint point source: the time to reach a signal-to-noise ratio scales as the
star's area, which scales as width squared (`QUALITY_METRICS_PLAN.md` section 4
table). Derived."""

SEEING_EXPONENT_ASTROMETRY_SKY_LIMITED = 4.0
"""Astrometric precision, sky-limited: the position error scales as the star
width
over its signal-to-noise ratio (`QUALITY_METRICS_PLAN.md` section 4 table), so
the time to reach a given error scales with the width to the fourth power.
Taken from the plan; register entry WAY-20."""

SEEING_EXPONENT_ASTROMETRY_OTHER = 2.0
"""Astrometric precision, not sky-limited: no sky-limited argument holds, so
the
faint point source exponent is used. Design estimate, register row WAY-D23."""

# --- Noise regime
SKY_LIMITED_VARIANCE_RATIO = 9.0
"""A frame is sky-limited when the sky photon variance is at least this
multiple
of the read noise variance. Derivation: the read-noise factor is eta_RN = s2 /
(s2 + RN^2) with s2 the sky variance. Requiring eta_RN >= 0.9 gives s2 >= 9
RN^2. Used only to label the regime and the depth view, never to change eta.
Register row WAY-D24."""

SOURCE_LIMITED_VARIANCE_RATIO = 1.0
"""A frame is source-limited when the signal's photon variance (the signal in
electrons) is at least this multiple of the sky variance. At a ratio of 1 the
source's own noise is as large as the sky's. Design estimate, register row
WAY-D24."""

# --- Baseline
STAR_WIDTH_NIGHTLY_SPREAD_KEY = "frame.star_width_effective_arcsec.nightly_spread"
"""Key, in a baseline reference's factors, of the one-standard-deviation spread
of the nightly median star widths (sample standard deviation, in arcseconds).
It is not a factor of eta. It tells a reader how far a night's width may sit
from the baseline by ordinary night-to-night change. Present only when the
width baseline is. Register row WAY-D22."""

BASELINE_PERCENTILE = 10.0
"""The baseline takes this percentile of the per-night medians, so it describes
the equipment's good nights (a night with the sharpest stars or the darkest
sky) and not the average. Design estimate, register row WAY-D22."""

MINIMUM_NIGHTS_FOR_BASELINE = 3
"""Fewest nights that make a baseline. With two nights the 10th percentile is
just
the better night. With three, one outlier night cannot set the value alone.
Design estimate, register row WAY-D22."""

DEPTH_MAGNITUDES_PER_LOG_TIME = 1.25
"""Depth lost, in magnitudes, is 1.25 log10(1 / eta) for a sky-limited point
source: the noise falls as the square root of time and a magnitude is 2.5 log10
of a flux ratio, so a time ratio r is 2.5 log10(sqrt(r)) = 1.25 log10(r)
magnitudes. Derived, register entry WAY-20."""

CAUSE_SEEING = "seeing"
CAUSE_SKY = "sky_brightness"
CAUSE_READ_NOISE = "read_noise"
CAUSE_TRANSPARENCY = "transparency"
CAUSE_SKY_MOON = "sky_brightness_moon"
"""The part of the sky-brightness loss that the sky model assigns to the Moon
(model-derived)."""
CAUSE_SKY_OTHER = "sky_brightness_other"
CAUSE_UNKNOWN = "cause_unknown"

TRANSPARENCY_NOT_INCLUDED_REASON = (
    "Cloud and haze need a zero point that is not exploratory. The catalog zero point of the "
    "science plan does not exist yet, so transparency is left out and no value is invented."
)
"""Why transparency is never part of eta (decision S12)."""

_MODEL_CONFIG = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class FrameObservation(BaseModel):
    """The measurements of one frame that efficiency needs.

    The caller reads these from the measurement store (or builds them by
    hand) and passes them in. Missing values are ``None`` and the reason is
    written in `missing_reasons`. A value is never replaced by a default.

    Parameters
    ----------
    frame_id : `str`
        A stable id for the frame (target plus relative path).
    capture_setting : `CaptureSetting`
        The equipment and exposure settings of the frame.
    night_id : `str` or `None`
        The observing night the frame belongs to.
    target_id : `str` or `None`
        The target.
    exposure_seconds : `float` or `None`
        The exposure time of the frame, in seconds. Falls back on the
        capture setting's exposure.
    star_width_effective_arcsec : `float` or `None`
        The effective star width (`frame.star_width_effective_arcsec`), in
        arcseconds.
    sky_rate_e_per_s_per_px : `float` or `None`
        The sky background (`frame.sky_rate_e_per_s_per_px`), in electrons
        per second per pixel.
    sky_noise_e : `float` or `None`
        The sky photon noise in one pixel of one exposure
        (`frame.sky_noise_e`), in electrons.
    read_noise_e : `float` or `None`
        The camera's read noise (`frame.read_noise_e`), in electrons.
        ``None`` when the profile's value was only assumed.
    start_epoch_seconds : `float` or `None`
        When the exposure began, in seconds since the Unix epoch.
    ra_deg, dec_deg : `float` or `None`
        Where the frame points (ICRS), in degrees. Needed by the sky model.
    set_aside_reason : `str` or `None`
        Why the stacker set the frame aside. A frame with a reason is
        counted as lost time and is not given an efficiency.
    assumed_fields : `tuple` [`str`]
        Names of the fields whose rows had the status ``assumed``.
    missing_reasons : `dict` [`str`, `str`]
        Why each missing field is missing.
    """

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel, frozen=True)

    frame_id: str
    capture_setting: CaptureSetting
    night_id: str | None = None
    target_id: str | None = None
    exposure_seconds: float | None = Field(default=None, gt=0.0)
    star_width_effective_arcsec: float | None = Field(default=None, gt=0.0)
    sky_rate_e_per_s_per_px: float | None = Field(default=None, ge=0.0)
    sky_noise_e: float | None = Field(default=None, ge=0.0)
    read_noise_e: float | None = Field(default=None, ge=0.0)
    start_epoch_seconds: float | None = None
    ra_deg: float | None = None
    dec_deg: float | None = None
    set_aside_reason: str | None = None
    assumed_fields: tuple[str, ...] = ()
    missing_reasons: dict[str, str] = Field(default_factory=dict)

    @property
    def effective_exposure_seconds(self) -> float | None:
        """Give the frame's exposure time, falling back on its capture setting.

        Returns
        -------
        exposure_seconds : `float` or `None`
            The exposure time in seconds, or ``None`` if neither is known.
        """
        if self.exposure_seconds:
            return self.exposure_seconds
        return self.capture_setting.exposure_seconds or None


class ReferenceOutcome(BaseModel):
    """A reference, or the reason none could be built.

    Parameters
    ----------
    reference : `EfficiencyReference` or `None`
        The reference, or ``None`` when its inputs are missing.
    reason : `str` or `None`
        Why there is no reference. Set exactly when `reference` is ``None``.
    """

    model_config = _MODEL_CONFIG

    reference: EfficiencyReference | None = None
    reason: str | None = None


class BaselineNight(BaseModel):
    """The medians of one night, for the equipment baseline.

    Parameters
    ----------
    night_id : `str`
        The observing night.
    median_star_width_effective_arcsec : `float` or `None`
        The median effective star width of the night's kept frames.
    median_sky_rate_e_per_s_per_px : `float` or `None`
        The median sky rate of the night's kept frames.
    frame_count : `int`
        How many kept frames the medians come from.
    """

    model_config = _MODEL_CONFIG

    night_id: str
    median_star_width_effective_arcsec: float | None = None
    median_sky_rate_e_per_s_per_px: float | None = None
    frame_count: int = Field(default=0, ge=0)


class NoiseRegimeAssessment(BaseModel):
    """The noise regime of a frame, with a reason when it is unknown.

    Parameters
    ----------
    regime : `NoiseRegime`
        The regime.
    reason : `str` or `None`
        Why the regime is unknown. This is an insufficient-data reason.
    """

    model_config = _MODEL_CONFIG

    regime: NoiseRegime
    reason: str | None = None


class FrameEfficiency(BaseModel):
    """The efficiency of one frame.

    Parameters
    ----------
    frame_id : `str`
        The frame.
    exposure_seconds : `float`
        The frame's exposure time, in seconds.
    eta : `float` or `None`
        The product of the included factors, above 0 and at most 1. ``None``
        when no factor could be included.
    factors : `list` [`EfficiencyFactor`]
        Every cause, included or not.
    regime : `NoiseRegimeAssessment`
        The frame's noise regime.
    """

    model_config = _MODEL_CONFIG

    frame_id: str
    exposure_seconds: float = Field(gt=0.0)
    eta: float | None = Field(default=None, gt=0.0, le=1.0)
    factors: list[EfficiencyFactor] = Field(default_factory=list)
    regime: NoiseRegimeAssessment


class FrameIssue(BaseModel):
    """A frame that could not be given an efficiency, and why.

    Parameters
    ----------
    frame_id : `str`
        The frame.
    reason : `str`
        Why.
    """

    model_config = _MODEL_CONFIG

    frame_id: str
    reason: str


class EfficiencyAssessment(BaseModel):
    """Efficiency of a set of kept frames, with the per-frame results.

    Parameters
    ----------
    result : `EfficiencyResult`
        Eta for the set. Its factor values are the time-weighted geometric
        means of the frames' factors (a typical factor). Eta itself is the
        time-weighted mean of the frames' etas, so the typical factors need
        not multiply to it exactly.
    frame_results : `list` [`FrameEfficiency`]
        One result per frame that was assessed.
    frames_set_aside : `int`
        Frames skipped because the stacker set them aside.
    set_aside_seconds : `float`
        Their total exposure time, in seconds.
    frames_without_efficiency : `list` [`FrameIssue`]
        Kept frames that could not be assessed, with the reason. Their time
        is not in `result.captured_seconds`.
    regime_mixed : `bool`
        Whether the frames were not all in one regime. The result's regime
        is then the regime of most of the captured time.
    notes : `list` [`str`]
        Plain-language remarks, such as which fields were assumed.
    """

    model_config = _MODEL_CONFIG

    result: EfficiencyResult
    frame_results: list[FrameEfficiency] = Field(default_factory=list)
    frames_set_aside: int = Field(default=0, ge=0)
    set_aside_seconds: float = Field(default=0.0, ge=0.0)
    frames_without_efficiency: list[FrameIssue] = Field(default_factory=list)
    regime_mixed: bool = False
    notes: list[str] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# References
# -----------------------------------------------------------------------------


def diffraction_fwhm_arcsec(aperture_mm: float, wavelength_nm: float = REFERENCE_WAVELENGTH_NM) -> float:
    """Give the width of the diffraction-limited star for an aperture.

    Parameters
    ----------
    aperture_mm : `float`
        The clear aperture, in millimeters. Must be above 0.
    wavelength_nm : `float`, optional
        The wavelength, in nanometers.

    Returns
    -------
    fwhm_arcsec : `float`
        1.029 lambda / D, in arcseconds.

    Raises
    ------
    ValueError
        If the aperture or the wavelength is not above 0.
    """
    if aperture_mm <= 0.0 or wavelength_nm <= 0.0:
        raise ValueError("The aperture and the wavelength must be above 0.")
    wavelength_mm = wavelength_nm * 1.0e-6
    return AIRY_FWHM_FACTOR * wavelength_mm / aperture_mm * ARCSEC_PER_RADIAN


def physical_reference(
    aperture_mm: float | None,
    pixel_scale_arcsec: float | None,
    typical_seeing_fwhm_arcsec: float | None = None,
    wavelength_nm: float = REFERENCE_WAVELENGTH_NM,
) -> ReferenceOutcome:
    """Build the physical reference (best star width).

    The reference width is the effective width a perfect frame would have:
    the diffraction limit of the aperture, the site's typical seeing and the
    pixel blur, added in quadrature (as squared widths). When the site's
    seeing is not set, the reference has no width at all: diffraction and pixel
    blur alone are not a width a real night could reach, so the seeing factor
    is "not included" with that reason. The
    sky brightness and the transparency are "not included": the library has
    no dark-sky value and no throughput it could use without inventing
    numbers (decision S2).

    Parameters
    ----------
    aperture_mm : `float` or `None`
        The telescope's clear aperture, in millimeters
        (`Telescope.aperture_mm`).
        ``None`` or 0 counts as missing.
    pixel_scale_arcsec : `float` or `None`
        The imaging pixel scale, in arcseconds per pixel. The measured width
        includes the pixel blur, so the reference must too.
    typical_seeing_fwhm_arcsec : `float` or `None`, optional
        The site's typical good-night star width
        (`SiteProfile.typical_seeing_fwhm_arcsec`). ``None`` or 0 leaves the
        seeing factor "not included" (register row WAY-D01).
    wavelength_nm : `float`, optional
        The wavelength for the diffraction limit.

    Returns
    -------
    outcome : `ReferenceOutcome`
        The reference (name ``physical``), or ``None`` with a reason when the
        aperture or the pixel scale is missing.
    """
    if not aperture_mm or aperture_mm <= 0.0:
        return ReferenceOutcome(
            reason="The telescope's aperture is unknown (focal length or focal ratio missing or 0)."
        )
    if not pixel_scale_arcsec or pixel_scale_arcsec <= 0.0:
        return ReferenceOutcome(reason="The imaging pixel scale is unknown.")
    diffraction = diffraction_fwhm_arcsec(aperture_mm, wavelength_nm)
    not_included = {
        CAUSE_SKY: "No dark-sky value exists that is not an exploratory estimate, so none is invented.",
        CAUSE_TRANSPARENCY: TRANSPARENCY_NOT_INCLUDED_REASON,
    }
    factors: dict[str, float] = {}
    if typical_seeing_fwhm_arcsec and typical_seeing_fwhm_arcsec > 0.0:
        pixel_term_squared = PIXEL_BLUR_FWHM_SQUARED_FACTOR * pixel_scale_arcsec**2
        factors[STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC] = math.sqrt(
            diffraction**2 + typical_seeing_fwhm_arcsec**2 + pixel_term_squared
        )
    else:
        not_included[STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC] = (
            "The site's typical seeing is not set, so there is no best star width to compare against."
        )
    return ReferenceOutcome(
        reference=EfficiencyReference(
            name=PHYSICAL_REFERENCE_NAME,
            version=PHYSICAL_REFERENCE_VERSION,
            kind=EfficiencyReferenceKind.PHYSICAL,
            factors=factors,
            not_included=not_included,
            register_entry=PHYSICAL_REFERENCE_ENTRY,
        )
    )


def physical_reference_for(
    telescope: Telescope, site: SiteProfile, pixel_scale_arcsec: float | None
) -> ReferenceOutcome:
    """Build the physical reference from the configured telescope and site.

    Parameters
    ----------
    telescope : `Telescope`
        The imaging telescope. Its `aperture_mm` is used.
    site : `SiteProfile`
        The observing site. Its `typical_seeing_fwhm_arcsec` is used.
    pixel_scale_arcsec : `float` or `None`
        The imaging pixel scale, in arcseconds per pixel.

    Returns
    -------
    outcome : `ReferenceOutcome`
        See `physical_reference`.
    """
    return physical_reference(telescope.aperture_mm, pixel_scale_arcsec, site.typical_seeing_fwhm_arcsec)


def baseline_key(setting: CaptureSetting) -> str:
    """Name the equipment a baseline belongs to.

    The key holds the camera, telescope, filter, gain setting, offset and
    binning. It does not hold the exposure time: star width and sky rate
    per second do not depend on it (decision S3).

    Parameters
    ----------
    setting : `CaptureSetting`
        A capture setting.

    Returns
    -------
    key : `str`
        For example ``"ASI533MM|RC8|L|gain100|offset10|bin1"``. A missing
        part is written as ``"?"``.
    """

    def part(value: object, prefix: str = "") -> str:
        """Write one part of the key.

        Parameters
        ----------
        value : `object`
            The value, or ``None``.
        prefix : `str`, optional
            Text put before the value.

        Returns
        -------
        text : `str`
            The part.
        """
        return "?" if value is None else f"{prefix}{value}"

    return "|".join([
        setting.camera,
        part(setting.telescope),
        part(setting.filter_name),
        part(setting.gain_setting, "gain"),
        part(setting.offset, "offset"),
        part(setting.binning, "bin"),
    ])


def build_baseline_nights(observations: Iterable[FrameObservation]) -> list[BaselineNight]:
    """Take the median star width and sky rate of each night's kept frames.

    Parameters
    ----------
    observations : `Iterable` [`FrameObservation`]
        Frames of one baseline key (see `baseline_key`). Frames set aside are
        skipped. A frame with no night is skipped.

    Returns
    -------
    nights : `list` [`BaselineNight`]
        One entry per night, in night order.
    """
    widths: dict[str, list[float]] = {}
    rates: dict[str, list[float]] = {}
    counts: dict[str, int] = {}
    for observation in observations:
        if observation.set_aside_reason is not None or observation.night_id is None:
            continue
        night = observation.night_id
        counts[night] = counts.get(night, 0) + 1
        if observation.star_width_effective_arcsec is not None:
            widths.setdefault(night, []).append(observation.star_width_effective_arcsec)
        if observation.sky_rate_e_per_s_per_px is not None:
            rates.setdefault(night, []).append(observation.sky_rate_e_per_s_per_px)
    return [
        BaselineNight(
            night_id=night,
            median_star_width_effective_arcsec=float(np.median(widths[night])) if night in widths else None,
            median_sky_rate_e_per_s_per_px=float(np.median(rates[night])) if night in rates else None,
            frame_count=counts[night],
        )
        for night in sorted(counts)
    ]


def baseline_reference(nights: Sequence[BaselineNight], key: str) -> ReferenceOutcome:
    """Build the equipment baseline: the equipment's own good nights.

    The reference width and sky rate are the 10th percentile of the
    per-night medians. Each needs at least three nights that have it; a
    value with fewer nights is "not included", and with neither the result
    is "no baseline yet" (decision S3).

    Parameters
    ----------
    nights : `Sequence` [`BaselineNight`]
        The nights of one baseline key, from `build_baseline_nights`.
    key : `str`
        The key (`baseline_key`). It is written into the reference's name
        so that a baseline is never used for other equipment.

    Returns
    -------
    outcome : `ReferenceOutcome`
        The reference named ``baseline:<key>``, or ``None`` with a reason.
    """
    width_values = [
        n.median_star_width_effective_arcsec for n in nights if n.median_star_width_effective_arcsec
    ]
    rate_values = [n.median_sky_rate_e_per_s_per_px for n in nights if n.median_sky_rate_e_per_s_per_px]
    factors: dict[str, float] = {}
    not_included: dict[str, str] = {
        CAUSE_TRANSPARENCY: TRANSPARENCY_NOT_INCLUDED_REASON,
    }
    for name, values, label in (
        (STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC, width_values, "star width"),
        (SKY_RATE_METRIC, rate_values, "sky rate"),
    ):
        if len(values) >= MINIMUM_NIGHTS_FOR_BASELINE:
            factors[name] = float(np.percentile(values, BASELINE_PERCENTILE))
            if name == STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC:
                factors[STAR_WIDTH_NIGHTLY_SPREAD_KEY] = float(np.std(values, ddof=1))
        else:
            not_included[name] = (
                f"Only {len(values)} night(s) with a {label}; a baseline needs {MINIMUM_NIGHTS_FOR_BASELINE}."
            )
    if not factors:
        return ReferenceOutcome(
            reason=(
                f"No baseline yet for {key}: it needs {MINIMUM_NIGHTS_FOR_BASELINE} nights of "
                "measured frames."
            )
        )
    return ReferenceOutcome(
        reference=EfficiencyReference(
            name=f"{BASELINE_REFERENCE_NAME}:{key}",
            version=BASELINE_REFERENCE_VERSION,
            kind=EfficiencyReferenceKind.BASELINE,
            factors=factors,
            not_included=not_included,
            register_entry=BASELINE_REFERENCE_ENTRY,
        )
    )


# -----------------------------------------------------------------------------
# Noise regime
# -----------------------------------------------------------------------------


def noise_regime(
    sky_noise_e: float | None, read_noise_e: float | None, source_signal_e: float | None = None
) -> NoiseRegimeAssessment:
    """Say which noise dominates a frame.

    The regime only labels the answer and the depth view. It never changes
    eta.

    * Unknown, with a reason, when the sky noise or the read noise is
      missing (for example a camera with no measured read noise).
    * Source-limited when the signal's variance (its electrons) is at least
      `SOURCE_LIMITED_VARIANCE_RATIO` times the sky variance.
    * Sky-limited when the sky variance is at least
      `SKY_LIMITED_VARIANCE_RATIO` times the read-noise variance.
    * Read-noise-limited otherwise.

    Parameters
    ----------
    sky_noise_e : `float` or `None`
        Sky photon noise in one pixel of one exposure, in electrons.
    read_noise_e : `float` or `None`
        Read noise, in electrons.
    source_signal_e : `float` or `None`, optional
        The target's signal in the same pixel, in electrons. ``None`` skips
        the source-limited test.

    Returns
    -------
    assessment : `NoiseRegimeAssessment`
        The regime and, when unknown, the reason.
    """
    if sky_noise_e is None:
        return NoiseRegimeAssessment(regime=NoiseRegime.UNKNOWN, reason="The sky noise was not measured.")
    if read_noise_e is None:
        return NoiseRegimeAssessment(
            regime=NoiseRegime.UNKNOWN,
            reason="The camera has no measured read noise, so the regime cannot be found.",
        )
    sky_variance = sky_noise_e**2
    if source_signal_e is not None and sky_variance > 0.0:
        if source_signal_e >= SOURCE_LIMITED_VARIANCE_RATIO * sky_variance:
            return NoiseRegimeAssessment(regime=NoiseRegime.SOURCE_LIMITED)
    if sky_variance >= SKY_LIMITED_VARIANCE_RATIO * read_noise_e**2:
        return NoiseRegimeAssessment(regime=NoiseRegime.SKY_LIMITED)
    return NoiseRegimeAssessment(regime=NoiseRegime.READ_NOISE_LIMITED)


def depth_loss_magnitudes(eta: float | None, goal: ScienceGoal, regime: NoiseRegime) -> float | None:
    """Give the depth lost to inefficiency, in magnitudes.

    The formula 1.25 log10(1 / eta) holds only for a sky-limited point
    source, so any other goal or regime gives ``None``.

    Parameters
    ----------
    eta : `float` or `None`
        Efficiency.
    goal : `ScienceGoal`
        The science goal.
    regime : `NoiseRegime`
        The noise regime.

    Returns
    -------
    magnitudes : `float` or `None`
        Magnitudes of depth lost, or ``None`` when the view does not apply.
    """
    if eta is None or eta <= 0.0:
        return None
    if goal != ScienceGoal.FAINT_POINT_SOURCE or regime != NoiseRegime.SKY_LIMITED:
        return None
    return DEPTH_MAGNITUDES_PER_LOG_TIME * math.log10(1.0 / eta)


# -----------------------------------------------------------------------------
# One frame
# -----------------------------------------------------------------------------

_SEEING_NOT_A_TIME_LOSS = {
    ScienceGoal.EXTENDED_OBJECT: (
        "Seeing costs resolution on an extended object, and more time does not repair it."
    ),
    ScienceGoal.BRIGHT_STAR_PHOTOMETRY: (
        "Scintillation or a systematic floor limits bright-star photometry, not the hours."
    ),
    ScienceGoal.SLITLESS_SPECTRAL_RESOLUTION: (
        "Seeing sets the resolution of a slitless spectrum, and more time does not repair it."
    ),
    ScienceGoal.PERIOD_DETECTION: "The spacing of the nights limits period detection, not the hours.",
}
"""Goals for which seeing is not a time loss, with the reason."""

_NO_TIME_FACTORS_GOALS = {
    ScienceGoal.BRIGHT_STAR_PHOTOMETRY: (
        "A bright star is source-limited, so sky brightness and read noise do not set the precision."
    ),
    ScienceGoal.PERIOD_DETECTION: (
        "The window function of the nights, not the exposure time, limits the period search."
    ),
    ScienceGoal.SLITLESS_SPECTRAL_RESOLUTION: (
        "A slitless spectrum is source-limited, so sky brightness and read noise are not charged against it."
    ),
}
"""Goals for which the sky and read-noise factors are also left out. Design
estimate, WAY-D25."""


def seeing_exponent(goal: ScienceGoal, regime: NoiseRegime) -> float | None:
    """Give the power the width ratio is raised to, for a goal.

    Parameters
    ----------
    goal : `ScienceGoal`
        The science goal.
    regime : `NoiseRegime`
        The frame's noise regime.

    Returns
    -------
    exponent : `float` or `None`
        2 for a faint point source, 4 for astrometric precision when
        sky-limited and 2 otherwise, and ``None`` when seeing is not a time
        loss for the goal.
    """
    if goal in _SEEING_NOT_A_TIME_LOSS:
        return None
    if goal == ScienceGoal.ASTROMETRIC_PRECISION:
        if regime == NoiseRegime.SKY_LIMITED:
            return SEEING_EXPONENT_ASTROMETRY_SKY_LIMITED
        return SEEING_EXPONENT_ASTROMETRY_OTHER
    return SEEING_EXPONENT_POINT_SOURCE


def _capped_factor(cause: str, raw_value: float, *, model_derived: bool = False) -> EfficiencyFactor:
    """Build an included factor, capped at 1.

    Parameters
    ----------
    cause : `str`
        The cause.
    raw_value : `float`
        The uncapped factor, above 0.
    model_derived : `bool`, optional
        Whether the value comes from a model.

    Returns
    -------
    factor : `EfficiencyFactor`
        The factor, recoverable with time.
    """
    capped_reason = None
    value = raw_value
    if raw_value > 1.0:
        value = 1.0
        capped_reason = (
            f"The frame is better than the reference ({raw_value:.3f} times); the factor is capped at 1."
        )
    return EfficiencyFactor(
        cause=cause,
        value=value,
        included=True,
        cappedReason=capped_reason,
        recoverableWithTime=True,
        modelDerived=model_derived,
    )


def _left_out_factor(cause: str, reason: str, *, recoverable_with_time: bool = True) -> EfficiencyFactor:
    """Build a factor that is not part of eta.

    Parameters
    ----------
    cause : `str`
        The cause.
    reason : `str`
        Why it is not included.
    recoverable_with_time : `bool`, optional
        Whether more exposure time would repair this loss. A cause that is
        not a time loss for the goal is not.

    Returns
    -------
    factor : `EfficiencyFactor`
        The factor, with no value.
    """
    return EfficiencyFactor(
        cause=cause,
        value=None,
        included=False,
        notIncludedReason=reason,
        recoverableWithTime=recoverable_with_time,
        modelDerived=False,
    )


def frame_efficiency(
    observation: FrameObservation, goal: ScienceGoal, reference: EfficiencyReference
) -> FrameEfficiency | None:
    """Find the efficiency of one frame.

    Parameters
    ----------
    observation : `FrameObservation`
        The frame's measurements.
    goal : `ScienceGoal`
        The science goal.
    reference : `EfficiencyReference`
        The reference to compare against.

    Returns
    -------
    result : `FrameEfficiency` or `None`
        The frame's factors and eta. ``None`` when the frame has no
        exposure time.
    """
    exposure = observation.effective_exposure_seconds
    if exposure is None:
        return None
    sky_noise = observation.sky_noise_e
    if sky_noise is None and observation.sky_rate_e_per_s_per_px is not None:
        sky_noise = math.sqrt(observation.sky_rate_e_per_s_per_px * exposure)
    regime = noise_regime(sky_noise, observation.read_noise_e)
    factors: list[EfficiencyFactor] = []

    # Seeing.
    exponent = seeing_exponent(goal, regime.regime)
    reference_width = reference.factors.get(STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC)
    if exponent is None:
        factors.append(
            _left_out_factor(CAUSE_SEEING, _SEEING_NOT_A_TIME_LOSS[goal], recoverable_with_time=False)
        )
    elif reference_width is None:
        factors.append(
            _left_out_factor(
                CAUSE_SEEING,
                reference.not_included.get(
                    STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC, "The reference has no star width."
                ),
            )
        )
    elif observation.star_width_effective_arcsec is None:
        factors.append(
            _left_out_factor(
                CAUSE_SEEING,
                observation.missing_reasons.get(
                    "star_width_effective_arcsec", "The frame's star width was not measured."
                ),
            )
        )
    else:
        ratio = reference_width / observation.star_width_effective_arcsec
        factors.append(_capped_factor(CAUSE_SEEING, ratio**exponent))

    # Sky brightness and read noise, from the one exact formula B0 / (B + RN^2
    # / t).
    if goal in _NO_TIME_FACTORS_GOALS:
        reason = _NO_TIME_FACTORS_GOALS[goal]
        factors.append(_left_out_factor(CAUSE_SKY, reason, recoverable_with_time=False))
        factors.append(_left_out_factor(CAUSE_READ_NOISE, reason, recoverable_with_time=False))
    else:
        sky_rate = observation.sky_rate_e_per_s_per_px
        reference_sky = reference.factors.get(SKY_RATE_METRIC)
        if reference_sky is None:
            factors.append(
                _left_out_factor(
                    CAUSE_SKY,
                    reference.not_included.get(
                        CAUSE_SKY,
                        reference.not_included.get(SKY_RATE_METRIC, "The reference has no sky rate."),
                    ),
                )
            )
        elif sky_rate is None or sky_rate <= 0.0:
            factors.append(
                _left_out_factor(
                    CAUSE_SKY,
                    observation.missing_reasons.get(
                        "sky_rate_e_per_s_per_px", "The frame's sky rate was not measured."
                    ),
                )
            )
        else:
            factors.append(_capped_factor(CAUSE_SKY, reference_sky / sky_rate))
        if sky_rate is None or sky_rate <= 0.0:
            factors.append(
                _left_out_factor(
                    CAUSE_READ_NOISE,
                    observation.missing_reasons.get(
                        "sky_rate_e_per_s_per_px",
                        "Without the sky rate the read-noise share cannot be found.",
                    ),
                )
            )
        elif observation.read_noise_e is None:
            factors.append(
                _left_out_factor(
                    CAUSE_READ_NOISE,
                    observation.missing_reasons.get("read_noise_e", "The camera has no measured read noise."),
                )
            )
        else:
            sky_electrons = sky_rate * exposure
            read_factor = sky_electrons / (sky_electrons + observation.read_noise_e**2)
            factors.append(_capped_factor(CAUSE_READ_NOISE, read_factor))

    factors.append(_left_out_factor(CAUSE_TRANSPARENCY, TRANSPARENCY_NOT_INCLUDED_REASON))

    included = [factor.value for factor in factors if factor.included and factor.value is not None]
    eta = math.prod(included) if included else None
    return FrameEfficiency(
        frame_id=observation.frame_id,
        exposure_seconds=exposure,
        eta=eta,
        factors=factors,
        regime=regime,
    )


# -----------------------------------------------------------------------------
# A set of frames
# -----------------------------------------------------------------------------


def assess_efficiency(
    observations: Iterable[FrameObservation], goal: ScienceGoal, reference: EfficiencyReference
) -> EfficiencyAssessment:
    """Find the efficiency of a set of frames.

    Frames the stacker set aside (``set_aside_reason`` set) are counted
    apart: they are time lost, not frames with an efficiency. Eta is the
    time-weighted mean of the frames' etas, so the effective time is
    ``sum(exposure * eta)``.

    Parameters
    ----------
    observations : `Iterable` [`FrameObservation`]
        The frames.
    goal : `ScienceGoal`
        The science goal.
    reference : `EfficiencyReference`
        The reference.

    Returns
    -------
    assessment : `EfficiencyAssessment`
        Eta (``None`` when no frame could be assessed), the typical factor
        of each cause, the per-frame results and the frames that could not
        be assessed.
    """
    frame_results: list[FrameEfficiency] = []
    issues: list[FrameIssue] = []
    set_aside_count = 0
    set_aside_seconds = 0.0
    assumed: set[str] = set()
    for observation in observations:
        if observation.set_aside_reason is not None:
            set_aside_count += 1
            set_aside_seconds += observation.effective_exposure_seconds or 0.0
            continue
        assumed.update(observation.assumed_fields)
        found = frame_efficiency(observation, goal, reference)
        if found is None:
            issues.append(FrameIssue(frame_id=observation.frame_id, reason="The frame has no exposure time."))
        elif found.eta is None:
            reasons = "; ".join(
                sorted({f.not_included_reason or "" for f in found.factors if not f.included})
            )
            issues.append(
                FrameIssue(frame_id=observation.frame_id, reason=f"No factor could be found: {reasons}")
            )
        else:
            frame_results.append(found)

    captured = sum(item.exposure_seconds for item in frame_results)
    effective = sum(item.exposure_seconds * (item.eta or 0.0) for item in frame_results)
    eta = effective / captured if captured > 0.0 else None
    regime, mixed = _set_regime(frame_results)
    notes: list[str] = []
    if assumed:
        notes.append("These fields were assumed, not measured: " + ", ".join(sorted(assumed)) + ".")
    if mixed:
        notes.append(
            "The frames are not all in one noise regime; the regime shown is that of most of the time."
        )

    result = EfficiencyResult(
        goal=goal,
        regime=regime,
        reference=reference,
        eta=eta,
        factors=_typical_factors(frame_results),
        capturedSeconds=captured,
        efficiencyEffectiveSeconds=effective if eta is not None else None,
    )
    return EfficiencyAssessment(
        result=result,
        frame_results=frame_results,
        frames_set_aside=set_aside_count,
        set_aside_seconds=set_aside_seconds,
        frames_without_efficiency=issues,
        regime_mixed=mixed,
        notes=notes,
    )


def _set_regime(frame_results: Sequence[FrameEfficiency]) -> tuple[NoiseRegime, bool]:
    """Pick the regime that holds most of the captured time.

    Parameters
    ----------
    frame_results : `Sequence` [`FrameEfficiency`]
        The assessed frames.

    Returns
    -------
    regime, mixed : `tuple` [`NoiseRegime`, `bool`]
        The regime with the most exposure time (``unknown`` for no frames)
        and whether more than one regime appeared.
    """
    seconds: dict[NoiseRegime, float] = {}
    for item in frame_results:
        seconds[item.regime.regime] = seconds.get(item.regime.regime, 0.0) + item.exposure_seconds
    if not seconds:
        return NoiseRegime.UNKNOWN, False
    best = max(seconds, key=lambda regime: seconds[regime])
    return best, len(seconds) > 1


def _typical_factors(frame_results: Sequence[FrameEfficiency]) -> list[EfficiencyFactor]:
    """Combine the frames' factors into one typical factor per cause.

    The typical value is the time-weighted geometric mean over the frames
    that included the cause. A cause that no frame included stays "not
    included" with the first frame's reason.

    Parameters
    ----------
    frame_results : `Sequence` [`FrameEfficiency`]
        The assessed frames.

    Returns
    -------
    factors : `list` [`EfficiencyFactor`]
        One factor per cause, in the order the first frame lists them.
    """
    if not frame_results:
        return []
    typical: list[EfficiencyFactor] = []
    for template in frame_results[0].factors:
        values: list[tuple[float, float]] = []
        capped = False
        for item in frame_results:
            for factor in item.factors:
                if factor.cause == template.cause and factor.included and factor.value is not None:
                    values.append((item.exposure_seconds, factor.value))
                    capped = capped or factor.capped_reason is not None
        if not values:
            typical.append(template)
            continue
        total = sum(weight for weight, _ in values)
        log_mean = sum(weight * math.log(value) for weight, value in values) / total
        typical.append(
            EfficiencyFactor(
                cause=template.cause,
                value=math.exp(log_mean),
                included=True,
                cappedReason=(
                    "Some frames were better than the reference and were capped at 1." if capped else None
                ),
                recoverableWithTime=template.recoverable_with_time,
                modelDerived=template.model_derived,
            )
        )
    return typical


# -----------------------------------------------------------------------------
# The loss split
# -----------------------------------------------------------------------------


def split_kept_frame_deficit(
    frame_results: Sequence[FrameEfficiency],
    *,
    moonless_sky_rate_by_frame: Mapping[str, float] | None = None,
    sky_rate_by_frame: Mapping[str, float] | None = None,
) -> list[LossShare]:
    """Split the kept frames' lost time among the causes.

    A frame of exposure t and efficiency eta loses ``t * (1 - eta)``
    seconds. Its loss is shared by the logarithms of the factors:
    ``share_c = loss * ln(f_c) / ln(eta)``. The shares of one frame add up to
    the frame's loss exactly, so the split always adds up.

    The sky-brightness share can be divided into the Moon's part and the
    rest. The Moon's part of ``ln(f_sky) = ln(B0 / B)`` is ``ln(B_moonless /
    B)``,
    where ``B_moonless`` is the frame's sky rate with the Moon removed by the
    sky model. That part is labeled model-derived.

    Parameters
    ----------
    frame_results : `Sequence` [`FrameEfficiency`]
        The assessed frames.
    moonless_sky_rate_by_frame : `Mapping` [`str`, `float`], optional
        The sky model's sky rate for each frame with no Moon, by frame id.
    sky_rate_by_frame : `Mapping` [`str`, `float`], optional
        The frames' measured sky rates. Needed with the moonless rates.

    Returns
    -------
    shares : `list` [`LossShare`]
        Seconds lost to each cause, largest first.
    """
    seconds: dict[str, float] = {}
    recoverable: dict[str, bool] = {}
    model_derived: dict[str, bool] = {}
    for item in frame_results:
        if item.eta is None or item.eta >= 1.0:
            continue
        loss = item.exposure_seconds * (1.0 - item.eta)
        total_log = math.log(item.eta)
        for factor in item.factors:
            if not factor.included or factor.value is None or factor.value >= 1.0:
                continue
            part = loss * math.log(factor.value) / total_log
            if factor.cause == CAUSE_SKY and moonless_sky_rate_by_frame and sky_rate_by_frame:
                moonless = moonless_sky_rate_by_frame.get(item.frame_id)
                measured = sky_rate_by_frame.get(item.frame_id)
                if moonless and measured and measured > 0.0:
                    moon_log = max(0.0, math.log(measured / moonless))
                    moon_fraction = min(1.0, moon_log / -math.log(factor.value))
                    seconds[CAUSE_SKY_MOON] = seconds.get(CAUSE_SKY_MOON, 0.0) + part * moon_fraction
                    recoverable[CAUSE_SKY_MOON] = True
                    model_derived[CAUSE_SKY_MOON] = True
                    seconds[CAUSE_SKY_OTHER] = seconds.get(CAUSE_SKY_OTHER, 0.0) + part * (
                        1.0 - moon_fraction
                    )
                    recoverable[CAUSE_SKY_OTHER] = True
                    model_derived[CAUSE_SKY_OTHER] = False
                    continue
            seconds[factor.cause] = seconds.get(factor.cause, 0.0) + part
            recoverable[factor.cause] = factor.recoverable_with_time
            model_derived[factor.cause] = factor.model_derived
    shares = [
        LossShare(
            cause=cause,
            seconds=value,
            recoverableWithTime=recoverable[cause],
            modelDerived=model_derived[cause],
        )
        for cause, value in seconds.items()
    ]
    return sorted(shares, key=lambda share: share.seconds, reverse=True)


# -----------------------------------------------------------------------------
# Reading the measurement store
# -----------------------------------------------------------------------------

_FIELD_BY_METRIC = {
    STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC: "star_width_effective_arcsec",
    SKY_RATE_METRIC: "sky_rate_e_per_s_per_px",
    SKY_NOISE_METRIC: "sky_noise_e",
    READ_NOISE_METRIC: "read_noise_e",
}


def frame_observations_from_measurements(
    measurements: Iterable[QualityMeasurement],
    capture_settings: Mapping[int, CaptureSetting],
    *,
    start_epoch_by_frame: Mapping[str, float] | None = None,
    direction_by_frame: Mapping[str, tuple[float, float]] | None = None,
    set_aside_reason_by_frame: Mapping[str, str] | None = None,
) -> list[FrameObservation]:
    """Turn stored frame measurements into `FrameObservation` values.

    Only frame-level rows of the four metrics in `PINNED_METRIC_VERSIONS`
    are read. For each frame and metric the newest row is used. A row of
    another definition version is not used; the field is then missing with
    the reason "remeasure" (decision S13). A ``not_measured`` row leaves the
    field missing with the row's reason. An ``assumed`` row is used, and the
    field is listed in `assumed_fields`, except for the read noise: an
    assumed read noise is not a measurement, so the field stays missing.
    A color frame measured plane by plane gives one value, the median over
    its planes.

    Parameters
    ----------
    measurements : `Iterable` [`QualityMeasurement`]
        Rows from the measurement store (for example
        ``QualityRecords.query``). Rows of other metrics are ignored.
    capture_settings : `Mapping` [`int`, `CaptureSetting`]
        The capture settings by id (``QualityRecords.capture_settings``).
    start_epoch_by_frame : `Mapping` [`str`, `float`], optional
        When each frame's exposure began, by frame id.
    direction_by_frame : `Mapping` [`str`, `tuple`], optional
        The (right ascension, declination) each frame points at, in degrees.
    set_aside_reason_by_frame : `Mapping` [`str`, `str`], optional
        The reason each set-aside frame was set aside, by frame id.

    Returns
    -------
    observations : `list` [`FrameObservation`]
        One per frame, in frame-id order.
    """
    newest: dict[tuple[str, str, str | None], QualityMeasurement] = {}
    context: dict[str, QualityMeasurement] = {}
    for row in measurements:
        if row.subject.level.value != "frame" or row.metric not in PINNED_METRIC_VERSIONS:
            continue
        key = (row.subject.subject_id, row.metric, row.subject.channel)
        current = newest.get(key)
        stamp = (row.measured_at, row.row_id or 0)
        if current is None or stamp >= (current.measured_at, current.row_id or 0):
            newest[key] = row
        context.setdefault(row.subject.subject_id, row)

    per_frame: dict[str, dict[str, list[float]]] = {}
    assumed: dict[str, set[str]] = {}
    missing: dict[str, dict[str, str]] = {}
    for (frame_id, metric, _channel), row in newest.items():
        field = _FIELD_BY_METRIC[metric]
        pinned = PINNED_METRIC_VERSIONS[metric]
        if row.definition_version != pinned:
            missing.setdefault(frame_id, {})[field] = (
                f"{metric} is definition version {row.definition_version}; this code needs version "
                f"{pinned}. Remeasure the frame."
            )
        elif row.status == MeasurementStatus.NOT_MEASURED or row.value is None:
            missing.setdefault(frame_id, {})[field] = row.reason or f"{metric} was not measured."
        elif row.status == MeasurementStatus.ASSUMED and metric == READ_NOISE_METRIC:
            missing.setdefault(frame_id, {})[field] = (
                f"The read noise is assumed, not measured ({row.reason})."
            )
        else:
            per_frame.setdefault(frame_id, {}).setdefault(field, []).append(row.value)
            if row.status == MeasurementStatus.ASSUMED:
                assumed.setdefault(frame_id, set()).add(field)

    observations: list[FrameObservation] = []
    for frame_id in sorted(context):
        row = context[frame_id]
        setting_id = row.subject.capture_setting_id
        setting = capture_settings.get(setting_id) if setting_id is not None else None
        values = {field: float(np.median(numbers)) for field, numbers in per_frame.get(frame_id, {}).items()}
        for field in values:
            missing.get(frame_id, {}).pop(field, None)
        direction = (direction_by_frame or {}).get(frame_id)
        observations.append(
            FrameObservation(
                frame_id=frame_id,
                capture_setting=setting or CaptureSetting(camera=UNKNOWN_CAPTURE_SETTING_CAMERA),
                night_id=row.subject.observing_night_id,
                target_id=row.subject.target_id,
                start_epoch_seconds=(start_epoch_by_frame or {}).get(frame_id),
                ra_deg=direction[0] if direction else None,
                dec_deg=direction[1] if direction else None,
                set_aside_reason=(set_aside_reason_by_frame or {}).get(frame_id),
                assumed_fields=tuple(sorted(assumed.get(frame_id, set()))),
                missing_reasons=missing.get(frame_id, {}),
                **values,
            )
        )
    return observations
