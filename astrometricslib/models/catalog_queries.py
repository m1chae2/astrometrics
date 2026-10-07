"""Data structures for the answers of the catalog lookups.

The three catalogs each have one general lookup, `query`, that reads and
never changes anything. These structures are what those lookups return:

- `TargetQueryResult` from `TargetCatalog.query`: target rows, one target's
  full record, the cameras used, the observing nights, or each target's
  frames per camera.
- `StarQueryResult` from `StellarCatalog.query`: star ids, summary rows,
  analysis records, full star records, class counts or library statistics.
- `CalibrationQueryResult` from `CalibrationCatalog.query`: how many dark,
  bias and flat frames the library holds, or how a target's frames match it.
- `ReindexReport` from `TargetCatalog.reindex_frames`, which does change
  data: how many frames each target gained or lost.

Only the fields that belong to the chosen ``detail`` are filled; the rest
stay `None`. Rows are plain dictionaries because the user interface and the
AI clients read them as they are.
"""

from typing import Any

from pydantic import BaseModel, Field

from astrometricslib.models.stellar_source import StellarObject

__all__ = [
    "CalibrationQueryResult",
    "ReindexReport",
    "StarQueryResult",
    "TargetQueryResult",
    "TargetReindexChange",
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
