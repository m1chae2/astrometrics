"""Purpose: Keep the replies of the quality reads short enough to send.

Description: The MCP tools cut a reply longer than 40,000 characters, and a
cut reply is no longer valid JSON. The target report, tonight's ranking and
the night review can all grow with the library. This module holds what they
share: the limit, a measure of a reply, and the way to cut the evidence lists
of statements. Cutting never removes a statement, and a statement that lost
references says how many (``evidence_omitted_count``), so the loss is visible.
The rows stay readable with ``QualityRecords.query``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from pydantic import BaseModel

from wayfindinglib.models.quality.statements import TargetStatement

MAXIMUM_REPLY_CHARACTERS = 40_000
"""Most characters of compact JSON for one reply. It equals the reply limit of
the MCP tools (`MAXIMUM_REPLY_BYTES` in the MCP tool registry), so an AI agent
gets a whole reply. Design estimate, register row WAY-D77."""

EVIDENCE_STEPS = (20, 5, 1)
"""References one statement keeps, tried in turn, when a reply is too long.
Design estimate, register row WAY-D77."""


def reply_characters(model: BaseModel) -> int:
    """Count the characters of a model's compact JSON.

    Parameters
    ----------
    model : `BaseModel`
        The reply.

    Returns
    -------
    characters : `int`
        The length of the text an MCP tool would send.
    """
    return len(json.dumps(model.model_dump(mode="json"), separators=(",", ":")))


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
