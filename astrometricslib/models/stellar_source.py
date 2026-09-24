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
    "PeriodogramResult",
    "PhotometryResult",
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
    # What the search concluded: "detected", "possible", "not_detected" or
    # "insufficient_data" (the measurements are too few or too short to
    # test any repeat). Only "detected" and "possible" results say anything
    # about the star; "best_period_days" of the others is just the
    # strongest of many chance peaks.
    verdict: str = Field(default="", alias="verdict")
    # A sentence explaining a verdict that needs it (for example why the
    # data was insufficient).
    note: str = Field(default="", alias="note")
    # How many full cycles of the best period fit in the observed time.
    cycles_observed: float | None = Field(default=None, alias="cyclesObserved")
    # The shortest and longest period the search could test.
    searched_min_period_days: float | None = Field(default=None, alias="searchedMinPeriodDays")
    searched_max_period_days: float | None = Field(default=None, alias="searchedMaxPeriodDays")


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
    # The chance that shuffling the same measurements gives a dip pattern
    # at least this strong. Lower is more trustworthy.
    false_alarm_probability: float = Field(default=1.0, alias="falseAlarmProbability")
    # How many separate dips were seen, and how many measurements fell
    # inside them. One event is not a repeating pattern.
    transit_count: int = Field(default=0, alias="transitCount")
    points_in_transit: int = Field(default=0, alias="pointsInTransit")
    # "detected", "possible", "not_detected" or "insufficient_data". Only
    # "detected" and "possible" results say anything about the star.
    verdict: str = Field(default="", alias="verdict")
    note: str = Field(default="", alias="note")
    searched_min_period_days: float | None = Field(default=None, alias="searchedMinPeriodDays")
    searched_max_period_days: float | None = Field(default=None, alias="searchedMaxPeriodDays")


class PhotometryResult(BaseModel):
    """A record of how a star's brightness changes over time: a light curve."""

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
    mean_flux: float | None = Field(default=None, alias="meanFlux")
    # How spread out this star's brightness measurements are relative to
    # their average -- a standard way to compare "noisiness" between
    # stars of different brightness. Higher can mean the star is
    # actually variable, or just noisily measured. The single stored
    # source of truth for this star's variability; StellarObject's own
    # variability_score below is just this same number on a different
    # scale, computed rather than stored so the two can never drift apart.
    coefficient_of_variation: float | None = Field(default=None, alias="coefficientOfVariation")


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


