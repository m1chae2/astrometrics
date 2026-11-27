"""Purpose: What a night plan has captured so far, for the progress cards.

Description: `wayfinder.planning.sessions.get(night, include=["progress"])`
counts the frames that arrived and matches them to the session's entries.
These models carry the answer: for each entry, how many light frames were
requested and captured, how many carry a quality flag, how many are not judged
yet, and a typed run state. The session as a whole has the entry being imaged
now and its own run state, including "not run" for a night that passed with
nothing captured.

The counting rules are in `wayfindinglib.analytics.frame_counting`. The
screen draws these numbers as they are and decides nothing.
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class RunState(StrEnum):
    """How far a session or one of its entries has got, from the frames.

    The library works it out from the frames that arrived (see
    `wayfindinglib.analytics.session_progress`). The value is typed so a
    screen never has to decide it.

    * `UPCOMING`: the night has not begun, or nothing has been captured yet.
    * `IMAGING`: a frame arrived a short while ago, so it is imaged now.
    * `IN_PROGRESS`: tonight, some frames are in, and nothing arrived lately.
    * `COMPLETE`: every light frame requested has been captured and judged.
    * `PARTLY_DONE`: the night is over and some, but not all, frames came in.
    * `NOT_RUN`: the night is over and no frame came in.
    * `UNKNOWN`: the frames could not be read, so the library cannot say.
    """

    UPCOMING = "upcoming"
    IMAGING = "imaging"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    PARTLY_DONE = "partly_done"
    NOT_RUN = "not_run"
    UNKNOWN = "unknown"


class RowProgress(BaseModel):
    """The frames captured for one light row of an entry.

    Parameters
    ----------
    source_key : `str`
        The row's source key, when the library suggested it.
    filter_name : `str`
        The row's filter.
    exposure_s : `float`
        The row's frame length in seconds.
    requested : `int`
        How many frames the row asks for.
    captured : `int`
        Matching frames that arrived, judged or not, and not set aside.
    judged : `int`
        How many of them have a stored quality judgment.
    flagged : `int`
        How many of the judged frames carry a quality flag. They still count.
    not_judged : `int`
        How many have no judgment yet. They do not complete the row.
    met : `bool`
        `True` when the judged frames reach the requested count.
    """

    model_config = ConfigDict(populate_by_name=True)

    source_key: str = Field(default="", alias="sourceKey")
    filter_name: str = Field(default="", alias="filterName")
    exposure_s: float = Field(
        alias="exposureS",
        validation_alias=AliasChoices("exposureS", "exposure_s", "exposureSec", "exposure_sec"),
    )
    requested: int
    captured: int
    judged: int
    flagged: int
    not_judged: int = Field(alias="notJudged")
    met: bool


class EntryProgress(BaseModel):
    """The progress of one queue entry.

    Parameters
    ----------
    entry_id : `str`
        The queue entry id.
    target_id : `str`
        The entry's target.
    requested : `int`
        Light frames asked for, over all light rows.
    captured : `int`
        Light frames captured, each row capped at its request.
    flagged : `int`
        Flagged frames among them.
    not_judged : `int`
        Frames not judged yet.
    rows : `list` [`RowProgress`]
        The light rows, in order.
    newest_frame_time : `datetime` or `None`
        When the newest counted frame was taken. `None` when none was.
    state : `RunState`
        Where the entry stands.
    imaging_now : `bool`
        `True` when this is the entry being imaged now.
    note : `str`
        A plain sentence when the frames could not be read, else empty.
    """

    model_config = ConfigDict(populate_by_name=True)

    entry_id: str = Field(alias="entryId")
    target_id: str = Field(alias="targetId")
    requested: int
    captured: int
    flagged: int
    not_judged: int = Field(alias="notJudged")
    rows: list[RowProgress] = Field(default_factory=list)
    newest_frame_time: datetime | None = Field(default=None, alias="newestFrameTime")
    state: RunState
    imaging_now: bool = Field(default=False, alias="imagingNow")
    note: str = ""


class SessionProgress(BaseModel):
    """The progress of a whole session.

    Parameters
    ----------
    session_id : `str`
        The session.
    entries : `list` [`EntryProgress`]
        One per queue entry, in queue order.
    imaging_now_entry_id : `str` or `None`
        The entry being imaged now, or `None`.
    run_state : `RunState`
        Where the session stands.
    as_of : `datetime`
        When the counts were made (UTC).
    """

    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(alias="sessionId")
    entries: list[EntryProgress] = Field(default_factory=list)
    imaging_now_entry_id: str | None = Field(default=None, alias="imagingNowEntryId")
    run_state: RunState = Field(alias="runState")
    as_of: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="asOf")
