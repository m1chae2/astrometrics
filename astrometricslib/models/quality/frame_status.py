"""Purpose: The status of each frame of a target in its stack.

Description: The models `TargetFrames.get_statuses` returns, one entry
per frame, for the File Browser's Status column.
"""

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["FRAME_STATES", "FrameStatus", "FrameStatusReport"]

FRAME_STATES = ("included", "flagged", "not_stacked", "set_aside")
"""The states a frame can be in."""


class FrameStatus(BaseModel):
    """The status of one frame, with the reason when there is one.

    Attributes
    ----------
    path : `str`
        The frame's file. For a set-aside frame, its place in `_excluded`.
    name : `str`
        The file name.
    state : `str`
        One of `FRAME_STATES`.
    kind : `str` or `None`
        The kind the stacker recorded for a set-aside frame, such as
        ``clouded``, ``trailed`` or ``off_field``. `None` when none is
        recorded.
    reason : `str`
        The stacker's own words for a set-aside frame, or why a flagged
        frame is flagged (its elongation, the warning level and the set-aside
        threshold); empty otherwise.
    date : `str`
        When the frame was taken, as ``YYYY-MM-DD HH:MM:SS`` (set-aside
        frames only).
    camera : `str`
        The camera name from the frame's header (set-aside frames only).
    filter : `str`
        The filter name from the frame's header (set-aside frames only).
    exposure : `str`
        The exposure in seconds, for a set-aside frame; empty otherwise.
    elongation : `float` or `None`
        For a flagged frame, the elongation of its stars (long axis over
        short axis); `None` otherwise.
    original_path : `str` or `None`
        Where a set-aside frame was before it was moved.
    restorable : `bool`
        Whether the app can put a set-aside frame back.
    restore_note : `str`
        Why a set-aside frame cannot be put back, when it cannot.
    """

    model_config = ConfigDict(populate_by_name=True)

    path: str
    name: str
    state: str
    kind: str | None = None
    reason: str = ""
    date: str = ""
    camera: str = ""
    filter: str = ""
    exposure: str = ""
    elongation: float | None = None
    original_path: str | None = Field(default=None, alias="originalPath")
    restorable: bool = False
    restore_note: str = Field(default="", alias="restoreNote")


class FrameStatusReport(BaseModel):
    """The statuses of every frame of a target.

    Attributes
    ----------
    frames : `list` [`FrameStatus`]
        One entry per frame in the target, then one per set-aside frame.
    stacked_at : `float` or `None`
        When the latest stack file was written, in seconds since the Unix
        epoch. `None` when the target has no stack.
    """

    model_config = ConfigDict(populate_by_name=True)

    frames: list[FrameStatus] = Field(default_factory=list)
    stacked_at: float | None = Field(default=None, alias="stackedAt")
