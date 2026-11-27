"""Purpose: The owners of the night, guiding run and pointing model reads.

Description: The app records each observing night in several places: the
Ekos session logs, the guiding runs, the guide-log samples, the frame
library and the plate solves. The analyses in `night_analysis` turn these
into findings. The functions here are the owners behind the public review
methods: each reads one kind of answer for one night, or lists the nights,
the guiding runs or the Ekos sessions as a `Page`.

A list returns every match unless the caller asks for fewer; an adapter
that relays a reply as text keeps it to the size its client accepts.
Nothing here writes.
"""

from __future__ import annotations

import copy
import time
from datetime import date
from typing import TYPE_CHECKING, Any

from astrometricslib.arguments import Page, check_include, observing_night_id
from astrometricslib.exceptions import (
    ConfigurationError,
    ConflictError,
    InvalidArgumentError,
    NotFoundError,
    StorageError,
)
from wayfindinglib.analytics import night_index
from wayfindinglib.analytics.data_versions import (
    nights_context_signs,
    nights_data_versions,
    nights_index_version,
)
from wayfindinglib.analytics.night_index import NightFrame, NightIndex, build_night_index
from wayfindinglib.models.session.ekos_session import EkosSessionSummary
from wayfindinglib.models.session.night_list import NightListEntry
from wayfindinglib.tasks.control_tasks import night_analysis

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext
    from wayfindinglib.models.session.capture_quality import CaptureSessionAnalysis
    from wayfindinglib.models.session.ekos_session import EkosSessionContext
    from wayfindinglib.models.session.guiding_run import GuidingRunSummary
    from wayfindinglib.models.session.session_quality import GuidingSessionAnalysis
    from wayfindinglib.models.session.sky_quality import SkyAnalysis
    from wayfindinglib.models.session.telemetry import MountPointingModel

EKOS_SECTIONS = (
    "captures",
    "aborted_captures",
    "autofocus_runs",
    "align_events",
    "guide_state_events",
    "mount_state_events",
    "temperatures",
    "mount_positions",
    "equipment",
)
"""The parts of an Ekos session record that can be asked for."""

MAXIMUM_LIMIT = 50
"""Most items of each Ekos section the live status keeps."""


def to_plain(value: Any) -> Any:
    """Turn a result into plain JSON-safe data.

    Parameters
    ----------
    value : `Any`
        A pydantic model, a list or dict of them, or plain data.

    Returns
    -------
    plain : `Any`
        The same content as dicts, lists, strings and numbers. Field names
        are the Python names, not the camelCase aliases.
    """
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): to_plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [to_plain(item) for item in value]
    return value


def sample_evenly(items: list[Any], count: int) -> list[Any]:
    """Pick items spread evenly across a list, keeping the first and last.

    Parameters
    ----------
    items : `list`
        The items, in time order.
    count : `int`
        How many to keep.

    Returns
    -------
    sample : `list`
        All of ``items`` if there are no more than ``count``, otherwise
        ``count`` of them at even steps.
    """
    if len(items) <= count:
        return items
    if count <= 1:
        return items[:1]
    step = (len(items) - 1) / (count - 1)
    return [items[round(index * step)] for index in range(count)]


def ekos_overview(context: Any) -> dict[str, Any]:
    """Describe an Ekos session in a few fields.

    Parameters
    ----------
    context : `EkosSessionContext`
        The recorded session.

    Returns
    -------
    overview : `dict` [`str`, `Any`]
        Id, night, times, equipment fingerprint, and how many items each
        section holds.
    """
    return {
        "id": context.id,
        "session_id": context.session_id,
        "started_at": context.started_at,
        "ended_at": context.ended_at,
        "equipment_fingerprint": context.equipment.equipment_fingerprint if context.equipment else None,
        "section_counts": {
            name: len(getattr(context, name)) for name in EKOS_SECTIONS if name not in ("equipment",)
        },
    }


