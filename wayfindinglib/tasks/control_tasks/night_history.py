"""Answer questions about past observing nights, in replies of bounded size.

The app records each observing night in several places: the Ekos session
logs, the guiding runs, the guide-log samples, the frame library and the
plate solves. The analyses in `night_analysis` turn these into findings.
This module is the front door behind `review.query`. It checks
the arguments for a ``kind`` of question, picks the right analysis,
shrinks the answer to plain data, and guarantees that the reply fits under
a size limit.

The limit matters because a caller that relays the reply as text may cut it
at 40,000 characters, in the middle of the data. Several of the methods can
produce more than that: a single Ekos session can hold hundreds of mount
positions, and a list of every guiding run is about 40,000 characters
today. Here the reply is measured, and if it is too big it is shrunk in
steps until it fits, with a flag that says it was shrunk.

Nothing here writes. For exact behavior, read the code.
"""

from __future__ import annotations

import copy
import json
import time
from datetime import date
from typing import TYPE_CHECKING, Any

from astrometricslib.exceptions import (
    ConfigurationError,
    ConflictError,
    InvalidArgumentError,
    NotFoundError,
    StorageError,
)
from astrometricslib.reports import observing_night_id
from wayfindinglib.analytics import night_index
from wayfindinglib.analytics.data_versions import (
    nights_context_signs,
    nights_data_versions,
    nights_index_version,
)
from wayfindinglib.analytics.night_index import NightFrame, NightIndex, build_night_index
from wayfindinglib.models.session.night_list import NightListEntry
from wayfindinglib.tasks.control_tasks import night_analysis

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext

KINDS = (
    "capture",
    "guiding",
    "sky_coverage",
    "recurring_issues",
    "ekos_sessions",
    "guiding_runs",
    "pointing_model",
    "alignment",
)
"""The kinds of question `build_night_history` answers."""

KIND_ARGUMENTS = {
    "capture": ("session_id",),
    "guiding": ("session_id",),
    "sky_coverage": (),
    "recurring_issues": (),
    "ekos_sessions": ("session_id", "ekos_file_id", "include"),
    "guiding_runs": ("session_id",),
    "pointing_model": ("session_id",),
    "alignment": ("session_id",),
}
"""The optional arguments each kind uses. Any other one is refused."""

TYPED_KIND_ARGUMENTS = {
    "nights": (),
    "target_changes": ("session_id",),
    "night_quality": ("session_id",),
}
"""The kinds that answer with a typed list instead of a dictionary, and the
optional arguments each uses. `ObservationReview.query` answers these itself
(see `refuse_unused_arguments`). They are kept apart from ``KINDS`` because
their replies are lists of models, which `fit_to_budget` does not shrink."""

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

DEFAULT_LIMIT = 10
"""How many nights, runs or sessions an answer covers by default."""

MAXIMUM_LIMIT = 50
"""Most nights, runs, sessions or list items one answer covers."""

MAXIMUM_REPLY_CHARACTERS = 30_000
"""Largest reply, measured as indented JSON. The server cuts at 40,000."""

SHRINK_LEVELS = ((50, 500, 10), (20, 300, 7), (8, 200, 5), (3, 120, 3))
"""Steps tried in order when a reply is too big: most list items kept,
longest text kept, deepest nesting kept."""


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


def reply_size(payload: Any) -> int:
    """Measure a reply as the number of characters of its JSON text.

    Parameters
    ----------
    payload : `Any`
        The reply.

    Returns
    -------
    size : `int`
        Its length in characters as indented JSON.
    """
    return len(json.dumps(payload, indent=2, default=str))


def shrink(value: Any, maximum_items: int, maximum_text: int, maximum_depth: int) -> Any:
    """Make nested data smaller by cutting lists, text and depth.

    Parameters
    ----------
    value : `Any`
        Plain data.
    maximum_items : `int`
        Most items kept in any list. A note says how many were left out.
    maximum_text : `int`
        Longest string kept, in characters.
    maximum_depth : `int`
        Deepest nesting kept. Deeper dicts become a note.

    Returns
    -------
    smaller : `Any`
        The cut data.
    """
    if isinstance(value, dict):
        if maximum_depth <= 0:
            return f"<{len(value)} fields left out>"
        return {
            key: shrink(item, maximum_items, maximum_text, maximum_depth - 1) for key, item in value.items()
        }
    if isinstance(value, list):
        kept = [
            shrink(item, maximum_items, maximum_text, maximum_depth - 1) for item in value[:maximum_items]
        ]
        if len(value) > maximum_items:
            kept.append(f"<{len(value) - maximum_items} more items left out>")
        return kept
    if isinstance(value, str) and len(value) > maximum_text:
        return value[: maximum_text - 1] + "…"
    return value


