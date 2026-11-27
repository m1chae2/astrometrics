"""The result of measuring frames with the frame metrics, storing nothing.

`quality.frames.measure` with ``include=["metrics"]`` reads each frame and
measures it with the same `frame.*` metrics that `quality.frames.record`
stores for a library target. Nothing is written to the quality store, and the
frames need not be in the library. These models carry the numbers back to
the caller: one value per metric for each frame, and the median of each
metric over the frames.
"""

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

__all__ = ["FolderFrameMeasurement", "FolderMeasurementReport"]


class FolderFrameMeasurement(BaseModel):
    """The `frame.*` metrics of one frame in a folder.

    Parameters
    ----------
    file_name : `str`
        The frame's file name.
    exposure_s : `float` or `None`
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
    widths_on_scale_px : `dict` [`str`, `float`]
        Each star width of ``values`` put on the library's one star-width
        scale (the per-frame Gaussian of the brightest stars), in pixels,
        under the same key, so widths from different estimators can be
        compared. Only pixel widths whose estimator has a measured ratio to
        the scale are here; ``star_widths_not_on_scale`` of the report says
        why the others are not.
    not_measured : `dict` [`str`, `str`]
        The reason for each metric that could not be measured.
    """

    model_config = ConfigDict(populate_by_name=True)

    file_name: str = Field(alias="fileName")
    exposure_s: float | None = Field(
        default=None,
        alias="exposureS",
        validation_alias=AliasChoices("exposureS", "exposure_s", "exposureSeconds", "exposure_seconds"),
    )
    timestamp: float | None = None
    values: dict[str, float] = Field(default_factory=dict)
    uncertainties: dict[str, float] = Field(default_factory=dict)
    widths_on_scale_px: dict[str, float] = Field(default_factory=dict, alias="widthsOnScalePx")
    not_measured: dict[str, str] = Field(default_factory=dict, alias="notMeasured")


class FolderMeasurementReport(BaseModel):
    """The measurements of a folder of frames, with their medians.

    Parameters
    ----------
    folder_path : `str` or `None`
        The folder the frames are in, or `None` when they are in several.
    frames_matching : `int`
        How many frames were given.
    frames_measured : `int`
        How many of them were measured.
    bias_level_adu : `float` or `None`
        The bias level used for the sky metrics, in ADU, when one common
        level was given. `None` when it came from the calibration library
        (per camera setting) or none was available.
    frames : `list` [`FolderFrameMeasurement`]
        One entry per measured frame, in file-name order.
    medians : `dict` [`str`, `float`]
        The median over the frames of each metric's value. A metric measured
        on no frame has no entry.
    medians_on_scale_px : `dict` [`str`, `float`]
        The median over the frames of each ``widths_on_scale_px`` entry.
    star_width_estimators : `dict` [`str`, `str`]
        The estimator of each star width in ``medians``, by key: for example
        ``"frame Moffat"`` for ``frame.star_width_moffat_px`` and ``"frame
        Gaussian, brightest stars"`` (the scale itself) for
        ``frame.star_width_gaussian_brightest_px``.
    star_widths_not_on_scale : `dict` [`str`, `str`]
        Why each star width in ``medians`` that has no value on the scale
        has none: a width in arcseconds (use the pixel metric), or an
        estimator with no measured ratio to the scale, such as the
        effective width.
    notices : `list` [`str`]
        Warnings the measurement raised, such as that a frame was drawn by a
        camera simulator (its noise, sky and depth values do not describe a
        real sky).
    notes : `list` [`str`]
        Remarks, such as why the sky metrics could not be found.
    """

    model_config = ConfigDict(populate_by_name=True)

    folder_path: str | None = Field(default=None, alias="folderPath")
    frames_matching: int = Field(default=0, alias="framesMatching")
    frames_measured: int = Field(default=0, alias="framesMeasured")
    bias_level_adu: float | None = Field(default=None, alias="biasLevelAdu")
    frames: list[FolderFrameMeasurement] = Field(default_factory=list)
    medians: dict[str, float] = Field(default_factory=dict)
    medians_on_scale_px: dict[str, float] = Field(default_factory=dict, alias="mediansOnScalePx")
    star_width_estimators: dict[str, str] = Field(default_factory=dict, alias="starWidthEstimators")
    star_widths_not_on_scale: dict[str, str] = Field(default_factory=dict, alias="starWidthsNotOnScale")
    notices: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