def ekos_sections(context: Any, include: list[str], limit: int) -> dict[str, Any]:
    """Pull chosen sections out of an Ekos session record.

    Parameters
    ----------
    context : `EkosSessionContext`
        The recorded session.
    include : `list` [`str`]
        Names from ``EKOS_SECTIONS``.
    limit : `int`
        Most items kept per list section. A longer list is sampled evenly
        so the whole night is still represented.

    Returns
    -------
    sections : `dict` [`str`, `Any`]
        The overview plus each chosen section. A list section reports its
        ``total`` and how many it ``shown``.
    """
    answer: dict[str, Any] = {"overview": ekos_overview(context)}
    for name in include:
        value = getattr(context, name)
        if name == "equipment":
            answer[name] = to_plain(value)
            continue
        shown = sample_evenly(value, limit)
        answer[name] = {"total": len(value), "shown": len(shown), "items": to_plain(shown)}
    return answer


def build_night_list(context: ControlContext, now: float | None = None) -> list[NightListEntry]:
    """List the recorded observing nights and what data each has.

    A night is listed if it has light frames from the active equipment, an
    Ekos session record or a guiding run. With no telescope and camera
    active, the frame columns are empty and the other two sources still
    list their nights.

    The list is kept in two parts. The entries of the finished nights are
    built once and kept until something that touches a finished night
    changes. The entry of the night in progress is built again whenever its
    own data change, which during a night is about once a minute, and that
    needs only tonight's frames. The result is the same as building the
    whole list.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the recorded Ekos sessions, guiding runs and the science
        library's frame records.
    now : `float`, optional
        The moment that decides which night is in progress, as seconds since
        the Unix epoch. Defaults to the present.

    Returns
    -------
    nights : `list` [`NightListEntry`]
        One entry per night, oldest first. The two parts are kept in the
        context's review cache until the data behind them change (the live
        guiding samples do not count), and each caller gets its own copy.
    """
    tonight = observing_night_id(time.time() if now is None else now)
    signs = nights_context_signs(context.config)
    cache = context.review_cache
    index = cache.get_or_build(
        ("nights", "index"),
        lambda: nights_index_version(context, tonight, signs),
        lambda: _read_night_index(context, tonight),
        copy_result=False,
    )
    versions = nights_data_versions(context, index, signs)
    finished_version, tonight_version = (None, None) if versions is None else versions
    finished = cache.get_or_build(
        ("nights", "finished"),
        finished_version,
        lambda: _build_night_list(context, index, finished=True),
        copy_result=False,
    )
    in_progress = cache.get_or_build(
        ("nights", "tonight"),
        tonight_version,
        lambda: _build_night_list(context, index, finished=False),
        copy_result=False,
    )
    entries: list[NightListEntry] = []
    for night_id, entry in sorted([*finished, *in_progress], key=lambda pair: pair[0]):
        # A recorded session named by something that is not a date fails here,
        # and only when that night is among the ones listed.
        entries.append(entry if entry is not None else _entry_of(night_id, [], index, frozenset()))
    return [copy.deepcopy(entry) for entry in entries]


def _read_night_index(context: ControlContext, tonight: str) -> NightIndex:
    """Read the index of the nights, or fail if the sessions cannot be read.

    Returns
    -------
    index : `NightIndex`
        The nights' frames and recorded sessions.

    Raises
    ------
    StorageError
        If the recorded sessions cannot be read.
    """
    index = build_night_index(context, tonight)
    if index is None:
        raise StorageError("Could not read the recorded Ekos sessions and guiding runs.")
    return index