def trim_oldest_rows(payload: dict[str, Any], budget: int) -> dict[str, Any] | None:
    """Make a reply fit by dropping the oldest rows of its longest list.

    Lists in a reply run oldest first, so dropping from the front keeps the
    newest rows complete. This is tried before the blunter `shrink`.

    Parameters
    ----------
    payload : `dict` [`str`, `Any`]
        The reply.
    budget : `int`
        Largest allowed size in characters.

    Returns
    -------
    reply : `dict` [`str`, `Any`] or `None`
        A copy with the longest top-level list cut to its newest rows, with
        ``truncated`` set and ``shown`` updated, or `None` if cutting rows
        cannot make the reply fit.
    """
    lists = {key: value for key, value in payload.items() if isinstance(value, list) and len(value) > 1}
    if not lists:
        return None
    key = max(lists, key=lambda name: len(lists[name]))
    rows = lists[key]
    for keep in range(len(rows) - 1, 0, -1):
        candidate = {**payload, key: rows[-keep:], "truncated": True}
        candidate["truncation_note"] = (
            f"The answer was too large, so only the newest {keep} of {len(rows)} rows are shown. "
            "Ask for a smaller limit or a single night."
        )
        if "shown" in candidate:
            candidate["shown"] = keep
        if reply_size(candidate) <= budget:
            return candidate
    return None


def fit_to_budget(payload: dict[str, Any], budget: int = MAXIMUM_REPLY_CHARACTERS) -> dict[str, Any]:
    """Make a reply fit under a size limit.

    Parameters
    ----------
    payload : `dict` [`str`, `Any`]
        The reply.
    budget : `int`, optional
        Largest allowed size in characters.

    Returns
    -------
    reply : `dict` [`str`, `Any`]
        The reply unchanged if it fits. Otherwise the least-shrunk version
        that fits, with ``truncated`` set and a note. If nothing fits, only
        the kind and the names of the top-level fields.
    """
    if reply_size(payload) <= budget:
        return payload
    trimmed = trim_oldest_rows(payload, budget)
    if trimmed is not None:
        return trimmed
    note = (
        "The answer was too large, so lists and long text were cut. Ask a narrower question: "
        "one night, fewer items, or fewer sections."
    )
    for items, text, depth in SHRINK_LEVELS:
        candidate = shrink(payload, items, text, depth)
        if isinstance(candidate, dict):
            candidate["truncated"] = True
            candidate["truncation_note"] = note
            if reply_size(candidate) <= budget:
                return candidate
    return {
        "kind": payload.get("kind"),
        "truncated": True,
        "truncation_note": note,
        "fields": sorted(payload),
    }


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


def refuse_unused_arguments(
    kind: str,
    used: tuple[str, ...],
    session_id: str | None = None,
    ekos_file_id: str | None = None,
    include: list[str] | None = None,
) -> None:
    """Refuse an optional argument that a kind of question does not use.

    Parameters
    ----------
    kind : `str`
        The kind of question, for the message.
    used : `tuple` [`str`, ...]
        The optional arguments the kind uses.
    session_id, ekos_file_id, include
        The caller's optional arguments. An empty one counts as not given.

    Raises
    ------
    InvalidArgumentError
        If an argument is given that `kind` does not use.
    """
    given = {"session_id": session_id, "ekos_file_id": ekos_file_id, "include": include}
    unused = [name for name, value in given.items() if value and name not in used]
    if unused:
        raise InvalidArgumentError(
            f"kind={kind!r} does not use {', '.join(unused)}. It uses: "
            f"{', '.join(used) or 'no optional arguments'}."
        )


def clamp_limit(limit: int) -> int:
    """Bring a caller's `limit` into the range of 1 to ``MAXIMUM_LIMIT``.

    Parameters
    ----------
    limit : `int`
        The caller's value.

    Returns
    -------
    limit : `int`
        The value, held between 1 and ``MAXIMUM_LIMIT``.
    """
    return max(1, min(int(limit), MAXIMUM_LIMIT))


