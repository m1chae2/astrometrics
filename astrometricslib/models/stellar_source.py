"""Data structures for stars, light curves, and spectroscopy.

This module defines the pure data classes used to track individual stars,
measure how their brightness changes over time, and analyze their light
spectrums.
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
    """The result of searching a star's brightness for repeating cycles."""

    model_config = ConfigDict(populate_by_name=True)

    best_period_days: float = Field(default=0.0, alias="bestPeriodDays")
    power: float = Field(default=0.0, alias="power")
    false_alarm_probability: float = Field(default=1.0, alias="falseAlarmProbability")


class ExoplanetTransitCandidate(BaseModel):
    """Data for when a star dims, possibly because a planet passed in front."""

    model_config = ConfigDict(populate_by_name=True)

    period_days: float = Field(default=0.0, alias="periodDays")
    transit_depth_mag: float = Field(default=0.0, alias="transitDepthMag")
    transit_duration_hours: float = Field(default=0.0, alias="transitDurationHours")
    epoch_t0: float = Field(default=0.0, alias="epochT0")
    transit_snr: float = Field(default=0.0, alias="transitSnr")


class LightCurve(BaseModel):
    """A record of how a star's brightness changes over time."""

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
    """Tracks when a star was detected during a specific observing session.

    If we observe a star on 5 different nights, it will have 5 of these records
    combined into its final light curve.
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
        """Check if we have measured this star's light spectrum."""
        return bool(
            self.spectrum_data_processed
            or (self.spectra_history and len(self.spectra_history) > 0)
            or (self.spectrum_data and len(self.spectrum_data) > 0)
            or (self.data and len(self.data) > 0)
        )

    @computed_field(alias="hasPhotometry")
    @property
    def has_photometry(self) -> bool:
        """Check if we have tracked this star's brightness over time."""
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

    def deserialize(self, object_info: dict[str, Any]) -> None:
        """Load values from a dictionary back into this star object."""
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
    """A star we think might be changing brightness over time."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="id")
    mean_flux: float = Field(..., alias="meanFlux", ge=0.0)
    coefficient_of_variation: float = Field(..., alias="coefficientOfVariation", ge=0.0)
    score: float = Field(..., ge=0.0, le=1.0, alias="score")
    ra: float = Field(..., ge=0.0, le=360.0, alias="ra")
    dec: float = Field(..., ge=-90.0, le=90.0, alias="dec")


class AnalysisResult(BaseModel):
    """A summary of what happened when we ran a processing job."""

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
        """A readable text summary of the job's results."""
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
    """Holds the X and Y coordinates needed to draw a spectrum graph."""

    model_config = ConfigDict(populate_by_name=True)

    wavelengths: list[float] = Field(default_factory=list, alias="wavelengths")
    intensities: list[float] = Field(default_factory=list, alias="intensities")

    def validate_lengths(self) -> PlotData:
        """Make sure we have the same number of X and Y values.

        Returns
        -------
        self : `PlotData`
            This same object, once the lengths are confirmed to match.

        Raises
        ------
        ValueError
            If the list of wavelengths doesn't match the list of intensities.
        """
        if len(self.wavelengths) != len(self.intensities):
            raise ValueError(
                f"Wavelength array size ({len(self.wavelengths)}) must exactly match "
                f"intensity array size ({len(self.intensities)})."
            )
        return self


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