def _build_night_list(
    context: ControlContext, index: NightIndex, finished: bool
) -> list[tuple[str, NightListEntry | None]]:
    """Build the entries of the finished nights, or of the night in progress.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the science library, for the stored sky background rows.
    index : `NightIndex`
        The nights' frames and recorded sessions.
    finished : `bool`
        Build the nights before tonight if true, and tonight (and any later
        night) if false.

    Returns
    -------
    entries : `list` [`tuple` [`str`, `NightListEntry` or `None`]]
        Each night's id with its entry, oldest first. The entry is `None`
        when the night's id is not a date; asking for that night fails then.
    """

    def wanted(night: str) -> bool:
        """Say whether a night belongs to the part being built.

        Returns
        -------
        wanted : `bool`
            True when the night is finished and the finished part is built,
            or the night is in progress and that part is built.
        """
        return (night < index.tonight) == finished

    frames = index.finished_frames if finished else index.tonight_frames
    frames_by_night: dict[str, list[NightFrame]] = {}
    for frame in frames:
        frames_by_night.setdefault(frame.night, []).append(frame)
    nights = sorted({
        *frames_by_night,
        *filter(wanted, index.ekos_nights),
        *filter(wanted, index.guiding_nights),
    })
    with_background = night_index.subjects_with_background(
        context, None if finished else {frame.target_id for frame in frames}
    )
    entries: list[tuple[str, NightListEntry | None]] = []
    for night in nights:
        try:
            entry = _entry_of(night, frames_by_night.get(night, []), index, with_background)
        except ValueError:
            entry = None
        entries.append((night, entry))
    return entries


def _entry_of(
    night: str, night_frames: list[NightFrame], index: NightIndex, with_background: frozenset[str]
) -> NightListEntry:
    """Make one night's entry.

    A night whose id is not a date fails here, with the `ValueError` that
    `date.fromisoformat` gives.

    Returns
    -------
    entry : `NightListEntry`
        The night's entry.
    """
    return NightListEntry(
        night_id=night,
        night_date=date.fromisoformat(night),
        targets_imaged=sorted({frame.target_id for frame in night_frames}),
        frame_count=len(night_frames),
        has_frame_data=bool(night_frames),
        has_guiding_data=night in index.guiding_nights,
        has_sky_data=any(frame.subject_id in with_background for frame in night_frames),
        has_ekos_record=night in index.ekos_nights,
    )


def query_nights(
    context: ControlContext, limit: int | None = None, offset: int = 0, now: float | None = None
) -> Page[NightListEntry]:
    """List the recorded observing nights, oldest first, as a `Page`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the recorded data (see `build_night_list`).
    limit : `int` or `None`, optional
        The most nights to return. `None` returns every night.
    offset : `int`, optional
        How many nights to skip first.
    now : `float`, optional
        The moment that decides which night is in progress.

    Returns
    -------
    nights : `Page` [`NightListEntry`]
        The nights, oldest first.

    Notes
    -----
    Raises `InvalidArgumentError` if `limit` is below 1 or `offset` is
    below 0.
    """
    _check_page(limit, offset)
    return Page.of(build_night_list(context, now=now), offset=offset, limit=limit)


def _check_page(limit: int | None, offset: int) -> None:
    """Refuse a page size below 1 or an offset below 0.

    Parameters
    ----------
    limit : `int` or `None`
        The most items to return; `None` for every one.
    offset : `int`
        How many items to skip first.

    Raises
    ------
    InvalidArgumentError
        If either value is out of range.
    """
    if limit is not None and limit < 1:
        raise InvalidArgumentError("limit must be at least 1, or None for every match.")
    if offset < 0:
        raise InvalidArgumentError("offset must be 0 or more.")


def get_capture_analysis(context: ControlContext, night: str) -> CaptureSessionAnalysis:
    """Analyse one night's captured frames.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the recorded data.
    night : `str`
        The observing night, such as ``"2026-09-24"``.

    Returns
    -------
    analysis : `CaptureSessionAnalysis`
        The night's capture analysis.

    Raises
    ------
    NotFoundError
        If nothing is recorded for the night, or no equipment is active.
    """
    analysis = night_analysis.capture_night_analysis(context, night)
    if analysis is None:
        raise NotFoundError(
            f"Nothing is recorded for the capture analysis of night {night!r}.", details={"night": night}
        )
    return analysis


def get_guiding_analysis(context: ControlContext, night: str) -> GuidingSessionAnalysis:
    """Analyse one night's guiding.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the recorded data.
    night : `str`
        The observing night, such as ``"2026-09-24"``.

    Returns
    -------
    analysis : `GuidingSessionAnalysis`
        The night's guiding analysis.

    Raises
    ------
    NotFoundError
        If nothing is recorded for the night.
    """
    analysis = night_analysis.guiding_night_analysis(context, night)
    if analysis is None:
        raise NotFoundError(
            f"Nothing is recorded for the guiding analysis of night {night!r}.", details={"night": night}
        )
    return analysis


