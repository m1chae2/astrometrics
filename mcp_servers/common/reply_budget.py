"""Purpose: Keep the replies of the MCP tools short enough to send.

Description: The MCP tools cut a reply longer than 40,000 characters, and a
cut reply is no longer valid JSON. Many replies grow with the library: a
target's frames, a star's light curve and spectrum, a night's guiding
samples, the target report, tonight's ranking and the night review. The
library itself returns them in full, to every caller (the desktop app shows
full reports). The MCP layer calls `shape_reply` on a tool's reply before it
sends it. This module, which belongs to the MCP servers and not to the
library, holds what that needs, in one place:

* the limit, and the budget a reply must meet (the limit less 10 percent
  headroom);
* a measure of a reply made the way the MCP tool registry makes its own
  (``model_dump()`` and compact ``json.dumps`` with ``default=str``), so a
  reply that fits here is not cut there;
* ``fit_reply``, which applies a reply's own shrinking steps until it fits;
* the way to cut the evidence lists of statements;
* ``fit_reply_to_budget``, which picks the shrinking steps for the three
  quality reports;
* `HEAVY_LISTS`, the one table of heavy lists (a model's field and how many
  items it keeps), and ``shape_reply``, the entry point the tool registry
  calls: it fits the quality reports, then cuts the heavy lists until the
  reply fits, and gives back a note for each list it cut that says what was
  left out and how to ask for it.

When cutting evidence is not enough, the last step of a target report or a
night review empties the plain message of every statement. The technical
message says the same thing, and a reader that finds an empty plain message
uses it. The desktop app never meets this step, because only the MCP layer
calls ``fit_reply_to_budget``.

Each statement lists its evidence with the most extreme reference first (the
reference furthest beyond the statement's limit; see
``evidence_order.py``), so cutting a list from the end keeps the references
that matter most. Cutting never removes a statement, and a statement that
lost references says how many (``evidence_omitted_count``), so the loss is
visible. The rows stay readable with ``QualityRecords.query``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel

TARGET_REPORT_FIELDS = frozenset({"statements", "context", "context_omitted_reason"})
"""Field names that mark a target dataset report (a `TargetDatasetReport`)."""

TONIGHT_RANKING_FIELDS = frozenset({"lists", "notes"})
"""Field names that mark tonight's ranking (a `TonightRanking`)."""

NIGHT_QUALITY_FIELDS = frozenset({"targets", "night_statements", "notes"})
"""Field names that mark a night review (a `NightQuality`)."""

MAXIMUM_REPLY_CHARACTERS = 40_000
"""The reply limit of the MCP tools (`MAXIMUM_REPLY_BYTES` in the MCP tool
registry). A test compares the two."""

REPLY_HEADROOM_FRACTION = 0.10
"""Share of the limit kept free, because the registry may add a note and the
two measures can differ by a few characters. Design estimate, register row
WAY-D77."""

EVIDENCE_STEPS = (20, 5, 1)
"""References one statement keeps, tried in turn, when a reply is too long.
Design estimate, register row WAY-D77."""


def reply_budget(limit: int = MAXIMUM_REPLY_CHARACTERS) -> int:
    """Give the number of characters a reply may have.

    Parameters
    ----------
    limit : `int`, optional
        The reply limit.

    Returns
    -------
    budget : `int`
        The limit less the headroom.
    """
    return int(limit * (1.0 - REPLY_HEADROOM_FRACTION))


