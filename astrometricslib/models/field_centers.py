"""What `TargetCatalog.imaged_field_centers` returns: the places imaged.

- `FieldCenter` is one distinct sky position and the targets that share it.
- `ImagedFieldCenters` is the whole list.
"""

from pydantic import BaseModel, Field

__all__ = ["FieldCenter", "ImagedFieldCenters"]


class FieldCenter(BaseModel):
    """One distinct sky position the library has imaged.

    Parameters
    ----------
    right_ascension_deg : `float`
        Right ascension of the position, in degrees.
    declination_deg : `float`
        Declination of the position, in degrees.
    target_ids : `list` [`str`]
        The targets that were imaged at this position.
    frames_examined : `int`
        How many frames' headers gave this position.
    """

    right_ascension_deg: float
    declination_deg: float
    target_ids: list[str] = Field(default_factory=list)
    frames_examined: int = 0


class ImagedFieldCenters(BaseModel):
    """The distinct sky positions the library has imaged.

    Parameters
    ----------
    centers : `list` [`FieldCenter`]
        One entry per distinct position.
    """

    centers: list[FieldCenter] = Field(default_factory=list)
