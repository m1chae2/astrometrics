"""Data structures for the answers of the catalog lookups.

The target and star catalogs each have one general lookup, `query`, that
reads and never changes anything. These structures are what those lookups
return:

- `TargetCameraCounts` from `TargetCatalog.count_by_camera`: the frames each
  camera took, and each target's frames per configured camera.
- `TargetNightCounts` from `TargetCatalog.count_by_night`: how many targets
  have frames from each observing night.
- `TargetStarCount` from `StellarCatalog.count_by_target`: one target's
  star count and data flags. `OverlayStar` is one star drawn over a
  target's image (`Visualization.get_star_overlay`).
- `ReindexReport` from `TargetFrames.scan`, which does change data: how many
  frames each target gained or lost.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "OverlayStar",
    "ReindexReport",
    "TargetCameraCounts",
    "TargetNightCounts",
    "TargetReindexChange",
    "TargetStarCount",
]


class TargetCameraCounts(BaseModel):
    """The answer of `TargetCatalog.count_by_camera`.

    Parameters
    ----------
    frames_by_camera : `dict` [`str`, `int`]
        Each camera name, as the frame headers spell it, and how many light
        frames it took.
    camera_index : `dict` [`str`, `Any`]
        Each configured camera with how many targets it imaged, and per
        target the newest light-frame time and the light frames of each
        configured camera (see
        `astrometricslib.pipelines.shared.target_camera_index`).
    """

    model_config = ConfigDict(frozen=True)

    frames_by_camera: dict[str, int] = Field(default_factory=dict)
    camera_index: dict[str, Any] = Field(default_factory=dict)


class TargetNightCounts(BaseModel):
    """The answer of `TargetCatalog.count_by_night`.

    Parameters
    ----------
    targets_by_night : `dict` [`str`, `int`]
        Each observing night (``YYYY-MM-DD``) and how many targets have
        frames from it.
    """

    model_config = ConfigDict(frozen=True)

    targets_by_night: dict[str, int] = Field(default_factory=dict)


class OverlayStar(BaseModel):
    """One star placed on a target's image, for drawing labels over it.

    The positions are pixels of the target's reference image (its stacked
    image, or its processed image when there is no stack). The field names
    turn into the camelCase keys the app reads.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    # Pixel column and row of the star's center, 1-based as in FITS and DS9
    # (the center of the first pixel is 1, 1).
    x: float
    y: float
    spectral_type: str = Field(default="", alias="spectralType")
    # True when the star was matched to an outside catalog, not only found
    # in the image.
    is_catalog_identified: bool = Field(default=False, alias="isCatalogIdentified")
    # Size of the reference image in pixels, or None when it is not known.
    reference_width: int | None = Field(default=None, alias="referenceWidth")
    reference_height: int | None = Field(default=None, alias="referenceHeight")
    # The star's measured radius in reference-image pixels, or None when it
    # was never measured.
    radius_px: float | None = Field(default=None, alias="radiusPx")


class TargetStarCount(BaseModel):
    """How many stars belong to one target, and what data they have."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    # The target's id. Empty in the app's per-target table, which keys the
    # counts by target id instead.
    target_id: str = Field(default="", alias="targetId")
    star_count: int = Field(default=0, alias="starCount")
    # True when at least one of the target's stars has that kind of data.
    has_spectra: bool = Field(default=False, alias="hasSpectra")
    has_photometry: bool = Field(default=False, alias="hasPhotometry")


class TargetReindexChange(BaseModel):
    """How one target's frame list changed in a scan."""

    model_config = ConfigDict(frozen=True)

    target_id: str
    frames_before: int
    frames_after: int
    # True when the target did not exist and the reindex made it.
    created: bool = False


class ReindexReport(BaseModel):
    """What `TargetFrames.scan` changed."""

    model_config = ConfigDict(frozen=True)

    targets: list[TargetReindexChange] = Field(default_factory=list)
