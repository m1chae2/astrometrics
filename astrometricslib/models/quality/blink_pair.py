"""What the blink check returns: a frame, a typical frame and one star in both.

A person who looks at a flagged or set-aside frame wants to see what is wrong
with it. The easiest way is to flip between that frame and a normal frame of
the same session, zoomed in on the same star. These structures hold the
answer: which normal frame to use and where the star is in each frame.

Every position is in 1-based pixels (as in FITS and DS9): the center of the
first pixel is (1, 1). Every number is a plain Python float, int, bool or str,
so it can be sent to the app as it is.
"""

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["BlinkPair", "FramePositionMatch", "MatchedPoint"]


class MatchedPoint(BaseModel):
    """One position in a frame and the same sky position in another frame.

    Parameters
    ----------
    x, y : `float`
        The position in the first frame, in 1-based pixels.
    mapped_x, mapped_y : `float`
        Where the same sky position lies in the second frame, in 1-based
        pixels. It may lie outside the second frame.
    inside : `bool`
        `True` when the mapped position lies inside the second frame.
    """

    model_config = ConfigDict(populate_by_name=True)

    x: float
    y: float
    mapped_x: float = Field(alias="mappedX")
    mapped_y: float = Field(alias="mappedY")
    inside: bool


class FramePositionMatch(BaseModel):
    """How the stars of one frame line up with those of another.

    Parameters
    ----------
    matched : `bool`
        `True` when enough star pairs agreed to tell how the frames line up.
    turned : `bool`
        `True` when the second frame is turned half a revolution from the
        first (a meridian flip).
    votes : `int`
        How many star pairs agreed.
    points : `list` [`MatchedPoint`]
        The positions that were asked for, mapped into the second frame. Empty
        when ``matched`` is `False`.
    reason : `str`
        Why the frames could not be matched, or an empty string.
    """

    model_config = ConfigDict(populate_by_name=True)

    matched: bool
    turned: bool = False
    votes: int = 0
    points: list[MatchedPoint] = Field(default_factory=list)
    reason: str = ""


class BlinkPair(BaseModel):
    """A frame, a typical frame of its session and one star in both.

    Parameters
    ----------
    found : `bool`
        `True` when a typical frame and a reference star were found.
    reason : `str`
        Why nothing was found, or an empty string.
    path : `str`
        The frame that was asked about.
    typical_path : `str` or `None`
        The frame of the same session whose stored star elongation and sky
        unevenness lie closest to the session's medians.
    star_x, star_y : `float` or `None`
        The reference star in ``path``, in 1-based pixels.
    typical_star_x, typical_star_y : `float` or `None`
        The same star in ``typical_path``, in 1-based pixels.
    turned : `bool`
        `True` when ``typical_path`` is turned half a revolution from
        ``path``.
    votes : `int`
        How many star pairs agreed when the two frames were lined up.
    session_frame_count : `int`
        How many frames of the session could be a typical frame.
    """

    model_config = ConfigDict(populate_by_name=True)

    found: bool
    reason: str = ""
    path: str
    typical_path: str | None = Field(default=None, alias="typicalPath")
    star_x: float | None = Field(default=None, alias="starX")
    star_y: float | None = Field(default=None, alias="starY")
    typical_star_x: float | None = Field(default=None, alias="typicalStarX")
    typical_star_y: float | None = Field(default=None, alias="typicalStarY")
    turned: bool = False
    votes: int = 0
    session_frame_count: int = Field(default=0, alias="sessionFrameCount")
