"""Purpose: Telemetry Domain Models.

Description: Guiding samples, alignment attempts, and live guiding
status, carried forward unchanged from the deprecated
`observatory.py` -- these models and their computed properties are
untouched by the three-function redesign
(`Wayfinding_Library_Architecture.md` §2.4.7). Once `AUTOGUIDING`
reaches `AUTHORITATIVE`, `GuidingSample` records are produced by this
library's own guiding correction computation rather than parsed from
the incumbent guider, with identical fields, so downstream consumers
are unaffected by the transition.
"""

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GuidingSample(BaseModel):
    """Represents a single telemetry point from a guiding run."""

    model_config = ConfigDict(populate_by_name=True)
    time: float = Field(..., ge=0.0)
    dra: float
    ddec: float
    pulse_ra: float = Field(..., alias="pulseRa")
    pulse_dec: float = Field(..., alias="pulseDec")
    snr: float | None = Field(None, alias="snr")
    rms_ra: float | None = Field(None, alias="rmsRa")
    rms_dec: float | None = Field(None, alias="rmsDec")

    @property
    def total_drift(self) -> float:
        """Calculate the magnitude of the drift vector.

        This is the total coordinate error combining RA and DEC drift.

        """
        return math.sqrt(self.dra**2 + self.ddec**2)


class AlignmentAttempt(BaseModel):
    """Represents the result of a plate-solving alignment attempt."""

    model_config = ConfigDict(populate_by_name=True)
    status: str = Field(..., pattern="^(solving|failed|warning|aligned|idle)$")
    delta_ra_arcsec: float | None = Field(default=None, alias="deltaRaArcsec")
    delta_dec_arcsec: float | None = Field(default=None, alias="deltaDecArcsec")
    ra: float | None = Field(default=None, alias="ra")
    dec: float | None = Field(default=None, alias="dec")
    pointing_error_arcsec: float | None = Field(default=None, alias="pointingErrorArcsec")
    timestamp: float | None = Field(default=None, alias="timestamp")
    target_name: str | None = Field(default=None, alias="targetName")

    @property
    def pointing_error(self) -> float | None:
        """Calculate the total coordinate pointing offset magnitude."""
        if self.pointing_error_arcsec is not None:
            return self.pointing_error_arcsec
        if self.delta_ra_arcsec is not None and self.delta_dec_arcsec is not None:
            return math.sqrt(self.delta_ra_arcsec**2 + self.delta_dec_arcsec**2)
        return None


class PolarAlignmentStatus(BaseModel):
    """Status and measurement metrics from Polar Alignment Assistant (PAA)."""

    model_config = ConfigDict(populate_by_name=True)
    status: str = Field(default="idle", pattern="^(idle|in_progress|aligned|warning)$")
    total_error_arcsec: float | None = Field(default=None, alias="totalErrorArcsec")
    alt_error_arcsec: float | None = Field(default=None, alias="altErrorArcsec")
    az_error_arcsec: float | None = Field(default=None, alias="azErrorArcsec")
    pole_ra: float | None = Field(default=None, alias="poleRa")
    pole_dec: float | None = Field(default=None, alias="poleDec")
    paa_points: list[dict[str, Any]] = Field(default_factory=list, alias="paaPoints")
    timestamp: float | None = Field(default=None, alias="timestamp")


class AlignmentSessionSummary(BaseModel):
    """Summary of a past observing session's alignment and polar telemetry."""

    model_config = ConfigDict(populate_by_name=True)
    session_id: str = Field(..., alias="sessionId")
    session_date: str = Field(..., alias="sessionDate")
    sync_count: int = Field(default=0, alias="syncCount")
    target_count: int | None = Field(default=None, alias="targetCount")
    start_time: float | None = Field(default=None, alias="startTime")
    end_time: float | None = Field(default=None, alias="endTime")
    avg_error_arcsec: float | None = Field(default=None, alias="avgErrorArcsec")
    polar_error_arcsec: float | None = Field(default=None, alias="polarErrorArcsec")
    polar_alt_error_arcsec: float | None = Field(default=None, alias="polarAltErrorArcsec")
    polar_az_error_arcsec: float | None = Field(default=None, alias="polarAzErrorArcsec")


