"""Schema definitions for Scientific Analysis, Spectroscopy, and Stacking.

Models: LightCurve, SpectralObservation, StellarObject,
StellarSessionMatch, VariableCandidate, AnalysisResult, PlotData,
FileItem, GroupedFrameStat, TargetFilesResponse
# REQ: AST-1: Spectroscopy
# REQ: AST-2: Ångström Units
"""

import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

logger = logging.getLogger(__name__)

# Declares this module's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "AnalysisResult",
    "ExoplanetTransitCandidate",
    "FileItem",
    "GroupedFrameStat",
    "LightCurve",
    "PeriodogramResult",
    "PlotData",
    "SpectralObservation",
    "StellarObject",
    "StellarSessionMatch",
    "TargetFilesResponse",
    "VariableCandidate",
]


class PeriodogramResult(BaseModel):
    """Hold Lomb-Scargle periodogram analysis results for a light curve."""

    model_config = ConfigDict(populate_by_name=True)

    best_period_days: float = Field(default=0.0, alias="bestPeriodDays")
    power: float = Field(default=0.0, alias="power")
    false_alarm_probability: float = Field(default=1.0, alias="falseAlarmProbability")


class ExoplanetTransitCandidate(BaseModel):
    """Hold Box-fitting Least Squares (BLS) transit candidate parameters."""

    model_config = ConfigDict(populate_by_name=True)

    period_days: float = Field(default=0.0, alias="periodDays")
    transit_depth_mag: float = Field(default=0.0, alias="transitDepthMag")
    transit_duration_hours: float = Field(default=0.0, alias="transitDurationHours")
    epoch_t0: float = Field(default=0.0, alias="epochT0")
    transit_snr: float = Field(default=0.0, alias="transitSnr")


class LightCurve(BaseModel):
    """Represent a light curve of flux variations over time for a star."""

    model_config = ConfigDict(populate_by_name=True)

    timestamps: list[datetime] = Field(default_factory=list, alias="timestamps")
    fluxes: list[float] = Field(default_factory=list, alias="fluxes")
    fluxes_normalized: list[float] = Field(default_factory=list, alias="fluxesNormalized")
    fluxes_detrended: list[float] = Field(default_factory=list, alias="fluxesDetrended")
    airmasses: list[float] = Field(default_factory=list, alias="airmasses")
    magnitudes: list[float] = Field(default_factory=list, alias="magnitudes")
    is_saturated: list[bool] = Field(default_factory=list, alias="isSaturated")
    periodogram: PeriodogramResult | None = Field(default=None, alias="periodogram")
    transit_candidate: ExoplanetTransitCandidate | None = Field(default=None, alias="transitCandidate")


class StellarSessionMatch(BaseModel):
    """Record one observing session detection folded into a merged curve.

    One instance per contributing session (a merged star observed across
    N sessions carries N of these), mirroring how `EphemerisMatch` records
    a single cross-match rather than a bare boolean.
    """

    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(alias="sessionId")
    angular_separation_arcsec: float = Field(alias="angularSeparationArcsec")


class SpectralObservation(BaseModel):
    """Represent a single spectral extraction at a specific timestamp."""

    model_config = ConfigDict(populate_by_name=True)

    timestamp: datetime = Field(alias="timestamp")
    wavelengths: list[float] = Field(default_factory=list, alias="wavelengths")
    intensities: list[float] = Field(default_factory=list, alias="intensities")


class StellarObject(BaseModel):
    """Define a stellar object with coordinates, flux, and spectra."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    id: str = Field(default="", alias="id")
    name: str = Field(default="", alias="name")
    right_ascension: Any = Field(default="", alias="ra")
    declination: Any = Field(default="", alias="dec")
    flux: Any = Field(default="", alias="flux")
    magnitude: Any = Field(default="", alias="magnitude")
    spectral_type: str = Field(default="", alias="spectralType")
    light_curve: LightCurve | None = Field(default_factory=LightCurve, alias="lightCurve")
    spectra_history: list[SpectralObservation] = Field(default_factory=list, alias="spectraHistory")
    spectrum_data: list[Any] = Field(default_factory=list, alias="spectrumData")
    star_data: Any = Field(default_factory=list, alias="starData")
    data: list[Any] = Field(default_factory=list, alias="data")
    spectrum_data_processed: dict[str, Any] | None = Field(default=None, alias="spectrumDataProcessed")
    rect: Any | None = Field(default=None, alias="rect")
    rectangle: Any | None = Field(default=None, alias="rectangle")
    detected_angle: float | None = Field(default=None, alias="detectedAngle")
    dispersion_angle: float | None = Field(default=None, alias="dispersionAngle")
    trail_centerline_px: list[float] | None = Field(default=None, alias="trailCenterlinePx")
    trail_width_px: list[float] | None = Field(default=None, alias="trailWidthPx")
    stellar_spectral_type: str = Field(default="", alias="stellarSpectralType")
    camera: str | None = Field(default=None, alias="camera")
    target_ids: list[str] = Field(default_factory=list, alias="targetIds")
    extraction_radius: int | None = Field(default=None, alias="extractionRadius")
    mean_flux: float | None = Field(default=None, alias="meanFlux")
    coefficient_of_variation: float | None = Field(default=None, alias="coefficientOfVariation")
    variability_score: float | None = Field(default=None, alias="variabilityScore")
    session_matches: list[StellarSessionMatch] = Field(default_factory=list, alias="sessionMatches")
    is_catalog_identified: bool = Field(default=False, alias="isCatalogIdentified")

    @computed_field(alias="hasSpectra")
    @property
    def has_spectra(self) -> bool:
        """True if this stellar object has spectral data."""
        return bool(
            self.spectrum_data_processed
            or (self.spectra_history and len(self.spectra_history) > 0)
            or (self.spectrum_data and len(self.spectrum_data) > 0)
            or (self.data and len(self.data) > 0)
        )

    @computed_field(alias="hasPhotometry")
    @property
    def has_photometry(self) -> bool:
        """True if this object has light curve photometry data."""
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
        """Normalized plot data containing wavelengths and intensities."""
        return self.get_plot_data()

    def get_plot_data(self) -> dict[str, list[float]]:
        """Normalize internal spectrum formats into a canonical plot format.

        Returns
        -------
        plot_data : `dict` [`str`, `list` [`float`]]
            Dict with ``"wavelengths"`` and ``"intensities"`` keys.
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
                except IndexError, TypeError, AttributeError:
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
        """Convert the object to a dict, including normalized plot data.

        Returns
        -------
        data : `dict`
            The object's field values plus a ``"plotData"`` key.
        """
        data = self.model_dump(mode="python", by_alias=True)
        raw_plot = self.get_plot_data()
        data["plotData"] = {
            "wavelengths": raw_plot.get("wavelengths", []),
            "intensities": raw_plot.get("intensities", []),
        }
        return data

    def deserialize(self, object_info: dict[str, Any]) -> None:
        """Hydrate this stellar object using the provided dictionary."""
        if not isinstance(object_info, dict):
            return

        for key, val in object_info.items():
            try:
                if val is None:
                    continue
                if isinstance(val, (str, bytes)) and len(val) == 0:
                    continue
                if isinstance(val, (list, dict)) and len(val) == 0:
                    continue
                setattr(self, key, val)
            except Exception as exc:
                logger.debug("Skipping round-trip field '%s': %s", key, exc)
                continue


