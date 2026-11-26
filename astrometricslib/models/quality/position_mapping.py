"""What `QualityRecords.map_position` returns: a pixel followed elsewhere.

Every number is a plain Python float, so it can be sent to the app as it is.
"""

from typing import Literal

from pydantic import BaseModel

__all__ = ["PositionMapping"]


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
        ``"same pixel"`` (the same image, or a guess when nothing links the
        two) or ``"none"`` (the pixel cannot be followed).
    note : `str`
        A sentence about the result. For a guess it says so; for ``"none"``
        it says why.
    """

    x: float | None
    y: float | None
    method: Literal["sky", "stack alignment", "stack alignment + sky", "same pixel", "none"]
    note: str
