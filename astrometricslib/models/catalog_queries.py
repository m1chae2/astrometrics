"""Data structures for the answers of the catalog lookups.

The three catalogs each have one general lookup, `query`, that reads and
never changes anything. These structures are what those lookups return:

- `TargetQueryResult` from `TargetCatalog.query`: target rows, one target's
  full record, the cameras used, the observing nights, or each target's
  frames per camera.
- `StarQueryResult` from `StellarCatalog.query`: star ids, summary rows,
  analysis records, full star records, class counts, per-target star
  counts (`TargetStarCount`), the stars drawn over a target's image
  (`OverlayStar`), or library statistics.
- `CalibrationQueryResult` from `CalibrationCatalog.query`: how many dark,
  bias and flat frames the library holds, or how a target's frames match it.
- `ReindexReport` from `TargetCatalog.reindex_frames`, which does change
  data: how many frames each target gained or lost.

Only the fields that belong to the chosen ``detail`` are filled; the rest
stay `None`. Rows are plain dictionaries, so a caller can pass them on as
JSON unchanged.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib.models.stellar_source import StellarObject

__all__ = [
    "CalibrationQueryResult",
    "OverlayStar",
    "ReindexReport",
    "StarQueryResult",
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
    # Pixel column and row of the star's center, counted from 0.
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

    model_config = ConfigDict(populate_by_name=True)

    star_count: int = Field(default=0, alias="starCount")
    # True when at least one of the target's stars has that kind of data.
    has_spectra: bool = Field(default=False, alias="hasSpectra")
    has_photometry: bool = Field(default=False, alias="hasPhotometry")


class StarQueryResult(BaseModel):
    """The answer of `StellarCatalog.query`."""

    detail: str
    total_matching: int | None = None
    offset: int | None = None
    truncated: bool | None = None
    # detail="ids": the star ids.
    ids: list[str] | None = None
    # detail="exists": which of the given ids are in the library.
    found: list[str] | None = None
    # detail="summary": id, name, position, magnitude, spectral type,
    # targets and data flags, with the camelCase keys the app reads.
    # detail="analysis": what the analysis found for each star.
    stars: list[dict[str, Any]] | None = None
    # detail="objects": the full star records.
    objects: list[StellarObject] | None = None
    # detail="class_counts": how many stars each spectral class has.
    classes: list[dict[str, Any]] | None = None
    # detail="target_counts": each target's star count and data flags.
    target_counts: dict[str, TargetStarCount] | None = None
    # detail="overlay": the target's stars placed on its reference image.
    overlay: list[OverlayStar] | None = None
    # detail="stats": counts and coverage for the whole library.
    stats: dict[str, Any] | None = None


class CalibrationQueryResult(BaseModel):
    """The answer of `CalibrationCatalog.query`."""

    detail: str
    # The calibration kind asked for (dark, bias or flat), or None for all.
    kind: str | None = None
    target_id: str | None = None
    # detail="counts": one row per camera, setting and exposure (or filter).
    darks: list[dict[str, Any]] | None = None
    biases: list[dict[str, Any]] | None = None
    flats: list[dict[str, Any]] | None = None
    # detail="target_match": the target's light frames grouped by filter,
    # gain and exposure, each with the number of matching darks.
    groups: list[dict[str, Any]] | None = None
    # detail="target_frames": the target's light frames counted by
    # telescope, camera, gain, exposure and filter.
    lights: list[dict[str, Any]] | None = None


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
