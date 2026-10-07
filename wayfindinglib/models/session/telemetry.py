"""Purpose: Telemetry Domain Models.

Description: Guiding samples, alignment attempts, and live guiding
status (`Wayfinding_Library_Architecture.md` §2.4.7). Once `AUTOGUIDING`
reaches `AUTHORITATIVE`, `GuidingSample` records are produced by this
library's own guiding correction computation rather than parsed from
the incumbent guider, with identical fields, so downstream consumers
are unaffected by the transition.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GuidingSampleSource(StrEnum):
    """Where a stored guiding sample came from.

    The difference matters because only some sources are measurements of
    the real star. `INDI_PULSE_ESTIMATE` samples are reconstructed from
    the guide pulses the mount received: their drift values are a model
    of what those pulses imply, not something a camera saw, so they must
    never be treated as measurements.
    """

    PHD2_GUIDE_LOG = "phd2_guide_log"
    PHD2_LIVE = "phd2_live"
    EKOS_GUIDE_LOG = "ekos_guide_log"
    EKOS_ANALYZE_LOG = "ekos_analyze_log"
    INDI_PULSE_ESTIMATE = "indi_pulse_estimate"
    UNVERIFIED = "unverified"


MEASURED_GUIDING_SAMPLE_SOURCES: tuple[GuidingSampleSource, ...] = (
    GuidingSampleSource.PHD2_GUIDE_LOG,
    GuidingSampleSource.PHD2_LIVE,
    GuidingSampleSource.EKOS_GUIDE_LOG,
    GuidingSampleSource.EKOS_ANALYZE_LOG,
)
"""The sources whose drift values were measured from a real guide star."""


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
    star_mass: float | None = Field(None, alias="starMass")

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
    session_id: str | None = Field(default=None, alias="sessionId")

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
    mean_ra_deg: float | None = Field(
        default=None,
        alias="meanRaDeg",
        description="Average right ascension of the night's solves, wrapped at 0/360 degrees.",
    )
    mean_dec_deg: float | None = Field(
        default=None, alias="meanDecDeg", description="Average declination of the night's solves."
    )
    rms_jitter_arcsec: float | None = Field(
        default=None,
        alias="rmsJitterArcsec",
        description="Tracking jitter over the night's targets, weighted by their solve counts.",
    )


class AlignmentTrackPoint(BaseModel):
    """One plate solve in a target's run, timed from the run's first solve."""

    model_config = ConfigDict(populate_by_name=True)
    elapsed_seconds: float = Field(..., alias="elapsedSeconds")
    delta_ra_arcsec: float = Field(..., alias="deltaRaArcsec")
    delta_dec_arcsec: float = Field(..., alias="deltaDecArcsec")
    total_error_arcsec: float = Field(..., alias="totalErrorArcsec")
    timestamp: float = Field(..., alias="timestamp")


class AlignmentTargetSession(BaseModel):
    """The plate solves on one target, with their tracking statistics.

    Solves are grouped by target name, or, without a name, by being within
    half a degree of each other. A group can hold several runs: a gap of
    more than two hours starts a new run.

    Jitter is the root-mean-square (RMS) scatter of the solves around the
    run's own average offset, so a deliberate framing offset is not counted
    as tracking error. Drift is the slope of a straight-line fit of the
    offsets against time.
    """

    model_config = ConfigDict(populate_by_name=True)
    id: str = Field(..., alias="id")
    target_name: str = Field(..., alias="targetName")
    mean_ra_deg: float = Field(
        ..., alias="meanRaDeg", description="Average right ascension, wrapped at 0/360 degrees."
    )
    mean_dec_deg: float = Field(..., alias="meanDecDeg")
    frame_count: int = Field(..., alias="frameCount")
    initial_error_arcsec: float = Field(
        ..., alias="initialErrorArcsec", description="Pointing error of the first solve, after the slew."
    )
    rms_ra_arcsec: float = Field(..., alias="rmsRaArcsec")
    rms_dec_arcsec: float = Field(..., alias="rmsDecArcsec")
    rms_total_arcsec: float = Field(
        ..., alias="rmsTotalArcsec", description="Tracking jitter: RMS scatter around each run's mean offset."
    )
    drift_ra_arcsec_per_min: float = Field(..., alias="driftRaArcsecPerMin")
    drift_dec_arcsec_per_min: float = Field(..., alias="driftDecArcsecPerMin")
    start_time: float | None = Field(default=None, alias="startTime")
    end_time: float | None = Field(default=None, alias="endTime")
    elapsed_seconds: float = Field(
        ..., alias="elapsedSeconds", description="Time spent tracking, summed over runs (gaps excluded)."
    )
    time_series: list[AlignmentTrackPoint] = Field(default_factory=list, alias="timeSeries")
    attempts: list[AlignmentAttempt] = Field(default_factory=list, alias="attempts")

    @classmethod
    def from_attempts(cls, attempts: list[AlignmentAttempt]) -> list[AlignmentTargetSession]:
        """Group plate solves into one session per target.

        Parameters
        ----------
        attempts : `list` [`AlignmentAttempt`]
            Solves in any order. Ones without a position are skipped, and
            repeats of the same time and target are counted once.

        Returns
        -------
        sessions : `list` [`AlignmentTargetSession`]
            One per target, in the order the targets were first solved.
        """
        from wayfindinglib.analytics.alignment_sessions import group_alignment_attempts

        return group_alignment_attempts(attempts)


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


class LiveGuidingStatus(BaseModel):
    """The guiding now: whether it runs, its RMS and its newest samples.

    RMS (root-mean-square) error is the usual measure of guiding accuracy,
    in arcseconds.
    """

    model_config = ConfigDict(populate_by_name=True)
    is_guiding: bool = Field(default=False, alias="is_guiding")
    stats: GuidingStats = Field(default_factory=GuidingStats, alias="stats")
    history: list[GuidingSample] = Field(default_factory=list, alias="history")
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
    """Periodic error, worm harmonic spectrum, and backlash diagnostics.

    `id`/`telescope_id`/`schema_version` support this model's other use
    (M7a): as standing, cross-night mount-mechanical Foundation state
    persisted via `DiskButler`, refit cumulatively each time
    `guiding_log_ingestion.py` processes a new guide log, the same
    status as `GuiderCalibration`/`FocusModel`
    (`Wayfinding_Library_Architecture.md` §2.5.1a's §6a extension). All
    three default so existing ad hoc, non-persisted analysis results
    (e.g. `GuidingService.analyze_guiding_spectrum`'s live RPC response)
    are unaffected.
    """

    model_config = ConfigDict(populate_by_name=True)
    id: str = Field(default="", alias="id")
    telescope_id: str = Field(default="", alias="telescopeId")
    schema_version: int = Field(default=1, alias="schemaVersion")
    sample_count: int = Field(..., alias="sampleCount")
    duration_seconds: float = Field(..., alias="durationSeconds")
    periodic_error_peak_to_peak_arcsec: float = Field(default=0.0, alias="periodicErrorPeakToPeakArcsec")
    dominant_period_seconds: float | None = Field(default=None, alias="dominantPeriodSeconds")
    dec_backlash_estimate_ms: float | None = Field(default=None, alias="decBacklashEstimateMs")
    peaks: list[GuidingSpectrumPeak] = Field(default_factory=list, alias="peaks")
    psd_curve: list[dict[str, float]] = Field(default_factory=list, alias="psdCurve")
    message: str = Field(default="", alias="message")
