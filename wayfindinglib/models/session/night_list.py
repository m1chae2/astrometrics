"""Purpose: Data structures for the night list and the target changes.

Description: `control.history.query(kind="nights")` returns one
`NightListEntry` per recorded observing night, so a caller can pick a night
without guessing which nights have data. `control.history.query(
kind="target_changes")` returns one `TargetChange` for each time the mount
finished a slew and began tracking at a new place in the sky, matched to the
nearest target in the library. Both are computed when read and never
stored. The Ekos session record they come from is not changed.

Times are seconds since the Unix epoch. Angles are degrees. Positions are
ICRS (J2000).
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["NightListEntry", "TargetChange"]


class NightListEntry(BaseModel):
    """One recorded observing night and what data it has.

    Attributes
    ----------
    night_id : `str`
        The night, named for the local date on which it began, such as
        ``"2026-09-24"``. Pass it as ``session_id`` to the other history
        questions.
    night_date : `datetime.date`
        The same date as a date.
    targets_imaged : `list` [`str`]
        Ids of the library targets with light frames from the active
        equipment on this night, sorted. Empty when there are no frames.
    frame_count : `int`
        Light frames from the active equipment on this night.
    has_frame_data : `bool`
        Whether the night has light frames from the active equipment.
    has_guiding_data : `bool`
        Whether at least one guiding run is recorded for the night.
    has_sky_data : `bool`
        Whether at least one of the night's frames has a stored sky
        background measurement. Sky brightness and transparency checks
        need it.
    has_ekos_record : `bool`
        Whether an Ekos session record is stored for the night.
    """

    model_config = ConfigDict(populate_by_name=True)

    night_id: str = Field(alias="nightId")
    night_date: date = Field(alias="nightDate")
    targets_imaged: list[str] = Field(default_factory=list, alias="targetsImaged")
    frame_count: int = Field(default=0, alias="frameCount")
    has_frame_data: bool = Field(default=False, alias="hasFrameData")
    has_guiding_data: bool = Field(default=False, alias="hasGuidingData")
    has_sky_data: bool = Field(default=False, alias="hasSkyData")
    has_ekos_record: bool = Field(default=False, alias="hasEkosRecord")


class TargetChange(BaseModel):
    """A slew that ended in tracking at a new place in the sky.

    Attributes
    ----------
    time : `float`
        When the mount began tracking after the slew.
    night_id : `str`
        The observing night of the Ekos session it came from.
    ra_deg, dec_deg : `float` or `None`
        Where the mount pointed, in degrees. `None` when the position could
        not be worked out; `note` says why.
    target_id : `str` or `None`
        The nearest library target inside the match radius. `None` when no
        library target is that close.
    separation_deg : `float` or `None`
        How far the pointing is from `target_id`'s position, in degrees.
        `None` when there is no matched target.
    source : `str`
        The id of the Ekos session record the change was read from.
    note : `str` or `None`
        Why a field is missing, in plain words. `None` when nothing is
        missing.
    """

    model_config = ConfigDict(populate_by_name=True)

    time: float
    night_id: str = Field(alias="nightId")
    ra_deg: float | None = Field(default=None, alias="raDeg")
    dec_deg: float | None = Field(default=None, alias="decDeg")
    target_id: str | None = Field(default=None, alias="targetId")
    separation_deg: float | None = Field(default=None, alias="separationDeg")
    source: str
    note: str | None = None
