"""Purpose: Keep the replies of the quality reads short enough to send.

Description: The MCP tools cut a reply longer than 40,000 characters, and a
cut reply is no longer valid JSON. The target report, tonight's ranking and
the night review can all grow with the library. The library itself returns
them in full, to every caller (the desktop app shows full reports). The MCP
layer calls `fit_reply_to_budget` on a tool's reply before it sends it. This
module holds what that needs, in one place:

* the limit, and the budget a reply must meet (the limit less 10 percent
  headroom);
* a measure of a reply made the way the MCP tool registry makes its own
  (``model_dump()`` and compact ``json.dumps`` with ``default=str``), so a
  reply that fits here is not cut there;
* ``fit_reply``, which applies a reply's own shrinking steps until it fits;
* the way to cut the evidence lists of statements;
* ``fit_reply_to_budget``, the one public entry point, which picks the
  shrinking steps for each kind of reply.

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
from typing import cast

from pydantic import BaseModel

from wayfindinglib.models.planning.target_report import TargetDatasetReport
from wayfindinglib.models.planning.tonight_ranking import TonightRanking
from wayfindinglib.models.quality.statements import TargetStatement
from wayfindinglib.models.session.night_quality import NightQuality

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


def reply_characters(model: BaseModel) -> int:
    """Count the characters of a model's compact JSON.

    The text is made the way the MCP tool registry makes it: ``model_dump()``
    (field names, Python values) and compact ``json.dumps`` with
    ``default=str``.

    Parameters
    ----------
    model : `BaseModel`
        The reply.

    Returns
    -------
    characters : `int`
        The length of the text an MCP tool would send.
    """
    return len(json.dumps(model.model_dump(), separators=(",", ":"), default=str))


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


def limit_statement_evidence(statements: Sequence[TargetStatement], keep: int) -> list[TargetStatement]:
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


def _fit_target_report(report: TargetDatasetReport, limit: int) -> TargetDatasetReport:
    """Shrink a target report until its JSON fits the budget.

    Two things can go, in this order. First the context section, which is only
    a convenience: the statements cite the rows they used, so the report
    says why the section is empty. Then the long evidence lists, cut to
    ``EVIDENCE_STEPS`` references per statement in turn, each statement
    counting what it lost. The statements, their order and the counts of the
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

    def shrink(current: TargetDatasetReport, round_number: int) -> TargetDatasetReport | None:
        """Make the report one step smaller.

        Parameters
        ----------
        current : `TargetDatasetReport`
            The report so far.
        round_number : `int`
            0 drops the context; later rounds cut the evidence lists.

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
        if round_number > len(EVIDENCE_STEPS):
            return None
        keep = EVIDENCE_STEPS[round_number - 1]
        return current.model_copy(update={"statements": limit_statement_evidence(current.statements, keep)})

    return fit_reply(report, shrink, limit)


def _fit_tonight_ranking(ranking: TonightRanking, limit: int) -> TonightRanking:
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

    def shrink(current: TonightRanking, _round_number: int) -> TonightRanking | None:
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


def _fit_night_quality(quality: NightQuality, limit: int) -> NightQuality:
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
        they lost; a note says it was done. No statement is removed.
    """
    note = (
        f"The evidence lists of the statements were cut to fit the reply budget of {limit:,} characters. "
        "Read the rows with QualityRecords.query."
    )

    def shrink(current: NightQuality, round_number: int) -> NightQuality | None:
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
        if round_number >= len(EVIDENCE_STEPS):
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
    if isinstance(reply, TargetDatasetReport):
        return cast(ReplyT, _fit_target_report(reply, limit))
    if isinstance(reply, TonightRanking):
        return cast(ReplyT, _fit_tonight_ranking(reply, limit))
    if isinstance(reply, NightQuality):
        return cast(ReplyT, _fit_night_quality(reply, limit))
    return reply