def build_night_list(
    context: ControlContext, limit: int = DEFAULT_LIMIT, now: float | None = None
) -> list[NightListEntry]:
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
    limit : `int`, optional
        How many of the most recent nights to list.
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
    limit = clamp_limit(limit)
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
    for night_id, entry in sorted([*finished, *in_progress], key=lambda pair: pair[0])[-limit:]:
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


def build_night_history(
    context: ControlContext,
    kind: str,
    session_id: str | None = None,
    ekos_file_id: str | None = None,
    include: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Answer one question about past observing nights.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the recorded data.
    kind : `str`
        One of ``KINDS``. ``KIND_ARGUMENTS`` lists the optional arguments
        each kind uses.
    session_id : `str`, optional
        An observing night, named for the local date it began, such as
        ``"2026-09-24"``.
    ekos_file_id : `str`, optional
        For ``kind="ekos_sessions"``, one session's id.
    include : `list` [`str`], optional
        For one Ekos session, the sections to return (``EKOS_SECTIONS``).
    limit : `int`, optional
        How many of the most recent nights, runs or sessions to cover, and
        how many items of each Ekos section. From 1 to 50.

    Returns
    -------
    reply : `dict` [`str`, `Any`]
        The answer under a key named for the kind. Always under
        ``MAXIMUM_REPLY_CHARACTERS``; if it had to be cut, ``truncated``
        is set.

    Raises
    ------
    InvalidArgumentError
        If `kind` is unknown, or an argument is given that `kind` does not
        use.

    Notes
    -----
    The helpers also raise: `NotFoundError` when nothing is recorded for
    the night, Ekos session or alignment night asked about,
    `InvalidArgumentError` when a required argument is missing,
    `ConflictError` for ``"sky_coverage"`` when no telescope and camera
    are active, and `ConfigurationError` for
    ``"pointing_model"`` when no observer location is known.
    """
    if kind not in KINDS:
        raise InvalidArgumentError(f"kind must be one of: {', '.join(KINDS)}.")
    refuse_unused_arguments(kind, KIND_ARGUMENTS[kind], session_id, ekos_file_id, include)
    limit = clamp_limit(limit)
    reply = _answer(context, kind, session_id, ekos_file_id, include, limit)
    return fit_to_budget(reply)


def _answer(
    context: ControlContext,
    kind: str,
    session_id: str | None,
    ekos_file_id: str | None,
    include: list[str] | None,
    limit: int,
) -> dict[str, Any]:
    """Build the reply for a valid kind, before the size check.

    Parameters
    ----------
    context, kind, session_id, ekos_file_id, include, limit
        As in `build_night_history`; ``limit`` is already in range.

    Returns
    -------
    reply : `dict` [`str`, `Any`]
        The answer.

    Raises
    ------
    ConflictError
        If ``kind="sky_coverage"`` and no telescope and camera are active.
    """
    if kind in ("capture", "guiding"):
        return _capture_or_guiding(context, kind, session_id, limit)
    if kind == "sky_coverage":
        analysis = night_analysis.sky_coverage_analysis(context)
        if analysis is None:
            raise ConflictError("No telescope and camera are active, so the sky coverage cannot be analysed.")
        return {"kind": kind, "analysis": to_plain(analysis)}
    if kind == "recurring_issues":
        issues = night_analysis.recurring_issues(context, latest_nights=limit)
        return {"kind": kind, "nights_covered": f"the latest {limit}", "issues": to_plain(issues)}
    if kind == "guiding_runs":
        runs = night_analysis.guiding_runs(context, session_id)
        shown = runs[-limit:]
        return {"kind": kind, "total": len(runs), "shown": len(shown), "runs": to_plain(shown)}
    if kind == "ekos_sessions":
        return _ekos(context, session_id, ekos_file_id, include, limit)
    if kind == "alignment":
        from wayfindinglib.tasks.control_tasks import alignment_history

        if session_id:
            return alignment_history.alignment_night(context, session_id)
        return alignment_history.alignment_nights(context, limit)
    return _pointing_model(context, session_id)


def _capture_or_guiding(
    context: ControlContext, kind: str, session_id: str | None, limit: int
) -> dict[str, Any]:
    """Answer a capture or guiding question: one night, or recent nights.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the recorded data.
    kind : `str`
        ``"capture"`` or ``"guiding"``.
    session_id : `str` or `None`
        A night for the full analysis, or `None` for one summary row per
        recent night.
    limit : `int`
        How many recent nights the summary covers.

    Returns
    -------
    reply : `dict` [`str`, `Any`]
        The analysis or the summary rows.

    Raises
    ------
    NotFoundError
        If nothing is recorded for `session_id`.
    """
    if session_id:
        analyze = (
            night_analysis.capture_night_analysis
            if kind == "capture"
            else night_analysis.guiding_night_analysis
        )
        analysis = analyze(context, session_id)
        if analysis is None:
            raise NotFoundError(
                f"Nothing is recorded for the {kind} analysis of night {session_id!r}.",
                details={"session_id": session_id},
            )
        return {"kind": kind, "session_id": session_id, "analysis": to_plain(analysis)}
    summarize = (
        night_analysis.capture_night_summaries
        if kind == "capture"
        else night_analysis.guiding_night_summaries
    )
    rows = summarize(context, latest_nights=limit)
    return {"kind": kind, "nights_covered": f"the latest {limit}", "nights": to_plain(rows)}


def _ekos(
    context: ControlContext,
    session_id: str | None,
    ekos_file_id: str | None,
    include: list[str] | None,
    limit: int,
) -> dict[str, Any]:
    """Answer an Ekos question: a list of sessions, or one session's sections.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the recorded sessions.
    session_id : `str` or `None`
        Keep only the sessions of this night when listing.
    ekos_file_id : `str` or `None`
        One session to read in detail.
    include : `list` [`str`] or `None`
        Sections of the session to return. `None` returns only the overview.
    limit : `int`
        How many sessions to list, or items to keep per section.

    Returns
    -------
    reply : `dict` [`str`, `Any`]
        The list, the overview, or the chosen sections.

    Raises
    ------
    InvalidArgumentError
        If `include` is given without `ekos_file_id`, or names an unknown
        section.
    NotFoundError
        If no Ekos session has the id `ekos_file_id`.
    """
    if ekos_file_id:
        record = night_analysis.ekos_session_record(context, ekos_file_id)
        if record is None:
            raise NotFoundError(
                f"No Ekos session with id {ekos_file_id!r}. List the sessions to find an id.",
                details={"ekos_file_id": ekos_file_id},
            )
        unknown = [name for name in (include or []) if name not in EKOS_SECTIONS]
        if unknown:
            raise InvalidArgumentError(
                f"Unknown section(s) {unknown}. Choose from: {', '.join(EKOS_SECTIONS)}."
            )
        if not include:
            return {"kind": "ekos_sessions", "session": {"overview": ekos_overview(record)}}
        return {"kind": "ekos_sessions", "session": ekos_sections(record, include, limit)}
    if include:
        raise InvalidArgumentError("include needs an ekos_file_id: it picks sections of one session.")
    summaries = night_analysis.ekos_session_summaries(context)
    if session_id:
        summaries = [row for row in summaries if row["sessionId"] == session_id]
    shown = summaries[-limit:]
    return {"kind": "ekos_sessions", "total": len(summaries), "shown": len(shown), "sessions": shown}


def _pointing_model(context: ControlContext, session_id: str | None) -> dict[str, Any]:
    """Answer a pointing-model question for one night.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the plate solves and the observer location.
    session_id : `str` or `None`
        The night to fit. Required: a fit that mixes several nights'
        polar alignments is not meaningful.

    Returns
    -------
    reply : `dict` [`str`, `Any`]
        The fitted model.

    Raises
    ------
    InvalidArgumentError
        If `session_id` is not given.
    ConfigurationError
        If no observer location is known.
    """
    if not session_id:
        raise InvalidArgumentError(
            "kind='pointing_model' needs a session_id, because a model that mixes "
            "several nights is not meaningful."
        )
    if context.observer_location() is None:
        raise ConfigurationError(
            "No observer location is known, so the fit would have to guess the latitude. Set "
            "Observatory.Location in the configuration, or connect the mount, then ask again."
        )
    from wayfindinglib.tasks.control_tasks import pointing_log_ingestion

    model = pointing_log_ingestion.compute_pointing_model(context, context.records, session_id)
    return {"kind": "pointing_model", "session_id": session_id, "model": to_plain(model)}
