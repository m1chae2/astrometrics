"""Purpose: The star-shape review of one target, for the screen.

Description: `review.targets.get_star_shapes` returns a `StarShapeReview`. It
turns the star-shape measurements that `astrometricslib` stores (how
stretched the stars are, which way, and how wide they are at the center and
in the corners) into short plain verdicts: are the stars round, does the
stretch point one way, which frames the stacker set aside as trailed, and
how the corners compare with the center. Each verdict has one plain sentence
for the screen and the numbers behind it. The review also carries the
per-frame series and their typical elongation, so the screen can draw a
chart.

`astrometricslib` measures; this review only reads what was stored and says
what it suggests. Nothing here is stored.
"""

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "VERDICT_STATES",
    "FrameStarShape",
    "ShapeVerdict",
    "StandoutFrame",
    "StarShapeReview",
]

VERDICT_STATES = (
    "round",
    "elongated",
    "shared",
    "mixed",
    "not_needed",
    "none_found",
    "found",
    "not_validated",
    "no_history",
    "not_measured",
)
"""The states a verdict can have.

``round`` and ``elongated`` belong to the roundness verdict. ``shared`` and
``mixed`` belong to the direction verdict. ``none_found`` and ``found``
belong to the standout verdict. ``not_validated`` belongs to the corner
numbers. ``not_needed`` says a verdict does not apply (for example, the
direction of round stars). ``no_history`` says the equipment has too few
earlier nights to say what is normal. ``not_measured`` says the stored
measurements are missing.
"""


class FrameStarShape(BaseModel):
    """The stored star-shape numbers of one frame (or of one stack).

    Parameters
    ----------
    path : `str`
        The frame's path, or the stack's subject id.
    timestamp : `float` or `None`
        When the frame was taken, in seconds since the Unix epoch. `None`
        for a stack.
    elongation : `float` or `None`
        Long axis divided by short axis (1 is round).
    angle_degrees : `float` or `None`
        Direction of the long axis, from 0 up to but not including 180
        degrees.
    direction_coherence : `float` or `None`
        From 0 (the stars point every which way) to 1 (they all point the
        same way), from the stored coherence measurement.
    center_width_px : `float` or `None`
        Median star width at the center of the field, in pixels.
    corner_widths_px : `dict` [`str`, `float`]
        Median star width in each corner, in pixels, by corner name.
    elongation_row_id : `int` or `None`
        The id of the stored elongation row, so a statement can cite it.
    standout : `bool`
        Whether the stacker set this frame aside as trailed.
    """

    model_config = ConfigDict(populate_by_name=True)

    path: str
    timestamp: float | None = None
    elongation: float | None = None
    angle_degrees: float | None = Field(default=None, alias="angleDegrees")
    direction_coherence: float | None = Field(default=None, alias="directionCoherence")
    center_width_px: float | None = Field(default=None, alias="centerWidthPx")
    corner_widths_px: dict[str, float] = Field(default_factory=dict, alias="cornerWidthsPx")
    elongation_row_id: int | None = Field(default=None, alias="elongationRowId")
    standout: bool = False


class StandoutFrame(BaseModel):
    """A frame the stacker's quarantine set aside for stretched stars.

    The review lists it as the quarantine decided; it does not judge the
    frame again.

    Parameters
    ----------
    path : `str`
        The frame's path before it was set aside (its path now when that is
        not known).
    timestamp : `float` or `None`
        When the frame was taken, in seconds since the Unix epoch.
    elongation : `float` or `None`
        The frame's stored elongation, when there is one.
    reason : `str`
        The quarantine's sentence saying what it measured.
    """

    model_config = ConfigDict(populate_by_name=True)

    path: str
    timestamp: float | None = None
    elongation: float | None = None
    reason: str = ""


class ShapeVerdict(BaseModel):
    """One plain verdict with the numbers behind it.

    Parameters
    ----------
    state : `str`
        The verdict's state, one of `VERDICT_STATES`.
    sentence : `str`
        One short sentence in plain English, for the screen.
    numbers : `dict` [`str`, `float` or `None`]
        The numbers the sentence rests on, by name.
    """

    state: str
    sentence: str
    numbers: dict[str, float | None] = Field(default_factory=dict)


class StarShapeReview(BaseModel):
    """What the stored star shapes say about one target.

    Parameters
    ----------
    target_id : `str`
        The target.
    scope : `str`
        What was judged: ``"stack"`` (the stack's own numbers, when it has
        them), ``"frames"`` (the median of the frames), ``"night"`` or
        ``"frame_list"``.
    frame_count : `int`
        Frames with a stored elongation.
    typical_elongation : `float` or `None`
        The median elongation of those frames, the session's typical value.
        `None` when no frame has one.
    roundness : `ShapeVerdict`
        Round or elongated: the frames against the equipment's own history,
        a stack against the library's other stacks.
    direction : `ShapeVerdict`
        Whether the stretch points one way, and what that suggests.
    standout : `ShapeVerdict`
        Whether the stacker set frames aside as trailed, with
        ``standout_frames`` listing them.
    corners : `ShapeVerdict`
        The corner and center widths. Always reported as not validated.
    standout_frames : `list` [`StandoutFrame`]
        The frames set aside as trailed, oldest first.
    series : `list` [`FrameStarShape`]
        Every frame with a stored elongation, oldest first, for a chart.
    notes : `list` [`str`]
        Anything that limited the review, such as a missing history.
    """

    model_config = ConfigDict(populate_by_name=True)

    target_id: str = Field(alias="targetId")
    scope: str
    frame_count: int = Field(alias="frameCount")
    typical_elongation: float | None = Field(default=None, alias="typicalElongation")
    roundness: ShapeVerdict
    direction: ShapeVerdict
    standout: ShapeVerdict
    corners: ShapeVerdict
    standout_frames: list[StandoutFrame] = Field(default_factory=list, alias="standoutFrames")
    series: list[FrameStarShape] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
