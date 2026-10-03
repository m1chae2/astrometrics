"""Data structures that describe frames the stacking pipeline set aside.

Before a stack, the pipeline moves light frames with clouds or trailed
stars into an `_excluded` folder next to them. These structures describe
those frames, so a person or a tool can see what was moved and why, and put
frames back:

- `SetAsideFrame` describes one frame, moved or about to be moved.
- `QuarantinePreview` says what the check would move, without moving it.
- `RestoreReport` says what a restore listed or moved back.
"""

from pydantic import BaseModel, ConfigDict, Field


class SetAsideFrame(BaseModel):
    """One light frame the quarantine step moved, or would move.

    The numbers are the measurements the decision rests on. They come from
    the same check that runs during an observing session.
    """

    model_config = ConfigDict(populate_by_name=True)

    # The frame's file name.
    file: str
    # Where the frame lies (or lay) in the target's folder.
    original_path: str = Field(alias="originalPath")
    # "clouded" (few stars) or "trailed" (stars smeared into streaks).
    kind: str
    # One sentence saying what was measured.
    reason: str
    # Bright regions found in this frame, and the typical count of its batch.
    star_count: int = Field(alias="starCount")
    typical_star_count: float = Field(alias="typicalStarCount")
    # Median star roundness of this frame, from 0 to 1 (1 is round). `None`
    # if no stars were round enough to measure.
    roundness: float | None = None
    # Median pixel value of this frame, in camera counts (ADU).
    sky_median_adu: float = Field(alias="skyMedianAdu")
    # When the frame was moved (UTC, ISO 8601). `None` for a preview.
    moved_at: str | None = Field(default=None, alias="movedAt")
    # The `_excluded` folder the frame is in now. `None` for a preview.
    folder: str | None = None


class QuarantinePreview(BaseModel):
    """What the quarantine check would move for a target. Nothing is moved."""

    model_config = ConfigDict(populate_by_name=True)

    target_id: str = Field(alias="targetId")
    # Light frames the check measured.
    frames_checked: int = Field(alias="framesChecked")
    # The frames it would move.
    would_move: list[SetAsideFrame] = Field(default_factory=list, alias="wouldMove")
    # One sentence for each batch it would leave alone, with the reason.
    notes: list[str] = Field(default_factory=list)
    # Frames that could not be measured. They would stay where they are.
    unreadable: list[str] = Field(default_factory=list)


class RestoreReport(BaseModel):
    """What a restore listed, or moved back, for a target."""

    model_config = ConfigDict(populate_by_name=True)

    target_id: str = Field(alias="targetId")
    # False: the frames are only listed and nothing moved.
    applied: bool
    # The frames set aside for this target (before any restore).
    frames: list[SetAsideFrame] = Field(default_factory=list)
    # How many of them were moved back. Zero unless `applied`.
    restored_count: int = Field(default=0, alias="restoredCount")
