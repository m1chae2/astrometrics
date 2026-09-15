"""Data structures for stars, light curves, and spectroscopy.

This module defines the pure data classes used to track individual stars,
measure how their brightness changes over time, and analyze their light
spectrums.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

# Declares this module's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "AnalysisResult",
    "FileItem",
    "GroupedFrameStat",
    "LightCurve",
    "PeriodogramResult",
    "PlotData",
    "SpectralObservation",
    "StellarObject",
    "StellarSessionMatch",
    "TargetFilesResponse",
    "TransitCandidate",
    "VariableCandidate",
]


class PeriodogramResult(BaseModel):
    """The result of searching a star's brightness for repeating cycles.

    A "periodogram" tests many possible repeat lengths (periods) against
    a star's brightness history and reports which one fits best -- the
    way you might try different guesses for a song's beat until one
    lines up.
    """

    model_config = ConfigDict(populate_by_name=True)

    best_period_days: float = Field(default=0.0, alias="bestPeriodDays")
    # How strong/clear the best repeating pattern is. Higher means the
    # star more clearly brightens and dims on a regular schedule.
    power: float = Field(default=0.0, alias="power")
    # The chance this pattern is just random noise instead of a real
    # repeating cycle. Lower is more trustworthy.
    false_alarm_probability: float = Field(default=1.0, alias="falseAlarmProbability")


class TransitCandidate(BaseModel):
    """Data for a brief, repeating dip in a star's brightness.

    This "transit" pattern is how astronomers find planets around other
    stars, but the same box-shaped dip also shows up when the "star" is
    actually two stars and one passes in front of the other (an
    eclipsing binary) -- the detection math (see
    `VariabilityAnalyzer.run_bls_transit_search`) doesn't know which
    caused it, so this model doesn't assume either.
    """

    model_config = ConfigDict(populate_by_name=True)

    period_days: float = Field(default=0.0, alias="periodDays")
    transit_depth_mag: float = Field(default=0.0, alias="transitDepthMag")
    transit_duration_hours: float = Field(default=0.0, alias="transitDurationHours")
    # The exact time of the middle of one transit, used as a reference
    # point for predicting when the next ones will happen.
    epoch_t0: float = Field(default=0.0, alias="epochT0")
    # Signal-to-noise ratio: how clearly the dip stands out from normal
    # measurement noise. Higher means a more convincing detection.
    transit_snr: float = Field(default=0.0, alias="transitSnr")
    # transit_snr run through the same significance-to-confidence
    # heuristic saturation used for spectral feature detection, so 0
    # means noise and confidence approaches 1 as the dip's SNR grows --
    # not a calibrated detection probability.
    transit_confidence: float = Field(default=0.0, alias="transitConfidence")


class LightCurve(BaseModel):
    """A record of how a star's brightness changes over time."""

    model_config = ConfigDict(populate_by_name=True)

    timestamps: list[datetime] = Field(default_factory=list, alias="timestamps")
    fluxes: list[float] = Field(default_factory=list, alias="fluxes")
    fluxes_normalized: list[float] = Field(default_factory=list, alias="fluxesNormalized")
    # Brightness values with any slow, gradual drift removed (like from
    # clouds or the star slowly rising and setting), leaving just the
    # short-term ups and downs.
    fluxes_detrended: list[float] = Field(default_factory=list, alias="fluxesDetrended")
    airmasses: list[float] = Field(default_factory=list, alias="airmasses")
    magnitudes: list[float] = Field(default_factory=list, alias="magnitudes")
    is_saturated: list[bool] = Field(default_factory=list, alias="isSaturated")
    periodogram: PeriodogramResult | None = Field(default=None, alias="periodogram")
    transit_candidate: TransitCandidate | None = Field(default=None, alias="transitCandidate")


class StellarSessionMatch(BaseModel):
    """Tracks when a star was detected during a specific observing session.

    If a star is observed on 5 different nights, it will have 5 of
    these records combined into its final light curve.
    """

    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(alias="sessionId")
    angular_separation_arcsec: float = Field(alias="angularSeparationArcsec")


