"""Purpose: How much of the deep-star catalog is installed, and its full size.

Description: The Planetarium draws faint stars from a local copy of the
Gaia DR3 catalog, downloaded in chunks of sky. `DeepCatalogStatus` says
how much of that copy is on disk. When asked, it also carries a
`DeepCatalogEstimate`: a guess at the finished size, made by counting the
stars in a sample of chunks.
"""

from pydantic import BaseModel, ConfigDict, Field


class DeepCatalogEstimate(BaseModel):
    """A guess at the finished catalog's size, from a sample of chunks."""

    model_config = ConfigDict(populate_by_name=True)

    pixels_total: int = Field(..., description="How many chunks the whole sky is cut into.")
    pixels_sampled: int = Field(..., description="How many chunks were counted.")
    counts: list[int] = Field(default_factory=list, description="The star count of each sampled chunk.")
    sample_min: int = Field(..., description="Fewest stars in one sampled chunk.")
    sample_max: int = Field(..., description="Most stars in one sampled chunk.")
    sample_mean: float = Field(..., description="Average stars per sampled chunk.")
    estimated_stars: float = Field(..., description="Estimated stars in the whole catalog.")
    estimated_megabytes: float = Field(..., description="Estimated size on disk, in megabytes.")


class DeepCatalogStatus(BaseModel):
    """How much of the downloaded deep-star catalog is installed."""

    model_config = ConfigDict(populate_by_name=True)

    installed: bool = Field(..., description="True once any chunk has been saved.")
    complete: bool = Field(..., description="True once every chunk has been saved.")
    star_count: int = Field(..., description="Stars saved so far.")
    pixels_downloaded: int = Field(..., description="Chunks saved so far.")
    pixels_total: int | None = Field(default=None, description="Chunks in the planned download.")
    healpix_level: int | None = Field(
        default=None, description="How finely the planned download cuts the sky."
    )
    magnitude_limit: float | None = Field(default=None, description="Faintest Gaia G magnitude downloaded.")
    size_megabytes: float = Field(..., description="Size of the catalog file, in megabytes.")
    estimate: DeepCatalogEstimate | None = Field(
        default=None, description='The size estimate. Filled with include=["estimate"].'
    )