class IndiStatus(BaseModel):
    """Status of the connected INDI hardware driver layer."""

    model_config = ConfigDict(populate_by_name=True)
    status: str = Field(default="UNKNOWN", alias="status")


class GuidingStats(BaseModel):
    """Statistical RMS errors and tracking SNR for active guiding."""

    model_config = ConfigDict(populate_by_name=True)
    rms_ra: float = Field(default=0.0, alias="rms_ra")
    rms_dec: float = Field(default=0.0, alias="rms_dec")
    rms_total: float = Field(default=0.0, alias="rms_total")
    star_mass: float = Field(default=0.0, alias="star_mass")
    snr: float = Field(default=0.0, alias="snr")


class GuidingStatus(BaseModel):
    """Live feedback of guiding loop state and correction sample history."""

    model_config = ConfigDict(populate_by_name=True)
    is_guiding: bool = Field(default=False, alias="is_guiding")
    stats: GuidingStats = Field(default_factory=GuidingStats, alias="stats")
    history: list[dict[str, Any]] = Field(default_factory=list, alias="history")
    exposure: float = Field(default=1.0, alias="exposure")
    gain: float = Field(default=0.0, alias="gain")


class MountPointingModel(BaseModel):
    """Decomposed geometric mount pointing model terms from plate solves."""

    model_config = ConfigDict(populate_by_name=True)
    sample_count: int = Field(..., alias="sampleCount")
    raw_rms_arcsec: float = Field(..., alias="rawRmsArcsec")
    residual_rms_arcsec: float = Field(..., alias="residualRmsArcsec")
    improvement_percent: float = Field(default=0.0, alias="improvementPercent")
    ih_arcsec: float = Field(default=0.0, alias="ihArcsec")
    id_arcsec: float = Field(default=0.0, alias="idArcsec")
    me_arcsec: float = Field(default=0.0, alias="meArcsec")
    ma_arcsec: float = Field(default=0.0, alias="maArcsec")
    ch_arcsec: float = Field(default=0.0, alias="chArcsec")
    tf_arcsec: float = Field(default=0.0, alias="tfArcsec")
    total_polar_error_arcsec: float = Field(default=0.0, alias="totalPolarErrorArcsec")
    confidence: str = Field(default="high", alias="confidence")
    message: str = Field(default="", alias="message")


class GuidingSpectrumPeak(BaseModel):
    """A dominant harmonic frequency identified in guiding telemetry."""

    model_config = ConfigDict(populate_by_name=True)
    period_seconds: float = Field(..., alias="periodSeconds")
    amplitude_arcsec: float = Field(..., alias="amplitudeArcsec")
    power: float = Field(..., alias="power")
    probable_source: str = Field(default="Unknown", alias="probableSource")


class GuidingSpectrumAnalysis(BaseModel):
    """Periodic error, worm harmonic spectrum, and backlash diagnostics."""

    model_config = ConfigDict(populate_by_name=True)
    sample_count: int = Field(..., alias="sampleCount")
    duration_seconds: float = Field(..., alias="durationSeconds")
    periodic_error_peak_to_peak_arcsec: float = Field(default=0.0, alias="periodicErrorPeakToPeakArcsec")
    dominant_period_seconds: float | None = Field(default=None, alias="dominantPeriodSeconds")
    dec_backlash_estimate_ms: float | None = Field(default=None, alias="decBacklashEstimateMs")
    peaks: list[GuidingSpectrumPeak] = Field(default_factory=list, alias="peaks")
    psd_curve: list[dict[str, float]] = Field(default_factory=list, alias="psdCurve")
    message: str = Field(default="", alias="message")
