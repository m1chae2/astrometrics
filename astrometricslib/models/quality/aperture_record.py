"""What `QualityRecords.get_aperture_record` returns: the noise record.

The odd–even difference job saves, beside a stack, the sum and the difference
of each empty sky aperture it measured. `OddEvenApertureRecord` holds that
file's content, with one `ApertureNoiseRow` for each aperture.
"""

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ApertureNoiseRow", "OddEvenApertureRecord"]


class ApertureNoiseRow(BaseModel):
    """The values measured in one empty sky aperture.

    Parameters
    ----------
    index : `int`
        The aperture's place in the stored aperture set.
    x, y : `float`
        The aperture center on the stack, in pixels (column and row).
    distance_from_center_px : `float`
        The distance from the image center, in pixels.
    distance_from_target_px : `float` or `None`
        The distance from the target, in pixels. `None` when the target's
        place on the stack is not known.
    sum : `float`
        The sky-subtracted value on the sum image (A + B), in the stack's
        units.
    difference : `float`
        The sky-subtracted value on the difference image (A - B).
    """

    model_config = ConfigDict(extra="allow")

    index: int | None = None
    x: float
    y: float
    distance_from_center_px: float
    distance_from_target_px: float | None = None
    sum: float
    difference: float


class OddEvenApertureRecord(BaseModel):
    """The content of a per-aperture noise record file.

    Parameters
    ----------
    version : `int`
        The layout version of the file.
    stack : `str` or `None`
        The file name of the stack the record belongs to.
    aperture_identity : `str` or `None`
        The identity of the aperture set the values were measured in.
    note : `str` or `None`
        What the values are, in words.
    apertures : `list` [`ApertureNoiseRow`]
        One row for each aperture usable in both images.
    """

    model_config = ConfigDict(extra="allow")

    version: int
    stack: str | None = None
    aperture_identity: str | None = None
    note: str | None = None
    apertures: list[ApertureNoiseRow] = Field(default_factory=list)
