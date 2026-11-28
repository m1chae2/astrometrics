"""Where pixels of one image lie on another image.

`PositionMapping` is one pixel followed by the plate solutions and the stack
records. `MappedPositions` is what `quality.frames.map_positions` returns: many
positions followed at once, by those records or by matching the stars of two
frames, with the method used.

Every number is a plain Python float, so it can be sent to the app as it is.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["MappedPosition", "MappedPositions", "PositionMapping"]


class PositionMapping(BaseModel):
    """Where a pixel of one image lies on another, and how that was found.

    Parameters
    ----------
    x, y : `float` or `None`
        The position on the second image, in 1-based pixels (as in FITS and
        DS9): the center of the first pixel is (1, 1). `None` when the pixel
        cannot be followed (``method`` is ``"none"``).
    method : `str`
        ``"sky"`` (both images are plate solved), ``"stack alignment"`` (a
        stack's registration record links them), ``"stack alignment + sky"``
        (frames of two stacks, through the stacks' plate solutions),
        ``"star matching"`` (the stars of two frames were matched),
        ``"same pixel"`` (the same image, or a guess when nothing links the
        two) or ``"none"`` (the pixel cannot be followed).
    note : `str`
        A sentence about the result. For a guess it says so; for ``"none"``
        it says why.
    """

    x: float | None
    y: float | None
    method: Literal["sky", "stack alignment", "stack alignment + sky", "star matching", "same pixel", "none"]
    note: str


class MappedPosition(BaseModel):
    """One position on the first image and where it lies on the second.

    Parameters
    ----------
    x, y : `float`
        The position on the first image, in 1-based pixels.
    mapped_x, mapped_y : `float` or `None`
        The position on the second image, in 1-based pixels. `None` when it
        cannot be followed.
    inside : `bool`
        `True` when the mapped position lies inside the second image.
    """

    model_config = ConfigDict(frozen=True)

    x: float
    y: float
    mapped_x: float | None = None
    mapped_y: float | None = None
    inside: bool = False


class MappedPositions(BaseModel):
    """Where positions of one image lie on another, and how that was found.

    Parameters
    ----------
    method : `str`
        ``"sky"`` (both images are plate solved), ``"stack alignment"`` (a
        stack's registration record links them), ``"stack alignment +
        sky"`` (frames of two stacks, through the stacks' plate
        solutions), ``"star matching"`` (the bright stars of two frames of
        one size were matched), ``"same pixel"`` (the same image, or a guess
        when nothing links the two) or ``"none"`` (the positions cannot be
        followed).
    note : `str`
        A sentence about the result. For a guess it says so; for ``"none"``
        it says why.
    points : `list` [`MappedPosition`]
        One entry for each position asked, in the order asked.
    """

    model_config = ConfigDict(frozen=True)

    method: Literal["sky", "stack alignment", "stack alignment + sky", "star matching", "same pixel", "none"]
    note: str = ""
    points: list[MappedPosition] = Field(default_factory=list)
