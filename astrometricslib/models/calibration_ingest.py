"""Data structures that report on calibration frames the library holds.

Calibration frames (darks, biases and flats) only help if the stacker can
find them and they are good. These structures answer two questions:

- `FlatSetAssessment` says whether one set of flats is good enough to use.
- `CalibrationIngestReport` says what a rescan of the library added, and
  for flats, how good each newly added set is.

Both are plain data, so scripts, the backend and the MCP tools can show
them without re-deriving anything.
"""

from pydantic import BaseModel, ConfigDict, Field


class FlatSetAssessment(BaseModel):
    """How good one set of flats is.

    A set is every flat taken at the same gain and camera offset. Flats at
    other settings cannot calibrate these lights, so they are a different
    set.
    """

    model_config = ConfigDict(populate_by_name=True)

    # Where the set is filed in the calibration library.
    telescope: str
    camera: str
    filter: str
    # The gain (or ISO) setting, as text, and the camera offset.
    gain: str
    offset: float
    # How many flat frames are in the set.
    frame_count: int = Field(alias="frameCount")
    # Mean brightness of a flat as a fraction of full scale (0 to 1). The
    # assessment flags a set below `MINIMUM_FLAT_LEVEL_FRACTION` or above
    # `MAXIMUM_FLAT_LEVEL_FRACTION` (in `flat_calibration.py`). `None` if no
    # frame was readable.
    level_fraction: float | None = Field(default=None, alias="levelFraction")
    # Expected noise of the master flat, as a fraction of its brightness.
    # Above `MAXIMUM_FLAT_NOISE_FRACTION` the stacker smooths the master
    # flat. `None` if unmeasured.
    noise_fraction: float | None = Field(default=None, alias="noiseFraction")
    # Width, in pixels, of the Gaussian blur the stacker would apply to the
    # master flat to bring its noise under the limit. `None` if none is needed.
    smoothing_sigma_pixels: float | None = Field(default=None, alias="smoothingSigmaPixels")
    # True when no problem was found: the set is bright enough, not
    # saturated and quiet enough to use as it is.
    passes: bool
    # One plain sentence for each problem found. Empty when `passes` is true.
    issues: list[str] = Field(default_factory=list)


class CalibrationGroupChange(BaseModel):
    """How many frames one group gained or lost in a rescan."""

    model_config = ConfigDict(populate_by_name=True)

    # The group's place in the library, for example
    # "Apertura 75Q / ZWO ASI 533MM Pro / Luminance / 0.0@offset=10".
    group: str
    # Frames in the group that were not there before the rescan.
    added: int
    # Frames the rescan dropped because their files no longer exist.
    removed: int = 0
    # Frames in the group after the rescan.
    total: int


class CalibrationIngestReport(BaseModel):
    """What a rescan of the calibration library changed.

    Returned by `CalibrationCatalog.refresh`, so a caller that has just
    downloaded new frames learns at once whether they were found, where
    they were filed, and (for flats) whether they are good enough.
    """

    model_config = ConfigDict(populate_by_name=True)

    # Which kind of frame was rescanned: "dark", "bias" or "flat".
    kind: str
    # Totals over every group.
    added_count: int = Field(alias="addedCount")
    removed_count: int = Field(alias="removedCount")
    total_count: int = Field(alias="totalCount")
    # Only the groups that gained or lost frames.
    changes: list[CalibrationGroupChange] = Field(default_factory=list)
    # For flats: one assessment for each group that gained frames. Empty for
    # darks and biases.
    flat_assessments: list[FlatSetAssessment] = Field(default_factory=list, alias="flatAssessments")
