"""Purpose: The result model of `ObservationReview.query_alignment_records`.

Description: The stored alignment records of the mount, as the app's
alignment views show them: the plate-solve attempts, the same attempts
grouped into one session per target, and the latest polar alignment run.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from wayfindinglib.models.session.telemetry import (
    AlignmentAttempt,
    AlignmentTargetSession,
    PolarAlignmentStatus,
)


class AlignmentRecords(BaseModel):
    """Stored alignment attempts, their grouping by target, and a polar run.

    Returned by `ObservationReview.query_alignment_records`.

    Attributes
    ----------
    attempts : `list` [`AlignmentAttempt`]
        The attempts, oldest first. For one night this includes a tracking
        row for each target imaged that night.
    targets : `list` [`AlignmentTargetSession`]
        The same attempts grouped into one session per target, with jitter
        and drift rates.
    polar_alignment : `PolarAlignmentStatus` or `None`
        The latest polar alignment run of the night asked for (of any
        night for ``"all"``). `None` when no night was asked for, or no
        run is stored.
    """

    model_config = ConfigDict(populate_by_name=True)
    attempts: list[AlignmentAttempt] = Field(default_factory=list, alias="attempts")
    targets: list[AlignmentTargetSession] = Field(default_factory=list, alias="targets")
    polar_alignment: PolarAlignmentStatus | None = Field(default=None, alias="polarAlignment")