class SpectroscopyResult(BaseModel):
    """A star's own extracted spectrum, and what it suggests about the star.

    Bundles spectroscopy's results the same way `PhotometryResult` bundles
    photometry's: the processed measurement itself alongside what was
    derived from it, in one place on `StellarObject`, instead of as
    several same-topic fields scattered directly on the star.
    """

    model_config = ConfigDict(populate_by_name=True)

    wavelengths_angstrom: list[float] = Field(default_factory=list, alias="wavelengthsAngstrom")
    intensities: list[float] = Field(default_factory=list, alias="intensities")
    # Only set for a camera with a known quantum-efficiency curve on
    # file -- see quantum_efficiency_correction.py.
    quantum_efficiency_corrected_intensities: list[float] | None = Field(
        default=None, alias="quantumEfficiencyCorrectedIntensities"
    )
    # A spectral type guessed from this star's own extracted spectrum,
    # via template matching against a reference library -- independent
    # of StellarObject.spectral_type, which comes from a catalog
    # lookup. "Unknown" when no spectrum has been classified yet.
    self_determined_spectral_type: str = Field(default="", alias="selfDeterminedSpectralType")
    # How well the winning template matched (a Pearson correlation
    # coefficient, -1 to 1); None until self_determined_spectral_type is set.
    self_determined_spectral_type_confidence: float | None = Field(
        default=None, alias="selfDeterminedSpectralTypeConfidence"
    )
    # How far the winning reference is from this spectrum: the root-mean-
    # square difference between the spectrum and the reference scaled to
    # its brightness, as a fraction of the spectrum's average brightness.
    # Lower is better; above about 0.15 the match is poor. `None` when no
    # type was found.
    self_determined_spectral_type_rms: float | None = Field(
        default=None, alias="selfDeterminedSpectralTypeRms"
    )
    # Why no spectral type was determined (for example the trail left the
    # image), or empty when one was.
    self_determined_spectral_type_note: str = Field(default="", alias="selfDeterminedSpectralTypeNote")
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
    # Where the star's zero-order image sits, as an (x, y) pixel pair, in
    # the spectroscopy image. `StellarObject.star_data` holds the star's
    # position in the normal (astrometry) image, and the two pictures
    # are different pixel grids, so the spectroscopy position is kept
    # here instead. `rectangle` and `trail_centerline_px` below are in
    # this same spectroscopy-image grid.
    star_position_px: list[float] | None = Field(default=None, alias="starPositionPx")
    # The wavelength range, [lowest, highest] in Angstroms, the extraction
    # asked for before any samples were dropped. The spectrum arrays above
    # only hold the part of it that was on the image and inside the
    # camera's sensitive range, so comparing the two shows how much was lost.
    requested_wavelength_range_angstrom: list[float] | None = Field(
        default=None, alias="requestedWavelengthRangeAngstrom"
    )
    # The fraction (0 to 1) of the requested samples that were on the image
    # and inside the camera's range. Below 1.0, part of the spectrum trail
    # ran off the edge of the picture. `None` for a spectrum saved before
    # this was recorded.
    valid_fraction: float | None = Field(default=None, alias="validFraction")
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
    # An audit warning, one value per sample of the spectrum above: how many
    # times brighter this star is at half that wavelength. Second-order light
    # from the blue can add to a red wavelength, and a large ratio means
    # even a small amount would matter (see second_order_risk). It changes
    # nothing in the spectrum. 0.0 where half the wavelength was not
    # measured. `None` for a spectrum saved before this was recorded.
    second_order_blue_to_red_ratio: list[float] | None = Field(
        default=None, alias="secondOrderBlueToRedRatio"
    )
    # How much the instrument blurred this spectrum, in Angstroms, worked
    # out from the trail width above (see spectral_resolution). The
    # classification and the feature tests were run at this width. `None`
    # when the trail width was not available, in which case they used the
    # fixed fallback resolution instead.
    resolution_element_angstrom: float | None = Field(default=None, alias="resolutionElementAngstrom")
    # How many pixels out from the star's center to gather light from
    # when measuring its spectrum.
    extraction_radius: int | None = Field(default=None, alias="extractionRadius")


