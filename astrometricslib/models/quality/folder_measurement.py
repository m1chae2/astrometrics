"""The result of measuring a folder of frames without storing anything.

`QualityRecords.measure_folder` reads each FITS file of a folder and measures
it with the same `frame.*` metrics that `record_frame_measurements` stores for
a library target. Nothing is written to the quality store, and the folder need
not be in the library. These models carry the numbers back to the caller: one
value per metric for each frame, and the median of each metric over the
frames.
"""

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["FolderFrameMeasurement", "FolderMeasurementReport"]


class FolderFrameMeasurement(BaseModel):
    """The `frame.*` metrics of one frame in a folder.

    Parameters
    ----------
    file_name : `str`
        The frame's file name.
    exposure_seconds : `float` or `None`
        The exposure length from the header, in seconds.
    timestamp : `float` or `None`
        The start of the exposure, in seconds since the Unix epoch.
    values : `dict` [`str`, `float`]
        The value of each metric that was measured or assumed, by metric name
        (for example ``"frame.star_width_moffat_px"``, in pixels, and
        ``"frame.sky_rate_e_per_s_per_px"``, in electrons per second per
        pixel). A frame stored as color planes gives one entry per plane,
        named ``metric[plane]``.
    uncertainties : `dict` [`str`, `float`]
        One standard error for each value that has one, under the same key.
    not_measured : `dict` [`str`, `str`]
        The reason for each metric that could not be measured.
    """

    model_config = ConfigDict(populate_by_name=True)

    file_name: str = Field(alias="fileName")
    exposure_seconds: float | None = Field(default=None, alias="exposureSeconds")
    timestamp: float | None = None
    values: dict[str, float] = Field(default_factory=dict)
    uncertainties: dict[str, float] = Field(default_factory=dict)
    not_measured: dict[str, str] = Field(default_factory=dict, alias="notMeasured")


class FolderMeasurementReport(BaseModel):
    """The measurements of a folder of frames, with their medians.

    Parameters
    ----------
    folder_path : `str`
        The folder.
    frames_matching : `int`
        How many FITS files the selection kept.
    frames_measured : `int`
        How many of them were measured (at most the limit).
    bias_level_adu : `float` or `None`
        The bias level used for the sky metrics, in ADU, when one common
        level was given. `None` when it came from the calibration library
        (per camera setting) or none was available.
    frames : `list` [`FolderFrameMeasurement`]
        One entry per measured frame, in file-name order.
    medians : `dict` [`str`, `float`]
        The median over the frames of each metric's value. A metric measured
        on no frame has no entry.
    notices : `list` [`str`]
        Warnings the measurement raised, such as that a frame was drawn by a
        camera simulator (its noise, sky and depth values do not describe a
        real sky).
    notes : `list` [`str`]
        Remarks, such as why the sky metrics could not be found.
    """

    model_config = ConfigDict(populate_by_name=True)

    folder_path: str = Field(alias="folderPath")
    frames_matching: int = Field(default=0, alias="framesMatching")
    frames_measured: int = Field(default=0, alias="framesMeasured")
    bias_level_adu: float | None = Field(default=None, alias="biasLevelAdu")
    frames: list[FolderFrameMeasurement] = Field(default_factory=list)
    medians: dict[str, float] = Field(default_factory=dict)
    notices: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