class VariableCandidate(BaseModel):
    """Represent a stellar object flagged as a variable star candidate."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="id")
    mean_flux: float = Field(..., alias="meanFlux", ge=0.0)
    coefficient_of_variation: float = Field(..., alias="coefficientOfVariation", ge=0.0)
    score: float = Field(..., ge=0.0, le=1.0, alias="score")
    ra: float = Field(..., ge=0.0, le=360.0, alias="ra")
    dec: float = Field(..., ge=-90.0, le=90.0, alias="dec")


class AnalysisResult(BaseModel):
    """Summarize an analysis run of the spectroscopy/variability pipeline."""

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

    @property
    def summary(self) -> str:
        """Formatted summary of the analysis execution results."""
        rejected_cnt = len(self.rejected_files) if self.rejected_files else self.rejected_count
        lines = [
            f"Analysis Target: {self.target_id}",
            f"Status: {self.status.upper()}",
            f"Mode: {self.analysis_mode}",
            f"Total Images: {self.total_images}",
            f"Frames Processed: {self.frames_processed}",
            f"Rejected Files: {rejected_cnt}",
            f"Stars Found: {self.stars_found}",
            f"Stars Processed: {self.stars_processed}",
            f"Variable Star Candidates: {len(self.variable_candidates) if self.variable_candidates else 0}",
        ]
        return "\n".join(lines)


class PlotData(BaseModel):
    """Hold wavelengths and flux intensities for interactive plotting."""

    model_config = ConfigDict(populate_by_name=True)

    wavelengths: list[float] = Field(default_factory=list, alias="wavelengths")
    intensities: list[float] = Field(default_factory=list, alias="intensities")

    def validate_lengths(self) -> PlotData:
        """Assert parity between coordinate and telemetry arrays.

        Returns
        -------
        validated : `PlotData`
            This instance, unchanged, once validated.

        Raises
        ------
        ValueError
            Raised if `wavelengths` and `intensities` differ in
            length.
        """
        if len(self.wavelengths) != len(self.intensities):
            raise ValueError(
                f"Wavelength array size ({len(self.wavelengths)}) must exactly match "
                f"intensity array size ({len(self.intensities)})."
            )
        return self


class FileItem(BaseModel):
    """Represent a single file item for display in the file browser."""

    model_config = ConfigDict(populate_by_name=True)

    path: str = Field(alias="path")
    name: str = Field(alias="name")
    camera: str = Field(default="Unknown", alias="camera")
    iso: str = Field(default="800", alias="iso")
    exposure: str = Field(default="1.0", alias="exposure")
    filter: str = Field(default="None", alias="filter")
    date: str = Field(default="Unknown", alias="date")


class GroupedFrameStat(BaseModel):
    """Represent frame statistics grouped by filter and exposure."""

    model_config = ConfigDict(populate_by_name=True)

    filter: str = Field(alias="filter")
    iso: str = Field(alias="iso")
    exposure: str = Field(alias="exposure")
    count: int = Field(..., gt=0, alias="count")
    darks: str | None = Field(default=None, alias="darks")
    camera: str | None = Field(default=None, alias="camera")


class TargetFilesResponse(BaseModel):
    """Represent the full file response for a target."""

    model_config = ConfigDict(populate_by_name=True)

    files: list[FileItem] = Field(default_factory=list, alias="files")
    stacked_image: str | None = Field(None, alias="stackedImage")
    stacked_spectral_target: str | None = Field(None, alias="stackedSpectralTarget")
    total_exposure: float = Field(default=0.0, alias="totalExposure", ge=0.0)
    exposure_counts: dict[str, int] = Field(default_factory=dict, alias="exposureCounts")
