"""Data structures that report on calibration frames the library holds.

Calibration frames (darks, biases and flats) only help if the stacker can
find them and they are good. These structures answer two questions:

- `FlatSetAssessment` says whether one set of flats is good enough to use.
- `CalibrationSetHealth` says the same for one set of darks, biases or flats,
  with one record per check.
- `CalibrationIngestReport` says what a rescan of the library added, and
  how good each set that gained frames is (a `CalibrationSetHealth` each).

Both are plain data, so any caller can show
them without re-deriving anything.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib.models.quality.gate_result import GateResult


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
    # The binning the flats were taken at, such as "2x2" ("1x1" for none).
    # Flats at another binning cannot calibrate these lights.
    binning: str | None = None
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


class FrameConcern(BaseModel):
    """One calibration frame that a check found fault with.

    Attributes
    ----------
    path : `str`
        The frame's file path.
    reason : `str`
        One plain sentence saying what is wrong with this frame.
    """

    model_config = ConfigDict(populate_by_name=True)

    path: str
    reason: str


class CalibrationSetHealth(BaseModel):
    """How good one set of dark, bias or flat frames is.

    One model for all three kinds. The checks are `GateResult` records, the
    same shape the stack's gates use, so a check that did not run
    (``not_checked``) is never mistaken for one that passed. A set is judged
    when it is asked for, from the numbers stored for each frame and the
    current limits; nothing judged is stored.

    Attributes
    ----------
    kind : `str`
        ``"dark"``, ``"bias"`` or ``"flat"``.
    row_key : `str`
        The key `CalibrationCatalog.query` gives the same set, so a display
        can join the two. Two flat sets of different telescopes can share a
        key.
    camera, gain, offset, binning
        The set's camera and settings.
    temperature_slot_c : `float` or `None`
        A dark's temperature slot.
    exposure_seconds : `float` or `None`
        A dark's exposure.
    telescope, filter : `str` or `None`
        A flat's telescope and filter.
    frame_count : `int`
        The files in the set.
    unique_frame_count : `int`
        The files with different pixels. Files not measured yet count as
        unique.
    measured_frame_count : `int`
        The files whose numbers are stored. Flats are not measured file by
        file, so this is 0 for them.
    status : `str`
        ``"good"`` (every check that ran passed), ``"warning"`` (one failed)
        or ``"not_checked"`` (no check could run).
    summary : `str`
        One plain sentence: the first problem, or that none was found, or why
        nothing was checked.
    checks : `list` [`GateResult`]
        One record per check.
    frames_of_concern : `list` [`FrameConcern`]
        The frames a check singled out, at most 25.
    measurements : `dict` [`str`, `float` or `None`]
        The numbers behind the checks, such as the mean level and the worst
        zero share.
    smoothing_sigma_pixels : `float` or `None`
        For a flat set, the blur the stacker would apply to its master.
    """

    model_config = ConfigDict(populate_by_name=True)

    kind: str
    row_key: str = Field(alias="rowKey")
    camera: str
    gain: str
    offset: float
    binning: str | None = None
    temperature_slot_c: float | None = Field(default=None, alias="temperatureSlotC")
    exposure_seconds: float | None = Field(default=None, alias="exposureSeconds")
    telescope: str | None = None
    filter: str | None = None
    frame_count: int = Field(alias="frameCount")
    unique_frame_count: int = Field(alias="uniqueFrameCount")
    measured_frame_count: int = Field(default=0, alias="measuredFrameCount")
    status: Literal["good", "warning", "not_checked"]
    summary: str
    checks: list[GateResult] = Field(default_factory=list)
    frames_of_concern: list[FrameConcern] = Field(default_factory=list, alias="framesOfConcern")
    measurements: dict[str, float | None] = Field(default_factory=dict)
    smoothing_sigma_pixels: float | None = Field(default=None, alias="smoothingSigmaPixels")


class CalibrationMeasurementReport(BaseModel):
    """What one run of the dark and bias measuring job did.

    Attributes
    ----------
    frame_count : `int`
        The frames the run looked at.
    measured_count : `int`
        Frames read, measured and stored.
    unchanged_count : `int`
        Frames skipped because their file has not changed since it was
        measured.
    unreadable_count : `int`
        Frames that could not be read. The next run tries them again.
    failed_count : `int`
        Frames whose numbers could not be stored.
    rows_stored : `int`
        The new rows written to the quality store.
    notes : `list` [`str`]
        One plain sentence for each of the first failures.
    """

    model_config = ConfigDict(populate_by_name=True)

    frame_count: int = Field(alias="frameCount")
    measured_count: int = Field(alias="measuredCount")
    unchanged_count: int = Field(alias="unchangedCount")
    unreadable_count: int = Field(alias="unreadableCount")
    failed_count: int = Field(alias="failedCount")
    rows_stored: int = Field(alias="rowsStored")
    notes: list[str] = Field(default_factory=list)


class CalibrationIngestReport(BaseModel):
    """What a rescan of the calibration library changed.

    Returned by `CalibrationCatalog.refresh`, so a caller that has just
    downloaded new frames learns at once whether they were found, where
    they were filed and whether the new sets are good enough. The rescan
    measures no frame of a dark or bias set: those sets are judged from the
    numbers `CalibrationCatalog.measure_frames` stored, so a new frame
    without numbers gives a set whose checks are ``not_checked`` with the
    reason. A new set of flats is measured, as it always was.
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
    # One health record for each set that gained frames, in the same model
    # `CalibrationCatalog.assess` returns.
    assessments: list[CalibrationSetHealth] = Field(default_factory=list)