class SpectralObservation(BaseModel):
    """A measurement of a star's light split into its component colors."""

    model_config = ConfigDict(populate_by_name=True)

    timestamp: datetime = Field(alias="timestamp")
    wavelengths: list[float] = Field(default_factory=list, alias="wavelengths")
    intensities: list[float] = Field(default_factory=list, alias="intensities")


class StellarObject(BaseModel):
    """The main record for an individual star found in an image."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    id: str = Field(default="", alias="id")
    name: str = Field(default="", alias="name")
    # Where the star is in the sky (right ascension is like longitude,
    # declination is like latitude, but for the sky instead of Earth).
    right_ascension: Any = Field(default="", alias="ra")
    declination: Any = Field(default="", alias="dec")
    # How much light the star gives off (a raw brightness reading).
    flux: Any = Field(default="", alias="flux")
    # The star's brightness on the standard astronomical scale, where
    # LOWER numbers mean a BRIGHTER star (the opposite of most scales).
    magnitude: Any = Field(default="", alias="magnitude")
    # spectral_type and stellar_spectral_type are normally kept equal --
    # both hold the star's classification (like "G2V" for a Sun-like
    # star). The one exception is a synthetic entry used to represent a
    # star cluster, where stellar_spectral_type is set to the fixed
    # label "Cluster" while spectral_type keeps the more general object
    # type from the catalog it came from.
    spectral_type: str = Field(default="", alias="spectralType")
    light_curve: LightCurve | None = Field(default_factory=LightCurve, alias="lightCurve")
    spectra_history: list[SpectralObservation] = Field(default_factory=list, alias="spectraHistory")
    spectrum_data: list[Any] = Field(default_factory=list, alias="spectrumData")
    # The star's raw pixel position and shape info from source detection
    # (e.g. its centroid coordinates), used to relocate it in later
    # pictures.
    star_data: Any = Field(default_factory=list, alias="starData")
    data: list[Any] = Field(default_factory=list, alias="data")
    spectrum_data_processed: dict[str, Any] | None = Field(default=None, alias="spectrumDataProcessed")
    # The pixel box drawn around the star's spectrum trail in the
    # picture, used to redraw that box later without redetecting it.
    rectangle: Any | None = Field(default=None, alias="rectangle")
    # The raw tilt angle measured straight off the detected trail, before
    # any cleanup. dispersion_angle below is the value actually used
    # downstream.
    detected_angle: float | None = Field(default=None, alias="detectedAngle")
    # The angle, in degrees, that this star's spectrum "rainbow" streak
    # is tilted at (see SpectroscopyPipelineQualityMetrics for more on
    # this streak, called the "trail").
    dispersion_angle: float | None = Field(default=None, alias="dispersionAngle")
    # The pixel coordinates running down the middle of that trail, and
    # how wide the trail is at each point.
    trail_centerline_px: list[float] | None = Field(default=None, alias="trailCenterlinePx")
    trail_width_px: list[float] | None = Field(default=None, alias="trailWidthPx")
    # See the comment on spectral_type above -- this is normally the
    # same value, kept as a separate field for the cluster-entry case.
    stellar_spectral_type: str = Field(default="", alias="stellarSpectralType")
    # A spectral type guessed from this star's own extracted spectrum,
    # via template matching against a reference library -- independent
    # of spectral_type, which comes from a catalog lookup. "Unknown" when
    # no spectrum has been classified yet.
    self_determined_spectral_type: str = Field(default="", alias="selfDeterminedSpectralType")
    # How well the winning template matched (a Pearson correlation
    # coefficient, -1 to 1); None until self_determined_spectral_type is set.
    self_determined_spectral_type_confidence: float | None = Field(
        default=None, alias="selfDeterminedSpectralTypeConfidence"
    )
    # Every reference type compared, most probable first -- each entry has
    # "spectral_type", "probability" (sums to 1 across the list, but is a
    # heuristic ranking rather than a calibrated probability), and
    # "correlation". Lets a caller see close calls, not just the winner.
    self_determined_spectral_type_candidates: list[dict[str, Any]] = Field(
        default_factory=list, alias="selfDeterminedSpectralTypeCandidates"
    )
    # Named absorption features (Balmer series, Ca II H&K, etc.) found in
    # this star's own spectrum, most confident first -- see
    # spectral_feature_detector.detect_named_features for what "confidence"
    # means here.
    probable_spectral_features: list[dict[str, Any]] = Field(
        default_factory=list, alias="probableSpectralFeatures"
    )
    target_ids: list[str] = Field(default_factory=list, alias="targetIds")
    # How many pixels out from the star's center to gather light from
    # when measuring its spectrum.
    extraction_radius: int | None = Field(default=None, alias="extractionRadius")
    mean_flux: float | None = Field(default=None, alias="meanFlux")
    # How spread out this star's brightness measurements are relative to
    # their average -- a standard way to compare "noisiness" between
    # stars of different brightness. Higher can mean the star is
    # actually variable, or just noisily measured.
    coefficient_of_variation: float | None = Field(default=None, alias="coefficientOfVariation")
    variability_score: float | None = Field(default=None, alias="variabilityScore")
    session_matches: list[StellarSessionMatch] = Field(default_factory=list, alias="sessionMatches")
    is_catalog_identified: bool = Field(default=False, alias="isCatalogIdentified")

    @computed_field(alias="hasSpectra")
    @property
    def has_spectra(self) -> bool:
        """Check if this star's light spectrum has been measured."""
        return bool(
            self.spectrum_data_processed
            or (self.spectra_history and len(self.spectra_history) > 0)
            or (self.spectrum_data and len(self.spectrum_data) > 0)
            or (self.data and len(self.data) > 0)
        )

    @computed_field(alias="hasPhotometry")
    @property
    def has_photometry(self) -> bool:
        """Check if this star's brightness has been tracked over time."""
        return bool(
            self.light_curve
            and (
                (self.light_curve.timestamps and len(self.light_curve.timestamps) > 0)
                or (self.light_curve.magnitudes and len(self.light_curve.magnitudes) > 0)
                or (self.light_curve.fluxes and len(self.light_curve.fluxes) > 0)
            )
        )

    @computed_field(alias="plotData")
    @property
    def plot_data(self) -> dict[str, list[float]]:
        """The star's spectrum, formatted so it's easy to draw on a graph."""
        return self.get_plot_data()

    def get_plot_data(self) -> dict[str, list[float]]:
        """Convert whatever format the spectrum is in to a standard graph one.

        Returns
        -------
        plot_data : `dict`
            A dictionary with ``"wavelengths"`` (x-axis) and ``"intensities"``
            (y-axis).
        """

        def normalize(wls: Any, flux: Any) -> dict[str, list[float]]:
            if wls and len(wls) > 0 and max(wls) < 2000:
                wls = [float(w) * 10 for w in wls]
            return {"wavelengths": [float(w) for w in wls], "intensities": [float(f) for f in flux]}

        if self.spectra_history:
            latest = self.spectra_history[-1]
            return normalize(latest.wavelengths, latest.intensities)

        if self.spectrum_data_processed and isinstance(self.spectrum_data_processed, dict):
            wls = self.spectrum_data_processed.get("wavelengths_angstrom")
            flux = self.spectrum_data_processed.get("intensities")
            if wls and flux:
                return normalize(wls, flux)

        if self.data and isinstance(self.data, list):
            if len(self.data) == 2 and isinstance(self.data[0], list):
                return normalize(self.data[0], self.data[1])
            if len(self.data) > 2 and isinstance(self.data[0], (list, tuple)):
                try:
                    wls = [row[0] for row in self.data]
                    flux = [row[1] for row in self.data]
                    return normalize(wls, flux)
                except (IndexError, TypeError, AttributeError):  # fmt: skip
                    pass

        if (
            self.data
            and isinstance(self.data, list)
            and len(self.data) > 0
            and not isinstance(self.data[0], (list, tuple))
        ):
            return normalize(list(range(len(self.data))), self.data)

        if self.spectrum_data and len(self.spectrum_data) > 0:
            if isinstance(self.spectrum_data[0], (list, tuple)):
                return normalize(self.spectrum_data[0], self.spectrum_data[1])
            return normalize(list(range(len(self.spectrum_data))), self.spectrum_data)

        return {"wavelengths": [], "intensities": []}

    def serialize(self) -> dict[str, Any]:
        """Package the star's data into a basic dictionary format.

        Returns
        -------
        data : `dict`
            The star's fields, plus the ready-to-graph ``"plotData"``.
        """
        data = self.model_dump(mode="python", by_alias=True)
        raw_plot = self.get_plot_data()
        data["plotData"] = {
            "wavelengths": raw_plot.get("wavelengths", []),
            "intensities": raw_plot.get("intensities", []),
        }
        return data


