"""Purpose: The answer to a name lookup: where a named object is in the sky.

Description: `ObservationPlanning.lookup_coordinates` returns one
`CoordinateLookup`. It holds the object's id and name, whether the library
knows it as a target or a star, and its position in degrees. A target also
carries its position as the text saved on the target. A star also carries its
magnitude and spectral type when the catalog gives them.
"""

from typing import Literal

from pydantic import BaseModel, Field


class CoordinateLookup(BaseModel):
    """Where a named object is, and what kind of object it is."""

    id: str = Field(..., description="The target id or the star id.")
    name: str | None = Field(default=None, description="The common name, or None when there is none.")
    kind: Literal["target", "star"] = Field(..., description="Whether the library holds a target or a star.")
    ra_deg: float = Field(..., description="Right ascension, in degrees.")
    dec_deg: float = Field(..., description="Declination, in degrees.")
    ra: str | None = Field(default=None, description="A target's right ascension as saved. None for a star.")
    dec: str | None = Field(default=None, description="A target's declination as saved. None for a star.")
    magnitude: float | None = Field(default=None, description="A star's magnitude. None when unknown.")
    spectral_type: str | None = Field(default=None, description="A star's spectral type. None when unknown.")
