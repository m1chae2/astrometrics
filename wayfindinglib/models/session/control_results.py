"""Purpose: The results of the `control.remote` and `control.guiding` calls.

Description: `control.remote.frame_status`, `sync_frames` and `sync_logs`,
`control.guiding.expose` and `review.frame_guiding` each return a model
that names its fields. The remote results fill different fields in
different cases (see the call's description), and leave the others out
when turned into plain data, so the data keeps the keys the dictionary
replies had before.
"""

from pydantic import BaseModel, JsonValue

from wayfindinglib.models.session.sparse_result import SparseResult

__all__ = [
    "FrameDifference",
    "FrameGuidingReport",
    "FrameStatusReport",
    "FrameSyncResult",
    "GuideExposureResult",
    "LogPlan",
    "LogSyncResult",
]


class GuideExposureResult(BaseModel):
    """What `control.guiding.expose` did.

    Attributes
    ----------
    started : `bool`
        Whether the guide camera accepted the exposure command.
    exposure_seconds : `float`
        The exposure length that was asked for.
    gain : `float`, optional
        The gain that was set first, or `None` if it was left alone.
    """

    started: bool
    exposure_seconds: float
    gain: float | None = None


class FrameSyncResult(SparseResult):
    """The result of `control.remote.sync_frames`.

    Which fields are filled depends on the case:

    - every folder: `succeeded`, `failed`, `job_id`, `measurements`;
    - a local folder or chosen files: `success`, `target`, `measurements`;
    - a calibration folder: `success`, `remote_count`,
      `already_held_count`, `transferred_count`, `added_by_folder`;
    - one target: the plan (`target_id`, `remote_folder`, `remote_files`,
      `already_held`, `to_transfer`, `examples`) and, for a real run,
      `success`, `transferred`, `measurements` and `message`.

    `measurements` lists the summary of each frame measurement run the sync
    started, as plain data.
    """

    target_id: str | None = None
    target: str | None = None
    remote_folder: str | None = None
    remote_files: int | None = None
    already_held: int | None = None
    to_transfer: int | None = None
    examples: list[str] | None = None
    success: bool | None = None
    transferred: int | None = None
    message: str | None = None
    measurements: list[JsonValue] | None = None
    succeeded: JsonValue = None
    failed: JsonValue = None
    job_id: str | None = None
    remote_count: int | None = None
    already_held_count: int | None = None
    transferred_count: int | None = None
    added_by_folder: JsonValue = None


class LogPlan(BaseModel):
    """Which logs of one kind on the observatory computer are new.

    Attributes
    ----------
    supported : `bool`
        Whether the transfer driver can list this kind of log.
    remote_files : `int`, optional
        How many logs of this kind the observatory computer holds.
    to_download : `int`, optional
        How many of them are new.
    examples : `list` [`str`], optional
        A few of the new log names.
    """

    supported: bool
    remote_files: int | None = None
    to_download: int | None = None
    examples: list[str] | None = None


class LogSyncResult(SparseResult):
    """The result of `control.remote.sync_logs`.

    A dry run or a real run fills the plan (`dry_run`, `destination_dir`,
    `guide_logs`, `ekos_analyze_logs`) and a real run adds `ingested`, the
    summary of what was stored. A run without downloading holds only the
    summary fields of what was stored.
    """

    dry_run: bool | None = None
    destination_dir: str | None = None
    guide_logs: LogPlan | None = None
    ekos_analyze_logs: LogPlan | None = None
    ingested: JsonValue = None


class FrameDifference(BaseModel):
    """Frames that are in one place and not in the next.

    Attributes
    ----------
    count : `int`
        How many frames.
    examples : `list` [`str`]
        The last few file names, in order.
    """

    count: int
    examples: list[str]


class FrameStatusReport(SparseResult):
    """Where a target's frames are: telescope, drive, library.

    Attributes
    ----------
    target_id : `str`
        The target.
    remote_folder : `str`, optional
        The target's folder on the observatory computer, if found.
    telescope_error, library_error : `str`, optional
        Why the telescope part or the library part could not be given.
    counts : `dict` [`str`, `int`], optional
        Frames in each place: ``telescope``, ``disk``,
        ``set_aside_on_disk`` and ``library`` (a place that could not be
        read is `None`).
    on_telescope_not_on_disk, on_disk_not_on_telescope : `FrameDifference`
        Frames on one of the two and not the other (optional).
    on_disk_not_in_library, in_library_not_on_disk : `FrameDifference`
        Frames on disk and not in the library, and the reverse (optional).
    note : `str`, optional
        How the places were matched.
    """

    target_id: str | None = None
    remote_folder: str | None = None
    telescope_error: str | None = None
    library_error: str | None = None
    counts: dict[str, int | None] | None = None
    on_telescope_not_on_disk: FrameDifference | None = None
    on_disk_not_on_telescope: FrameDifference | None = None
    on_disk_not_in_library: FrameDifference | None = None
    in_library_not_on_disk: FrameDifference | None = None
    note: str | None = None


class FrameGuidingReport(SparseResult):
    """The guide error during each light frame's exposure.

    Attributes
    ----------
    target_id : `str`
        The library target.
    frames_matching : `int`
        How many light frames matched the selection.
    frames_reported, frames_without_guiding : `int`, optional
        How many rows the report holds, and how many have no guide samples.
    group : `JsonValue`, optional
        The median error and the frames well above it.
    note, image_quality : `str`, optional
        What the numbers are, and how many frames had image measurements.
    frames : `list` [`JsonValue`]
        One row per frame.
    """

    target_id: str | None = None
    frames_matching: int | None = None
    frames_reported: int | None = None
    frames_without_guiding: int | None = None
    group: JsonValue = None
    note: str | None = None
    image_quality: str | None = None
    frames: list[JsonValue] | None = None
