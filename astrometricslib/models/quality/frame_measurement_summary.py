"""What one run of the frame measurement job did.

`ProcessingPipelines.record_frame_measurements` measures each light frame of
a target once and stores the numbers in the quality database. A frame whose
file has not changed since it was last measured is skipped.
`FrameMeasurementSummary` counts what happened, so a caller can tell a run
that measured nothing because everything was current from one that failed.
"""

from pydantic import BaseModel, ConfigDict, Field


class FrameMeasurementSummary(BaseModel):
    """The counts from one run of the frame measurement job.

    Attributes
    ----------
    target_id : `str`
        The target whose frames were measured.
    frames_considered : `int`
        The light frames the run looked at.
    frames_measured : `int`
        Frames measured and stored.
    frames_skipped_unchanged : `int`
        Frames left alone because their file is the same size and was last
        changed at the same time as when they were last measured.
    frames_failed : `int`
        Frames that could not be measured or whose rows could not be stored.
        They are measured again on the next run.
    rows_written : `int`
        New rows added to the quality database. A repeat of a stored run is
        not counted.
    observing_night_ids : `list` [`str`]
        The observing nights of the frames measured, sorted, without repeats.
    seconds : `float`
        How long the run took, in seconds.
    notes : `list` [`str`]
        Plain sentences about anything that did not go as planned, such as
        the first few frames that failed and why.
    """

    model_config = ConfigDict(populate_by_name=True)

    target_id: str = Field(alias="targetId")
    frames_considered: int = Field(default=0, alias="framesConsidered")
    frames_measured: int = Field(default=0, alias="framesMeasured")
    frames_skipped_unchanged: int = Field(default=0, alias="framesSkippedUnchanged")
    frames_failed: int = Field(default=0, alias="framesFailed")
    rows_written: int = Field(default=0, alias="rowsWritten")
    observing_night_ids: list[str] = Field(default_factory=list, alias="observingNightIds")
    seconds: float = 0.0
    notes: list[str] = Field(default_factory=list)