class VariableCandidate(BaseModel):
    """A star that might be changing brightness over time."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="id")
    mean_flux: float = Field(..., alias="meanFlux", ge=0.0)
    # How spread out this star's brightness measurements are relative to
    # their average. A higher number is one sign the star might really
    # be variable.
    coefficient_of_variation: float = Field(..., alias="coefficientOfVariation", ge=0.0)
    # How confident the code is that this star is truly variable, from
    # 0 (not confident) to 1 (very confident).
    score: float = Field(..., ge=0.0, le=1.0, alias="score")
    ra: float = Field(..., ge=0.0, le=360.0, alias="ra")
    dec: float = Field(..., ge=-90.0, le=90.0, alias="dec")


class AnalysisResult(BaseModel):
    """A summary of what happened when a processing job was run."""

    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(..., pattern="^(started|running|completed|failed|pruned)$")
    target_id: str = Field(..., alias="targetId")
    job_id: str | None = Field(default=None, alias="jobId")
    total_images: int = Field(default=0, alias="totalImages")
    analysis_mode: str = Field(..., alias="analysisMode")
    stars_processed: int = Field(default=0, alias="starsProcessed")
    spectra_extracted: int = Field(default=0, alias="spectraExtracted")
    stars_found: int = Field(default=0, alias="starsFound")
    frames_processed: int = Field(default=0, alias="framesProcessed")
    rejected_count: int = Field(default=0, alias="rejectedCount")
    rejected_files: list[str] = Field(default_factory=list, alias="rejectedFiles")
    variable_candidates: list[VariableCandidate] = Field(default_factory=list, alias="variableCandidates")
    error: str | None = Field(default=None, alias="error")
    message: str | None = Field(default=None, alias="message")


class PlotData(BaseModel):
    """Holds the X and Y coordinates needed to draw a spectrum graph."""

    model_config = ConfigDict(populate_by_name=True)

    wavelengths: list[float] = Field(default_factory=list, alias="wavelengths")
    intensities: list[float] = Field(default_factory=list, alias="intensities")


class FileItem(BaseModel):
    """A single image file ready to be shown in a UI list."""

    model_config = ConfigDict(populate_by_name=True)

    path: str = Field(alias="path")
    name: str = Field(alias="name")
    camera: str = Field(default="Unknown", alias="camera")
    iso: str = Field(default="800", alias="iso")
    exposure: str = Field(default="1.0", alias="exposure")
    filter: str = Field(default="None", alias="filter")
    date: str = Field(default="Unknown", alias="date")


class GroupedFrameStat(BaseModel):
    """A count of how many images share the same filter and exposure time."""

    model_config = ConfigDict(populate_by_name=True)

    filter: str = Field(alias="filter")
    iso: str = Field(alias="iso")
    exposure: str = Field(alias="exposure")
    count: int = Field(..., gt=0, alias="count")
    # Whether matching "dark" calibration frames are available -- these
    # are pictures taken with the lens capped, used to subtract out the
    # camera sensor's own background noise.
    darks: str | None = Field(default=None, alias="darks")
    camera: str | None = Field(default=None, alias="camera")


class TargetFilesResponse(BaseModel):
    """All the files and summary statistics that belong to a single target."""

    model_config = ConfigDict(populate_by_name=True)

    files: list[FileItem] = Field(default_factory=list, alias="files")
    stacked_image: str | None = Field(None, alias="stackedImage")
    stacked_spectral_target: str | None = Field(None, alias="stackedSpectralTarget")
    total_exposure: float = Field(default=0.0, alias="totalExposure", ge=0.0)
    exposure_counts: dict[str, int] = Field(default_factory=dict, alias="exposureCounts")
