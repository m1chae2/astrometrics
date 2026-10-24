"""Data structures that describe frames the stacking pipeline set aside.

Before a stack, the pipeline moves light frames with clouds or trailed
stars into an `_excluded` folder next to them. These structures describe
those frames, so a person or a tool can see what was moved and why, and put
frames back:

- `SetAsideFrame` describes one frame, moved or about to be moved.
- `QuarantinePreview` says what the check would move, without moving it.
  `QualityRecords.measure_frames(kind="quarantine_preview")` returns it.
- `RestoreReport` says what a restore listed or moved back.
- `SetAsideFrameRecord` is one line of `QualityRecords.set_aside_frames`: a
  frame lying in an `_excluded` folder, with the reason and where the reason
  came from.
"""

from typing import Literal

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

    kind: Literal["quarantine_preview"] = "quarantine_preview"
    target_id: str = Field(alias="targetId")
    # Light frames the check measured.
    frames_checked: int = Field(alias="framesChecked")
    # How many frames it would move. ``would_move`` may list fewer.
    would_move_total: int = Field(default=0, alias="wouldMoveTotal")
    # The frames it would move, up to the limit the caller gave.
    would_move: list[SetAsideFrame] = Field(default_factory=list, alias="wouldMove")
    # One sentence for each batch it would leave alone, with the reason.
    notes: list[str] = Field(default_factory=list)
    # Frames that could not be measured. They would stay where they are.
    unreadable: list[str] = Field(default_factory=list)
    # The frames the stacker has already set aside, when asked for.
    excluded: list[SetAsideFrame] | None = None


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


class SetAsideFrameRecord(BaseModel):
    """One frame that lies in a target's `_excluded` folder.

    The listing is read-only. Where the reason is known, it comes from the
    first source that has it: the stored quality rows of the frame (which
    carry the action taken), then the folder's `excluded_frames.json` file.
    """

    model_config = ConfigDict(populate_by_name=True)

    # The frame's file name in the `_excluded` folder.
    file: str
    # The frame's path now.
    path: str
    # The id the frame's quality rows use: the target, then the frame's place
    # in the library before it was moved. `None` when the original place is
    # unknown (no manifest entry), in which case no stored row can be matched.
    subject_id: str | None = Field(default=None, alias="subjectId")
    # Where the frame lay before it was moved, when known.
    original_path: str | None = Field(default=None, alias="originalPath")
    # "clouded", "trailed" or `None` when no source names a kind.
    kind: str | None = None
    # One sentence saying what was measured. Empty when no source has it.
    reason: str = ""
    # Where the kind and reason came from: "stored_row" (a quality row with
    # the action taken), "manifest" (only the folder's `excluded_frames.json`)
    # or "folder_only" (the frame lies in the folder and nothing records why).
    reason_source: Literal["stored_row", "manifest", "folder_only"] = Field(alias="reasonSource")
    # The `action_taken` text of the stored row, when there is one.
    action_taken: str | None = Field(default=None, alias="actionTaken")
    # Exposure of one frame in seconds: from the stored capture setting, else
    # from the file header. `None` if neither could be read.
    exposure_seconds: float | None = Field(default=None, alias="exposureSeconds")
    # The capture setting row of the frame's stored rows, when there is one.
    capture_setting_id: int | None = Field(default=None, alias="captureSettingId")
    # When the frame was moved (UTC, ISO 8601), from the manifest; else the
    # time of the stored row.
    moved_at: str | None = Field(default=None, alias="movedAt")
