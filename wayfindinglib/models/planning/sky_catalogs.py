"""Purpose: The sky data the Planetarium reads that is not a source list.

Description: `ObservationPlanning.query_catalogs` returns a list of
`CatalogDriverInfo`, one for each online catalog driver. The Planetarium
shows them in its catalog settings.
`ObservationPlanning.get_constellation_lines` returns a list of
`ConstellationLineSegment`, the straight pieces of the stick figures it draws
over the stars. The fields keep the names the app already reads.
"""

from pydantic import BaseModel, Field


class CatalogDriverInfo(BaseModel):
    """What the app needs to know about one online catalog driver."""

    driver_name: str = Field(
        ..., description="The name the driver is registered under, such as 'deep_stars'."
    )
    display_name: str = Field(..., description="The name to show a person.")
    maximum_query_radius_degrees: float = Field(
        ..., description="The widest search circle the driver answers, in degrees."
    )


class ConstellationLineSegment(BaseModel):
    """One straight piece of a constellation's stick figure."""

    constellation: str = Field(
        ..., description="The constellation's three-letter abbreviation, such as 'Ori'."
    )
    ra1: float = Field(..., description="Right ascension of the first end, in degrees.")
    dec1: float = Field(..., description="Declination of the first end, in degrees.")
    ra2: float = Field(..., description="Right ascension of the second end, in degrees.")
    dec2: float = Field(..., description="Declination of the second end, in degrees.")
