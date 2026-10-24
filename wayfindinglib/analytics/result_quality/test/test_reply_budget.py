"""Purpose: Tests for the shared reply budget helper.

Description: Checks that the limit equals the MCP tool registry's, that the
measure of a reply is the registry's own (``model_dump`` with compact
``json.dumps`` and ``default=str``), that the budget keeps 10 percent
headroom, and that ``fit_reply`` shrinks step by step until a reply fits.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from wayfindinglib.analytics.result_quality.reply_budget import (
    MAXIMUM_REPLY_CHARACTERS,
    fit_reply,
    reply_budget,
    reply_characters,
)


class _Reply(BaseModel):
    """A small reply with a date and a list."""

    when: datetime
    items: list[str]


def test_the_limit_equals_the_mcp_tool_registry_limit() -> None:
    """The registry's constant is read from its source, not imported."""
    source = Path(__file__).resolve().parents[4] / "mcp_servers" / "common" / "tool_registry.py"
    match = re.search(r"^MAXIMUM_REPLY_BYTES = (\d+)", source.read_text(), re.MULTILINE)
    assert match is not None
    assert int(match.group(1)) == MAXIMUM_REPLY_CHARACTERS


def test_the_budget_keeps_headroom() -> None:
    """The budget is the limit less ten percent."""
    assert reply_budget() == 36_000
    assert reply_budget(1000) == 900


def test_a_reply_is_measured_like_the_registry_measures_it() -> None:
    """A date is written with ``str``, as ``default=str`` does."""
    reply = _Reply(when=datetime(2026, 10, 1, tzinfo=UTC), items=["a"])
    expected = len(json.dumps(reply.model_dump(), separators=(",", ":"), default=str))
    assert reply_characters(reply) == expected


def test_fit_reply_applies_steps_until_the_reply_fits() -> None:
    """Each round halves the list until it fits or the steps run out."""
    reply = _Reply(when=datetime(2026, 10, 1, tzinfo=UTC), items=["x" * 10] * 64)

    def halve(current: _Reply, _round: int) -> _Reply | None:
        """Halve the list, or give up when one item is left.

        Returns
        -------
        smaller : `_Reply` or `None`
            The shorter reply, or ``None`` when one item is left.
        """
        if len(current.items) <= 1:
            return None
        return current.model_copy(update={"items": current.items[: len(current.items) // 2]})

    fitted = fit_reply(reply, halve, budget=200)
    assert reply_characters(fitted) <= 200
    assert 1 <= len(fitted.items) < 64
    assert fit_reply(reply, halve, budget=10).items == ["x" * 10]
    assert fit_reply(reply, halve, budget=10**6) is reply
