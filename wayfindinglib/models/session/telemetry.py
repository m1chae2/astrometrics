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

    @property
    def pointing_error(self) -> float | None:
        """Calculate the total coordinate pointing offset magnitude."""
        if self.delta_ra_arcsec is not None and self.delta_dec_arcsec is not None:
            return math.sqrt(self.delta_ra_arcsec**2 + self.delta_dec_arcsec**2)
        return None


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
