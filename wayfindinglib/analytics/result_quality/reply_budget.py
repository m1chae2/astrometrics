"""Purpose: Keep the replies of the quality reads short enough to send.

Description: The MCP tools cut a reply longer than 40,000 characters, and a
cut reply is no longer valid JSON. The target report, tonight's ranking and
the night review can all grow with the library. This module holds what they
share, in one place:

* the limit, and the budget a reply must meet (the limit less 10 percent
  headroom);
* a measure of a reply made the way the MCP tool registry makes its own
  (``model_dump()`` and compact ``json.dumps`` with ``default=str``), so a
  reply that fits here is not cut there;
* ``fit_reply``, which applies a reply's own shrinking steps until it fits;
* the way to cut the evidence lists of statements.

Cutting never removes a statement, and a statement that lost references says
how many (``evidence_omitted_count``), so the loss is visible. The rows stay
readable with ``QualityRecords.query``. The moving of this trimming to the MCP
edge is a later change (issue #96).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence

from pydantic import BaseModel

from wayfindinglib.models.quality.statements import TargetStatement

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