def serialize_reply(value: Any) -> Any:
    """Turn models and records into plain JSON-ready values, recursively.

    The MCP tool registry sends what this gives.

    Parameters
    ----------
    value : `Any`
        Value to convert.

    Returns
    -------
    serialized : `Any`
        The value as plain dicts, lists and scalars: a value with a
        ``serialize`` method gives its result, then ``model_dump()`` (field
        names, Python values), then ``to_dict()``.
    """
    if callable(getattr(value, "serialize", None)):
        return value.serialize()
    if callable(getattr(value, "model_dump", None)):
        return value.model_dump()
    if callable(getattr(value, "to_dict", None)):
        return value.to_dict()
    if isinstance(value, list | tuple):
        return [serialize_reply(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize_reply(item) for key, item in value.items()}
    return value


def reply_characters(model: Any) -> int:
    """Count the characters of a reply's compact JSON.

    The text is made the way the MCP tool registry makes it:
    `serialize_reply` (for a model, ``model_dump()``: field names, Python
    values) and compact ``json.dumps`` with ``default=str``.

    Parameters
    ----------
    model : `Any`
        The reply.

    Returns
    -------
    characters : `int`
        The length of the text an MCP tool would send.
    """
    return len(json.dumps(serialize_reply(model), separators=(",", ":"), default=str))


def fit_reply[ReplyT: BaseModel](
    reply: ReplyT, step: Callable[[ReplyT, int], ReplyT | None], budget: int | None = None
) -> ReplyT:
    """Shrink a reply until it fits the budget.

    Parameters
    ----------
    reply : `BaseModel`
        The finished reply.
    step : `Callable`
        Called as ``step(current, round_number)`` with round numbers 0, 1, 2
        and so on. It returns a smaller copy, whose notes already say what
        was cut (so the measure includes them), or ``None`` when it has
        nothing left to cut.
    budget : `int`, optional
        The most characters the reply may have. `reply_budget` when omitted.

    Returns
    -------
    reply : `BaseModel`
        The same reply when it fits. Otherwise the smallest copy the steps
        could make, which fits unless the steps ran out first.
    """
    limit = reply_budget() if budget is None else budget
    current = reply
    round_number = 0
    while reply_characters(current) > limit:
        smaller = step(current, round_number)
        if smaller is None:
            break
        current = smaller
        round_number += 1
    return current


def limit_statement_evidence(statements: Sequence[BaseModel], keep: int) -> list[BaseModel]:
    """Cut each statement's evidence list to its first ``keep`` references.

    Parameters
    ----------
    statements : `Sequence` [`TargetStatement`]
        The statements, in order.
    keep : `int`
        The most references one statement may keep.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        Copies in the same order. A statement that lost references counts
        them in ``evidence_omitted_count``.
    """
    return [
        statement.model_copy(
            update={
                "evidence": statement.evidence[:keep],
                "evidence_omitted_count": statement.evidence_omitted_count + len(statement.evidence) - keep,
            }
        )
        if len(statement.evidence) > keep
        else statement
        for statement in statements
    ]


def drop_plain_messages(statements: Sequence[BaseModel]) -> list[BaseModel]:
    """Empty each statement's plain message, the last way to shorten a reply.

    Parameters
    ----------
    statements : `Sequence` [`TargetStatement`]
        The statements, in order.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        Copies in the same order whose ``plain_message`` is empty. A reader
        then falls back to the technical ``message``.
    """
    return [
        statement.model_copy(update={"plain_message": ""}) if statement.plain_message else statement
        for statement in statements
    ]


def _fit_target_report(report: BaseModel, limit: int) -> BaseModel:
    """Shrink a target report until its JSON fits the budget.

    Two things can go, in this order. First the context section, which is only
    a convenience: the statements cite the rows they used, so the report
    says why the section is empty. Then the long evidence lists, cut to
    ``EVIDENCE_STEPS`` references per statement in turn, each statement
    counting what it lost. Last, the plain messages are emptied (see
    `drop_plain_messages`). The statements, their order and the counts of the
    warnings never change.

    Parameters
    ----------
    report : `TargetDatasetReport`
        The full report.
    limit : `int`
        The most characters its JSON may have.

    Returns
    -------
    report : `TargetDatasetReport`
        The same report when it fits. Otherwise a smaller copy; when even the
        smallest step does not fit, that smallest copy.
    """

    def shrink(current: BaseModel, round_number: int) -> BaseModel | None:
        """Make the report one step smaller.

        Parameters
        ----------
        current : `TargetDatasetReport`
            The report so far.
        round_number : `int`
            0 drops the context; later rounds cut the evidence lists, and the
            round after them empties the plain messages.

        Returns
        -------
        smaller : `TargetDatasetReport` or `None`
            The smaller report, or ``None`` when nothing is left to cut.
        """
        if round_number == 0:
            return current.model_copy(
                update={
                    "context": [],
                    "context_omitted_reason": (
                        "The context readings were left out so the report fits the reply budget of "
                        f"{limit:,} characters. The statements still cite the rows they used; read "
                        "any metric with QualityRecords.query."
                    ),
                }
            )
        if round_number == len(EVIDENCE_STEPS) + 1:
            return current.model_copy(update={"statements": drop_plain_messages(current.statements)})
        if round_number > len(EVIDENCE_STEPS) + 1:
            return None
        keep = EVIDENCE_STEPS[round_number - 1]
        return current.model_copy(update={"statements": limit_statement_evidence(current.statements, keep)})

    return fit_reply(report, shrink, limit)


def _fit_tonight_ranking(ranking: BaseModel, limit: int) -> BaseModel:
    """Leave out the lowest candidates until the ranking fits the budget.

    Parameters
    ----------
    ranking : `TonightRanking`
        The full ranking, with all its notes already in it.
    limit : `int`
        The most characters its JSON may have.

    Returns
    -------
    ranking : `TonightRanking`
        The same ranking when it fits. Otherwise a copy that drops the last
        candidate of the longest list again and again (the unranked, then the
        stopped, then the lowest gains), with a note for each list that says
        how many targets were left out. The notes are part of each copy, so
        the length that is checked includes them.
    """
    original_counts = {ranked_list.use: len(ranked_list.candidates) for ranked_list in ranking.lists}

    def shrink(current: BaseModel, _round_number: int) -> BaseModel | None:
        """Drop the lowest candidates of the longest list.

        Parameters
        ----------
        current : `TonightRanking`
            The ranking so far.
        _round_number : `int`
            Not used.

        Returns
        -------
        smaller : `TonightRanking` or `None`
            The smaller ranking, or ``None`` when every list is empty.
        """
        longest = max(current.lists, key=lambda ranked_list: len(ranked_list.candidates), default=None)
        if longest is None or not longest.candidates:
            return None
        # Trim several at a time: the cut is large when the library is large.
        cut = max(1, len(longest.candidates) // 10)
        lists = [
            ranked_list.model_copy(update={"candidates": ranked_list.candidates[:-cut]})
            if ranked_list.use == longest.use
            else ranked_list
            for ranked_list in current.lists
        ]
        notes = [
            f"{original_counts[ranked_list.use] - len(ranked_list.candidates)} {ranked_list.use.value} "
            f"targets ranked lowest were left out to fit the reply budget of {limit:,} characters."
            for ranked_list in lists
            if original_counts[ranked_list.use] > len(ranked_list.candidates)
        ]
        return ranking.model_copy(update={"lists": lists, "notes": [*ranking.notes, *notes]})

    return fit_reply(ranking, shrink, limit)


def _fit_night_quality(quality: BaseModel, limit: int) -> BaseModel:
    """Shorten the evidence lists until the night review fits the budget.

    Parameters
    ----------
    quality : `NightQuality`
        The full review.
    limit : `int`
        The most characters its JSON may have.

    Returns
    -------
    quality : `NightQuality`
        The same review when it fits. Otherwise a copy whose statements keep
        fewer references each (``EVIDENCE_STEPS``, in turn) and count what
        they lost; a note says it was done. Last, the plain messages are
        emptied. No statement is removed.
    """
    note = (
        f"The evidence lists of the statements were cut to fit the reply budget of {limit:,} characters. "
        "Read the rows with QualityRecords.query."
    )

    def shrink(current: BaseModel, round_number: int) -> BaseModel | None:
        """Cut the evidence lists one step shorter.

        Parameters
        ----------
        current : `NightQuality`
            The review so far.
        round_number : `int`
            Which step of ``EVIDENCE_STEPS`` to apply.

        Returns
        -------
        smaller : `NightQuality` or `None`
            The shorter review, or ``None`` when no step is left.
        """
        if round_number == len(EVIDENCE_STEPS):
            return current.model_copy(
                update={
                    "targets": [
                        entry.model_copy(update={"statements": drop_plain_messages(entry.statements)})
                        for entry in current.targets
                    ],
                    "night_statements": drop_plain_messages(current.night_statements),
                }
            )
        if round_number > len(EVIDENCE_STEPS):
            return None
        keep = EVIDENCE_STEPS[round_number]
        return current.model_copy(
            update={
                "targets": [
                    entry.model_copy(update={"statements": limit_statement_evidence(entry.statements, keep)})
                    for entry in current.targets
                ],
                "night_statements": limit_statement_evidence(current.night_statements, keep),
                "notes": current.notes if note in current.notes else [*current.notes, note],
            }
        )

    return fit_reply(quality, shrink, limit)


def fit_reply_to_budget[ReplyT](reply: ReplyT, budget: int | None = None) -> ReplyT:
    """Shrink a quality reply to fit the MCP reply budget.

    The MCP layer calls this on a tool's reply before it sends it. The
    library never calls it, so every other caller gets the full reply.

    Parameters
    ----------
    reply : `Any`
        What a tool returned. A `TargetDatasetReport`, a `TonightRanking`
        or a `NightQuality` is shrunk when it is too long; any other value is
        returned as it is.
    budget : `int`, optional
        The most characters the reply's JSON may have, measured the way the
        MCP tool registry measures a reply. `reply_budget` when omitted.

    Returns
    -------
    reply : `Any`
        The same reply when it fits or is of another kind. Otherwise a
        smaller copy. A target report loses its context section first, then
        evidence references from the end of each statement's list (the least
        extreme references, because each list starts with the most extreme).
        A ranking loses its lowest candidates. A night review loses evidence
        references from the end of each list. No statement is ever removed,
        and each cut is counted or noted in the reply.
    """
    limit = reply_budget() if budget is None else budget
    if not isinstance(reply, BaseModel):
        return reply
    field_names = frozenset(type(reply).model_fields)
    # Tell the three kinds apart by their field names, because the library
    # keeps these classes private.
    if TARGET_REPORT_FIELDS <= field_names:
        return cast(ReplyT, _fit_target_report(reply, limit))
    if NIGHT_QUALITY_FIELDS <= field_names:
        return cast(ReplyT, _fit_night_quality(reply, limit))
    if TONIGHT_RANKING_FIELDS <= field_names:
        return cast(ReplyT, _fit_tonight_ranking(reply, limit))
    return reply


@dataclass(frozen=True)
class HeavyList:
    """One list in a reply that can grow large, and how to cut it.

    Attributes
    ----------
    model : `str`
        The class name of the model that holds the list, such as
        ``"Target"``. The classes are private, so they are named, not
        imported. A generic model is named without its parameter (``"Page"``).
    fields : `tuple` [`str`, ...]
        The list fields, cut together to the same length. Several fields
        are parallel lists of one series (a light curve's times and fluxes).
    keep : `int`
        How many items the first cut keeps. Each later cut halves it. 0
        drops the list at once: a part of a spectrum or light curve would
        mislead.
    what : `str`
        What the items are, for the note, such as ``"frames"``.
    how_to_ask : `str`
        How a client asks for what was left out, for the note.
    """

    model: str
    fields: tuple[str, ...]
    keep: int
    what: str
    how_to_ask: str


_LIGHT_CURVE_FIELDS = (
    "timestamps",
    "fluxes",
    "fluxes_normalized",
    "fluxes_detrended",
    "airmasses",
    "magnitudes",
    "is_saturated",
    "flux_errors",
    "fluxes_normalized_errors",
    "fluxes_detrended_errors",
    "time_bjd_tdb",
)
_SPECTRUM_FIELDS = (
    "wavelengths_angstrom",
    "intensities",
    "intensity_errors",
    "quantum_efficiency_corrected_intensities",
    "response_corrected_intensities",
    "response_corrected_intensity_errors",
)
_NIGHT_SAMPLES_NOTE = "Ask about one night with the review tools, which summarize the samples."
_PAGE_NOTE = "Ask for the rest with offset= (and a smaller limit=), or narrow the query's filters."

HEAVY_LISTS: tuple[HeavyList, ...] = (
    HeavyList(
        "Target",
        ("frames",),
        20,
        "frames",
        "List a target's frames with target_select_frames (filter_name=, since=, until=, limit=).",
    ),
    HeavyList("Page", ("items",), 50, "items", _PAGE_NOTE),
    HeavyList(
        "PhotometryResult", _LIGHT_CURVE_FIELDS, 0, "light curve points", "Read one star with star_get."
    ),
    HeavyList(
        "SpectroscopyResult", _SPECTRUM_FIELDS, 0, "spectrum points", "Read it with star_get_spectrum."
    ),
    HeavyList("ObservationSession", ("guiding_samples",), 0, "guiding samples", _NIGHT_SAMPLES_NOTE),
    HeavyList("ObservationSession", ("weather_samples",), 0, "weather samples", _NIGHT_SAMPLES_NOTE),
    HeavyList(
        "ObservationSessionWithProgress", ("guiding_samples",), 0, "guiding samples", _NIGHT_SAMPLES_NOTE
    ),
    HeavyList(
        "ObservationSessionWithProgress", ("weather_samples",), 0, "weather samples", _NIGHT_SAMPLES_NOTE
    ),
    HeavyList("LiveGuidingStatus", ("history",), 50, "guiding samples", "The newest samples are first."),
    HeavyList("QualityQueryResult", ("rows",), 50, "rows", _PAGE_NOTE),
    HeavyList("FrameStatusReport", ("frames",), 50, "frames", "Ask about fewer frames."),
)
"""The heavy lists of the replies, by model. ``test_reply_shaper.py`` checks
that each model is one a public method returns and each field is a list."""

_HEAVY_BY_MODEL: dict[str, tuple[HeavyList, ...]] = {
    name: tuple(entry for entry in HEAVY_LISTS if entry.model == name)
    for name in {e.model for e in HEAVY_LISTS}
}


@dataclass(frozen=True)
class ShapedReply:
    """A reply cut to fit, and the notes that say what was cut.

    Attributes
    ----------
    reply : `Any`
        The reply, or a smaller copy.
    notes : `tuple` [`str`, ...]
        One note per heavy list that was cut. Empty when nothing was cut.
    """

    reply: Any
    notes: tuple[str, ...] = ()


def _model_name(model: BaseModel) -> str:
    """Name a model's class, without the parameter of a generic one.

    Parameters
    ----------
    model : `BaseModel`
        The model.

    Returns
    -------
    name : `str`
        For example ``"Page"`` for a ``Page[FrameRecord]``.
    """
    origin = type(model).__pydantic_generic_metadata__.get("origin")
    return (origin or type(model)).__name__


def _cut_heavy_lists(value: Any, halvings: int, cut: dict[HeavyList, list[int]]) -> Any:
    """Copy a reply with each heavy list cut, recursively.

    Parameters
    ----------
    value : `Any`
        The reply, or a part of it.
    halvings : `int`
        How many times each list's ``keep`` is halved.
    cut : `dict` [`HeavyList`, `list` [`int`]]
        Filled with, per table entry, the items left out and the items
        there were, summed over every model cut.

    Returns
    -------
    value : `Any`
        The same value when nothing in it was cut, else a copy.
    """
    if isinstance(value, BaseModel):
        heavy = {
            field_name: entry
            for entry in _HEAVY_BY_MODEL.get(_model_name(value), ())
            for field_name in entry.fields
        }
        updates: dict[str, Any] = {}
        counted: set[HeavyList] = set()
        for field_name in type(value).model_fields:
            item = getattr(value, field_name)
            entry = heavy.get(field_name)
            if entry is not None and isinstance(item, list | tuple):
                keep = entry.keep >> halvings
                if len(item) > keep:
                    if entry not in counted:
                        totals = cut.setdefault(entry, [0, 0])
                        totals[0] += len(item) - keep
                        totals[1] += len(item)
                        counted.add(entry)
                    item = item[:keep]
                    updates[field_name] = item
            smaller = _cut_heavy_lists(item, halvings, cut)
            if smaller is not item:
                updates[field_name] = smaller
        return value.model_copy(update=updates) if updates else value
    if isinstance(value, list | tuple):
        items = [_cut_heavy_lists(item, halvings, cut) for item in value]
        if all(new is old for new, old in zip(items, value, strict=True)):
            return value
        return type(value)(items) if isinstance(value, tuple) else items
    if isinstance(value, dict):
        changed = {key: _cut_heavy_lists(item, halvings, cut) for key, item in value.items()}
        return value if all(changed[key] is value[key] for key in value) else changed
    return value


def _cut_notes(cut: dict[HeavyList, list[int]], limit: int) -> tuple[str, ...]:
    """Write one note for each heavy list that was cut.

    Parameters
    ----------
    cut : `dict` [`HeavyList`, `list` [`int`]]
        Per table entry, the items left out and the items there were.
    limit : `int`
        The budget, for the note.

    Returns
    -------
    notes : `tuple` [`str`, ...]
        The notes, in table order.
    """
    return tuple(
        f"Left out {cut[entry][0]:,} of {cut[entry][1]:,} {entry.what} ({entry.model}.{entry.fields[0]}) "
        f"to fit the reply budget of {limit:,} characters. {entry.how_to_ask}"
        for entry in HEAVY_LISTS
        if entry in cut
    )


def shape_reply(reply: Any, budget: int | None = None) -> ShapedReply:
    """Shrink any tool reply to fit the MCP reply budget.

    The three quality reports are fitted first (`fit_reply_to_budget`).
    When the reply is still too long, every heavy list in it
    (`HEAVY_LISTS`, at any depth) is cut to its ``keep``, then to half of
    that, and so on, until the reply fits or every list is empty.

    Parameters
    ----------
    reply : `Any`
        What a tool returned.
    budget : `int`, optional
        The most characters the reply's JSON may have. `reply_budget` when
        omitted.

    Returns
    -------
    shaped : `ShapedReply`
        The reply (the same object when it fits) and a note per heavy list
        that was cut.
    """
    limit = reply_budget() if budget is None else budget
    fitted = fit_reply_to_budget(reply, limit)
    if reply_characters(fitted) <= limit:
        return ShapedReply(fitted)
    largest_keep = max(entry.keep for entry in HEAVY_LISTS)
    halvings = 0
    while True:
        cut: dict[HeavyList, list[int]] = {}
        smaller = _cut_heavy_lists(fitted, halvings, cut)
        notes = _cut_notes(cut, limit)
        fits = reply_characters(smaller) + sum(len(note) for note in notes) <= limit
        if cut and (fits or largest_keep >> halvings == 0):
            return ShapedReply(smaller, notes)
        if largest_keep >> halvings == 0:
            return ShapedReply(fitted)
        halvings += 1
