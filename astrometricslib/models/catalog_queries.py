"""Data structures for the answers of the catalog lookups.

The target and star catalogs each have one general lookup, `query`, that
reads and never changes anything. These structures are what those lookups
return:

- `TargetQueryResult` from `TargetCatalog.query`: target rows, full `Target`
  records, one target's full record, the cameras used, the observing
  nights, or each target's frames per camera.
- `TargetStarCount` from `StellarCatalog.count_by_target`: one target's
  star count and data flags. `OverlayStar` is one star drawn over a
  target's image (`Visualization.get_star_overlay`).
- `ReindexReport` from `TargetCatalog.reindex_frames`, which does change
  data: how many frames each target gained or lost.

Only the fields that belong to the chosen ``detail`` are filled; the rest
stay `None`. Rows are plain dictionaries, so a caller can pass them on as
JSON unchanged.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib.models.target import Target

__all__ = [
    "OverlayStar",
    "ReindexReport",
    "TargetQueryResult",
    "TargetReindexChange",
    "TargetStarCount",
]


class TargetQueryResult(BaseModel):
    """The answer of `TargetCatalog.query`."""

    detail: str
    # For lists: how many targets matched, the rows skipped, and whether the
    # list was cut by the limit.
    total_matching: int | None = None
    offset: int | None = None
    truncated: bool | None = None
    # detail="summary": one short row per target.
    targets: list[dict[str, Any]] | None = None
    # detail="objects": the full `Target` records, in stored order.
    objects: list[Target] | None = None
    # detail="full": one target's record, with its frames grouped.
    target: dict[str, Any] | None = None
    # detail="cameras": each camera name and how many frames it took.
    cameras: dict[str, int] | None = None
    # detail="nights": each observing night and how many targets have frames
    # from it.
    nights: dict[str, int] | None = None
    # detail="camera_index": each target's light frames per configured
    # camera, for the app's target list.
    camera_index: dict[str, Any] | None = None


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
    """How one target's frame list changed in a reindex."""

    target_id: str
    frames_before: int
    frames_after: int
    # True when the target did not exist and the reindex made it.
    created: bool = False


class ReindexReport(BaseModel):
    """What `TargetCatalog.reindex_frames` changed."""

    targets: list[TargetReindexChange] = Field(default_factory=list)
    # Paths that were asked for and are now frame records.
    added_paths: list[str] = Field(default_factory=list)
    # The path, among those asked for, that became the target's processed
    # image (a .jpg, .png or .tiff picture rather than a FITS frame).
    processed_image: str | None = None