def get_sky_coverage(context: ControlContext) -> SkyAnalysis:
    """Analyse where on the sky the active equipment has imaged.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the recorded data.

    Returns
    -------
    analysis : `SkyAnalysis`
        The sky-coverage analysis.

    Raises
    ------
    ConflictError
        If no telescope and camera are active.
    """
    analysis = night_analysis.sky_coverage_analysis(context)
    if analysis is None:
        raise ConflictError("No telescope and camera are active, so the sky coverage cannot be analysed.")
    return analysis


def query_guiding_runs(
    context: ControlContext, night: str | None = None, limit: int | None = None, offset: int = 0
) -> Page[GuidingRunSummary]:
    """List the recorded guiding runs, oldest first, as a `Page`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the stored runs.
    night : `str`, optional
        Only the runs of this observing night.
    limit : `int` or `None`, optional
        The most runs to return. `None` returns every run.
    offset : `int`, optional
        How many runs to skip first.

    Returns
    -------
    runs : `Page` [`GuidingRunSummary`]
        The runs, oldest first.
    """
    _check_page(limit, offset)
    return Page.of(night_analysis.guiding_runs(context, night), offset=offset, limit=limit)


def query_ekos_sessions(
    context: ControlContext, night: str | None = None, limit: int | None = None, offset: int = 0
) -> Page[EkosSessionSummary]:
    """List the recorded Ekos sessions, oldest first, as a `Page`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the stored session records.
    night : `str`, optional
        Only the sessions of this observing night.
    limit : `int` or `None`, optional
        The most sessions to return. `None` returns every session.
    offset : `int`, optional
        How many sessions to skip first.

    Returns
    -------
    sessions : `Page` [`EkosSessionSummary`]
        One summary per session: its id, night, times, equipment and how
        many items each section holds.
    """
    _check_page(limit, offset)
    records = sorted(context.butler.get_all("ekos_session_context"), key=lambda record: record.started_at)
    if night:
        records = [record for record in records if record.session_id == night]
    summaries = [EkosSessionSummary.of(record) for record in records]
    return Page.of(summaries, offset=offset, limit=limit)


def get_ekos_session(
    context: ControlContext, ekos_id: str, include: list[str] | None = None
) -> EkosSessionContext | None:
    """Read one recorded Ekos session, with only the sections asked for.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the stored session records.
    ekos_id : `str`
        The session's id, the analyze file's time stamp, such as
        ``"2026-09-23T20-31-48"``.
    include : `list` [`str`], optional
        The sections to keep (names from `EKOS_SECTIONS`). The other
        sections come back empty. `None` keeps none.

    Returns
    -------
    session : `EkosSessionContext` or `None`
        The session, or `None` if none has that id.

    Notes
    -----
    Raises `InvalidArgumentError` if `include` names an unknown section.
    """
    kept = set(check_include(include, EKOS_SECTIONS))
    record = night_analysis.ekos_session_record(context, ekos_id)
    if record is None:
        return None
    cleared: dict[str, Any] = {name: [] for name in EKOS_SECTIONS if name != "equipment" and name not in kept}
    if "equipment" not in kept:
        cleared["equipment"] = None
    return record.model_copy(update=cleared)


def get_pointing_model(context: ControlContext, night: str) -> MountPointingModel:
    """Fit the mount's pointing model to one night's plate solves.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the plate solves and the observer location.
    night : `str`
        The night to fit. A model that mixes several nights' polar
        alignments is not meaningful, so one night is required.

    Returns
    -------
    model : `MountPointingModel`
        The fitted model.

    Raises
    ------
    ConfigurationError
        If no observer location is known.
    """
    if context.observer_location() is None:
        raise ConfigurationError(
            "No observer location is known, so the fit would have to guess the latitude. Set "
            "Observatory.Location in the configuration, or connect the mount, then ask again."
        )
    from wayfindinglib.tasks.control_tasks import pointing_log_ingestion

    return pointing_log_ingestion.compute_pointing_model(context, context.records, night)
