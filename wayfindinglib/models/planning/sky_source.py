"""Purpose: One object to draw on the sky map: a star or a target.

Description: `ObservationPlanning.get_sources` and
`ObservationPlanning.get_online_catalog_sources` return a list of
`SkySource`. Each one holds the position in degrees, a name, the brightness
when known, and what data the library holds for it. The field names turn into
the camelCase keys the app's Planetarium reads. `has_catalog_magnitude` tells
the map whether the brightness is a real catalog magnitude it can draw by.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from astrometricslib import has_catalog_magnitude


class SkySource(BaseModel):
    """A star or target placed on the sky, ready for the Planetarium."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(..., description="The star's or target's id.")
    ra: float = Field(..., description="Right ascension, in degrees.")
    dec: float = Field(..., description="Declination, in degrees.")
    name: str = Field(..., description="The name to show. The id when there is no other name.")
    common_name: str = Field(..., alias="commonName", description="The common name, or the id.")
    spectral_type: str | None = Field(
        default=None, alias="spectralType", description="A star's catalog spectral type. None for a target."
    )
    magnitude: float | None = Field(default=None, description="A star's magnitude. None when unknown.")
    has_spectra: bool = Field(
        default=False, alias="hasSpectra", description="True when the library holds a spectrum for it."
    )
    has_photometry: bool = Field(
        default=False,
        alias="hasPhotometry",
        description="A star: true when it has a light curve. A target: true when it has a stacked or "
        "processed image.",
    )
    type: Literal["star", "target"] = Field(..., description="Whether this is a star or a target.")
    is_global: bool = Field(
        default=False,
        alias="global",
        description="True when it came from an online catalog, not from the user's own library.",
    )
    catalog_source: str | None = Field(
        default=None,
        alias="catalogSource",
        description="The online catalog driver that found it, such as 'deep_stars'. None for the library.",
    )
    stacked_image: str | None = Field(
        default=None,
        alias="stackedImage",
        description="A target's stacked image, or its longest light frame when it has no stack.",
    )
    field_of_view: str | None = Field(
        default=None, alias="fieldOfView", description="A target's field of view, as saved on the target."
    )

    @computed_field(alias="hasCatalogMagnitude")
    @property
    def has_catalog_magnitude(self) -> bool:
        """Check if the magnitude is a real catalog magnitude.

        `False` for a missing magnitude, an instrument reading from
        photometry, or the 0 saved when a catalog gave none (see
        astrometricslib's `has_catalog_magnitude`).
        """
        return has_catalog_magnitude(self.magnitude)
