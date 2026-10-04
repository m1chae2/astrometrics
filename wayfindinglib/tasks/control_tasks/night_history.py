"""Answer questions about past observing nights, in replies of bounded size.

The app records each observing night in several places: the Ekos session
logs, the guiding runs, the guide-log samples, the frame library and the
plate solves. The analysis methods on `ObservatoryControl` turn these into
findings. This module is the one front door for an AI client. It picks the
right method for a ``kind`` of question, shrinks the answer to plain data,
and guarantees that the reply fits under a size limit.

The limit matters because the MCP server cuts any reply at 40,000
characters, in the middle of the data. Several of the methods can produce
more than that: a single Ekos session can hold hundreds of mount positions,
and a list of every guiding run is about 40,000 characters today. Here the
reply is measured, and if it is too big it is shrunk in steps until it fits,
with a flag that says it was shrunk.

Nothing here writes. For exact behavior, read the code.
"""

import json
from typing import Any

KINDS = (
    "capture",
    "guiding",
    "sky_coverage",
    "recurring_issues",
    "ekos_sessions",
    "guiding_runs",
    "pointing_model",
)
"""The kinds of question `build_night_history` answers."""

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
    """Measure a reply the way the MCP server will send it.

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


def build_night_history(
    observatory: Any,
    kind: str,
    session_id: str | None = None,
    ekos_file_id: str | None = None,
    include: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Answer one question about past observing nights.

    Parameters
    ----------
    observatory : `ObservatoryControl`
        Supplies the analysis methods and the recorded data.
    kind : `str`
        One of ``KINDS``.
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
        The answer under a key named for the kind, or ``{"error": ...}``.
        Always under ``MAXIMUM_REPLY_CHARACTERS``; if it had to be cut,
        ``truncated`` is set.
    """
    if kind not in KINDS:
        return {"error": f"kind must be one of: {', '.join(KINDS)}."}
    limit = max(1, min(int(limit), MAXIMUM_LIMIT))
    reply = _answer(observatory, kind, session_id, ekos_file_id, include, limit)
    return fit_to_budget(reply)


def _answer(
    observatory: Any,
    kind: str,
    session_id: str | None,
    ekos_file_id: str | None,
    include: list[str] | None,
    limit: int,
) -> dict[str, Any]:
    """Build the reply for a valid kind, before the size check.

    Parameters
    ----------
    observatory, kind, session_id, ekos_file_id, include, limit
        As in `build_night_history`; ``limit`` is already in range.

    Returns
    -------
    reply : `dict` [`str`, `Any`]
        The answer, or ``{"error": ...}``.
    """
    if kind in ("capture", "guiding"):
        return _capture_or_guiding(observatory, kind, session_id, limit)
    if kind == "sky_coverage":
        analysis = observatory.analyze_sky_coverage()
        if analysis is None:
            return {"error": "No telescope and camera are active, so the sky coverage cannot be analysed."}
        return {"kind": kind, "analysis": to_plain(analysis)}
    if kind == "recurring_issues":
        issues = observatory.summarize_recurring_issues(latest_nights=limit)
        return {"kind": kind, "nights_covered": f"the latest {limit}", "issues": to_plain(issues)}
    if kind == "guiding_runs":
        runs = observatory.list_guiding_runs(session_id)
        shown = runs[-limit:]
        return {"kind": kind, "total": len(runs), "shown": len(shown), "runs": to_plain(shown)}
    if kind == "ekos_sessions":
        return _ekos(observatory, session_id, ekos_file_id, include, limit)
    return _pointing_model(observatory, session_id)


def _capture_or_guiding(observatory: Any, kind: str, session_id: str | None, limit: int) -> dict[str, Any]:
    """Answer a capture or guiding question: one night, or recent nights.

    Parameters
    ----------
    observatory : `ObservatoryControl`
        The analysis methods.
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
    """
    if session_id:
        analyze = (
            observatory.analyze_capture_session if kind == "capture" else observatory.analyze_guiding_session
        )
        analysis = analyze(session_id)
        if analysis is None:
            return {"error": f"Nothing is recorded for the {kind} analysis of night {session_id!r}."}
        return {"kind": kind, "session_id": session_id, "analysis": to_plain(analysis)}
    summarize = (
        observatory.summarize_capture_sessions
        if kind == "capture"
        else observatory.summarize_guiding_sessions
    )
    rows = summarize(latest_nights=limit)
    return {"kind": kind, "nights_covered": f"the latest {limit}", "nights": to_plain(rows)}


def _ekos(
    observatory: Any, session_id: str | None, ekos_file_id: str | None, include: list[str] | None, limit: int
) -> dict[str, Any]:
    """Answer an Ekos question: a list of sessions, or one session's sections.

    Parameters
    ----------
    observatory : `ObservatoryControl`
        The recorded sessions.
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
    """
    if ekos_file_id:
        context = observatory.get_ekos_session_context(ekos_file_id)
        if context is None:
            return {"error": f"No Ekos session with id {ekos_file_id!r}. List the sessions to find an id."}
        unknown = [name for name in (include or []) if name not in EKOS_SECTIONS]
        if unknown:
            return {"error": f"Unknown section(s) {unknown}. Choose from: {', '.join(EKOS_SECTIONS)}."}
        if not include:
            return {"kind": "ekos_sessions", "session": {"overview": ekos_overview(context)}}
        return {"kind": "ekos_sessions", "session": ekos_sections(context, include, limit)}
    summaries = observatory.list_ekos_session_summaries()
    if session_id:
        summaries = [row for row in summaries if row["sessionId"] == session_id]
    shown = summaries[-limit:]
    return {"kind": "ekos_sessions", "total": len(summaries), "shown": len(shown), "sessions": shown}


def _pointing_model(observatory: Any, session_id: str | None) -> dict[str, Any]:
    """Answer a pointing-model question for one night.

    Parameters
    ----------
    observatory : `ObservatoryControl`
        Supplies the fit and the observer location.
    session_id : `str` or `None`
        The night to fit. Required: a fit that mixes several nights'
        polar alignments is not meaningful.

    Returns
    -------
    reply : `dict` [`str`, `Any`]
        The fitted model, or an error.
    """
    if not session_id:
        return {
            "error": "kind='pointing_model' needs a session_id, because a model that mixes "
            "several nights is not meaningful."
        }
    if observatory.get_observer_location() is None:
        return {
            "error": "No observer location is known, so the fit would have to guess the latitude. Set "
            "Observatory.Location in the configuration, or connect the mount, then ask again."
        }
    model = observatory.get_pointing_model(session_id)
    return {"kind": "pointing_model", "session_id": session_id, "model": to_plain(model)}
