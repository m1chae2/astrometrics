"""Tests that a reply too long for the size limit stays valid JSON.

Cutting the text of a JSON list part way through leaves text that does not
parse. These tests check that a long list keeps whole items and says how
many were left out, and that a short reply is unchanged.
"""

import json

from mcp_servers.common.tool_registry import MAXIMUM_REPLY_BYTES, result_content


def test_a_long_list_keeps_whole_items_and_notes_the_rest() -> None:
    """A list over the limit sends whole items as JSON, then a note."""
    items = [{"name": f"metric_{index}", "text": "x" * 500} for index in range(200)]
    content = result_content(items)
    kept = json.loads(content[0].text)
    assert 0 < len(kept) < len(items)
    assert kept == items[: len(kept)]
    assert len(content[0].text) <= MAXIMUM_REPLY_BYTES
    assert f"first {len(kept)} of {len(items)}" in content[1].text


def test_a_short_list_is_sent_whole() -> None:
    """A list under the limit comes back as one JSON text with every item."""
    items = [{"name": "one"}, {"name": "two"}]
    content = result_content(items)
    assert len(content) == 1
    assert json.loads(content[0].text) == items
