"""Purpose: The night plan laid out the way Ekos Capture asks for it.

Description: A `SequenceSummary` is built from a stored night plan
(`ObservationSession`) so the user can copy the values into Ekos. It holds
rows, not a string, so the app and the AI tools show the same thing
(register entry WAY-52).

Each capture job is one row. Its fields follow the Ekos Capture module in
this order: filter, exposure, count, binning, gain, offset. Each field
names the Ekos screen its value goes on. Dither size is set in the Ekos
Guide options, and dither frequency is set per capture job, so dithering
gets its own rows. A value the plan leaves open has `value` set to `None`
and means "leave the camera's current value".

The summary is never stored. It is worked out each time it is read.
"""

from pydantic import BaseModel, ConfigDict, Field

from wayfindinglib.models.session.observation_session import ObservationSession

EKOS_CAPTURE_SCREEN = "Ekos Capture module, sequence queue, job settings"
"""The Ekos screen where a capture job's own settings go."""

EKOS_GUIDE_OPTIONS_SCREEN = "Ekos Guide module, Options, Dither"
"""The Ekos screen where the dither size (in pixels) goes."""

EKOS_CAPTURE_DITHER_SCREEN = "Ekos Capture module, sequence queue, job settings, Dither"
"""The Ekos screen where a job's dither frequency (every N frames) goes."""

CAPTURE_FIELD_ORDER = ("filter", "exposure_seconds", "count", "binning", "gain_setting", "offset")
"""The order of the fields in a capture row, as Ekos Capture lists them."""


class SequenceField(BaseModel):
    """One value of a capture job and the Ekos screen it goes on."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(alias="name", description="The field, one of `CAPTURE_FIELD_ORDER`.")
    value: str | float | int | None = Field(
        default=None, alias="value", description="The value to enter. None: leave the camera's own value."
    )
    ekos_screen: str = Field(alias="ekosScreen", description="Where to enter the value in Ekos.")


class SequenceRow(BaseModel):
    """One capture job of the plan, with its fields in Ekos order."""

    model_config = ConfigDict(populate_by_name=True)

    entry_id: str = Field(alias="entryId", description="The queue entry the job comes from.")
    target_id: str = Field(alias="targetId")
    frame_type: str = Field(alias="frameType", description="LIGHT, DARK, FLAT or BIAS.")
    fields: list[SequenceField] = Field(default_factory=list, alias="fields")


class SequenceDitherRow(BaseModel):
    """The dithering of one queue entry, as the two Ekos settings it needs."""

    model_config = ConfigDict(populate_by_name=True)

    entry_id: str = Field(alias="entryId")
    size_pixels: float = Field(alias="sizePixels")
    size_ekos_screen: str = Field(default=EKOS_GUIDE_OPTIONS_SCREEN, alias="sizeEkosScreen")
    every_n_frames: int = Field(alias="everyNFrames")
    frequency_ekos_screen: str = Field(default=EKOS_CAPTURE_DITHER_SCREEN, alias="frequencyEkosScreen")


class SequenceSummary(BaseModel):
    """The night plan as rows for the Ekos Capture module."""

    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(alias="sessionId")
    revision: int = Field(ge=0, alias="revision", description="The plan revision the rows were built from.")
    rows: list[SequenceRow] = Field(default_factory=list, alias="rows")
    dither_rows: list[SequenceDitherRow] = Field(default_factory=list, alias="ditherRows")

    @classmethod
    def from_session(cls, session: ObservationSession) -> SequenceSummary:
        """Build the summary from a stored night plan.

        Parameters
        ----------
        session : `ObservationSession`
            The night plan. Its queue order is kept.

        Returns
        -------
        summary : `SequenceSummary`
            One row per exposure request, and one dither row per entry
            whose dithering is enabled.
        """
        rows: list[SequenceRow] = []
        dither_rows: list[SequenceDitherRow] = []
        for entry in session.queue:
            for request in entry.exposure_requests:
                values = {
                    "filter": request.filter.value,
                    "exposure_seconds": request.exposure_sec,
                    "count": request.count,
                    "binning": request.binning,
                    "gain_setting": request.gain_setting,
                    "offset": request.offset,
                }
                rows.append(
                    SequenceRow(
                        entry_id=entry.id,
                        target_id=entry.target_id,
                        frame_type=request.frame_type.value,
                        fields=[
                            SequenceField(name=name, value=values[name], ekos_screen=EKOS_CAPTURE_SCREEN)
                            for name in CAPTURE_FIELD_ORDER
                        ],
                    )
                )
            if entry.dither_config is not None and entry.dither_config.enabled:
                dither_rows.append(
                    SequenceDitherRow(
                        entry_id=entry.id,
                        size_pixels=entry.dither_config.pixels,
                        every_n_frames=entry.dither_config.every_n_frames,
                    )
                )
        return cls(session_id=session.id, revision=session.revision, rows=rows, dither_rows=dither_rows)