class StellarObject(BaseModel):
    """The main record for an individual star found in an image."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    id: str = Field(default="", alias="id")
    name: str = Field(default="", alias="name")
    # Where the star is in the sky (right ascension is like longitude,
    # declination is like latitude, but for the sky instead of Earth).
    # `None` until identification/resolution sets it -- e.g. the
    # extended-target "Cluster" entry has no single point position.
    # Stays `Any` (not `float`) rather than `""` -- see the note on
    # flux below; the same tolerance is needed here too.
    right_ascension: Any = Field(default=None, alias="ra")
    declination: Any = Field(default=None, alias="dec")
    # How much light the star gives off (a raw brightness reading).
    # `Any`, not `float`: pipeline code transiently stashes numpy/
    # astropy values here before a save normalizes them (see
    # datastore.local_database.safe_json_dumps) -- `None` is just the
    # "not yet known" default, not the field's only valid shape.
    flux: Any = Field(default=None, alias="flux")
    # The star's brightness on the standard astronomical scale, where
    # LOWER numbers mean a BRIGHTER star (the opposite of most scales).
    # `None` means not yet known, not "magnitude zero" -- same `Any`
    # tolerance as flux above.
    magnitude: Any = Field(default=None, alias="magnitude")
    # spectral_type and stellar_spectral_type are normally kept equal --
    # both hold the star's classification (like "G2V" for a Sun-like
    # star). The one exception is a synthetic entry used to represent a
    # star cluster, where stellar_spectral_type is set to the fixed
    # label "Cluster" while spectral_type keeps the more general object
    # type from the catalog it came from.
    spectral_type: str = Field(default="", alias="spectralType")
    # This star's brightness measured over time -- see PhotometryResult.
    # Mirrors spectroscopy below: one nested result per domain, instead
    # of that domain's fields loose on the star.
    photometry: PhotometryResult | None = Field(default_factory=PhotometryResult, alias="photometry")
    spectra_history: list[SpectralObservation] = Field(default_factory=list, alias="spectraHistory")
    # The star's raw pixel position and shape info from source detection
    # (e.g. its centroid coordinates), used to relocate it in later
    # pictures. Defaults to an empty dict, not a list -- every real
    # consumer treats this as a dict (`.get("xcentroid", ...)`).
    star_data: Any = Field(default_factory=dict, alias="starData")
    # How big the star looks in the image it was detected in, as a radius in
    # pixels (measured by source detection). Used to size the on-screen
    # circle drawn around the star. `None` until detection has measured it.
    radius_px: float | None = Field(default=None, alias="radiusPx")
    # This star's own extracted spectrum and what it suggests about the
    # star -- see SpectroscopyResult. Mirrors photometry above: one
    # nested result per domain, instead of that domain's fields loose
    # on the star.
    spectroscopy: SpectroscopyResult | None = Field(default_factory=SpectroscopyResult, alias="spectroscopy")
    # See the comment on spectral_type above -- this is normally the
    # same value, kept as a separate field for the cluster-entry case.
    stellar_spectral_type: str = Field(default="", alias="stellarSpectralType")
    target_ids: list[str] = Field(default_factory=list, alias="targetIds")
    session_matches: list[StellarSessionMatch] = Field(default_factory=list, alias="sessionMatches")
    is_catalog_identified: bool = Field(default=False, alias="isCatalogIdentified")

    @computed_field(alias="variabilityScore")
    @property
    def variability_score(self) -> float | None:
        """How much this star's brightness jumps around, on a display scale.

        Returns
        -------
        variability_score : `float` or `None`
            `photometry.coefficient_of_variation` multiplied by 100, or
            `None` before any variability has been measured for this star.
        """
        cv = self.photometry.coefficient_of_variation if self.photometry else None
        return cv * 100.0 if cv is not None else None

    @computed_field(alias="hasSpectra")
    @property
    def has_spectra(self) -> bool:
        """Check if this star's light spectrum has been measured."""
        return bool(
            (self.spectroscopy and self.spectroscopy.wavelengths_angstrom)
            or (self.spectra_history and len(self.spectra_history) > 0)
        )

    @computed_field(alias="hasPhotometry")
    @property
    def has_photometry(self) -> bool:
        """Check if this star's brightness has been tracked over time."""
        return bool(
            self.photometry
            and (
                (self.photometry.timestamps and len(self.photometry.timestamps) > 0)
                or (self.photometry.magnitudes and len(self.photometry.magnitudes) > 0)
                or (self.photometry.fluxes and len(self.photometry.fluxes) > 0)
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

        if self.spectroscopy and self.spectroscopy.wavelengths_angstrom and self.spectroscopy.intensities:
            return normalize(self.spectroscopy.wavelengths_angstrom, self.spectroscopy.intensities)

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
    # be variable. The single stored source of truth here too -- see
    # PhotometryResult.coefficient_of_variation.
    coefficient_of_variation: float = Field(..., alias="coefficientOfVariation", ge=0.0)
    ra: float = Field(..., ge=0.0, le=360.0, alias="ra")
    dec: float = Field(..., ge=-90.0, le=90.0, alias="dec")

    @computed_field(alias="score")
    @property
    def score(self) -> float:
        """How confident the code is that this star is truly variable.

        Returns
        -------
        score : `float`
            `coefficient_of_variation`, capped at 1.0 so it reads as a
            0 (not confident) to 1 (very confident) score.
        """
        return min(1.0, self.coefficient_of_variation)


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
