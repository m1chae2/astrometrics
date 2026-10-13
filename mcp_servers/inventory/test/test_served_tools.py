"""Purpose: Check the served MCP tools against the public API and decisions.

Description: The two library servers turn every public library method into
a tool. ``tool_dispositions.DECISIONS`` records, for each tool, whether it is
offered and, if not, why. These tests check three things:

* every reflected library method has a row in the table, so a new public
  method fails a test until someone decides whether an AI may use it;
* the tools a server offers are exactly the reflected methods whose row
  says ``keep``, less the ones under an interim block;
* the committed ``tool_manifest.json`` files match a fresh run of the
  inventory, so a changed decision cannot be forgotten.
"""

import json
from typing import Any

import pytest

from astrometricslib import Astrometrics
from mcp_servers.astrometrics_core import definition as astrometrics_definition
from mcp_servers.common.profile import find_withheld_tools, load_manifest
from mcp_servers.common.reflection import register_reflected_tools
from mcp_servers.common.tool_registry import ToolRegistry
from mcp_servers.inventory.tool_dispositions import DECISIONS, INTERIM_BLOCKS
from mcp_servers.inventory.tool_inventory import (
    RUNTIME_MANIFEST_PATHS,
    ToolRecord,
    build_runtime_manifest,
    collect_inventory,
)
from mcp_servers.wayfinding_core import definition as wayfinding_definition
from wayfindinglib import Wayfinder


def _reflected_tool_names(api_object: Any, branch_prefixes: dict[str, str]) -> set[str]:
    """List the tools that reflection makes from a library's public methods.

    Parameters
    ----------
    api_object : `Any`
        The library's top-level object, `Astrometrics` or `Wayfinder`.
    branch_prefixes : `dict` [`str`, `str`]
        The server's sub-API -> tool name prefix table.

    Returns
    -------
    names : `set` [`str`]
        One tool name per public method.
    """
    registry = ToolRegistry()
    register_reflected_tools(registry, api_object, branch_prefixes)
    return set(registry.tools)


REFLECTED_SERVERS = {
    "astrometricslib-core": (Astrometrics, astrometrics_definition.BRANCH_PREFIXES),
    "wayfindinglib-core": (Wayfinder, wayfinding_definition.BRANCH_PREFIXES),
}
"""Server name -> the library class it reflects and its prefix table."""


@pytest.fixture(scope="module")
def inventory() -> dict[str, list[ToolRecord]]:
    """Collect the tools of every server once for this module.

    Returns
    -------
    inventory : `dict` [`str`, `list` [`ToolRecord`]]
        Server name -> its tool records, with decisions applied.
    """
    return collect_inventory()


@pytest.mark.parametrize("server", sorted(REFLECTED_SERVERS))
def test_served_tools_are_the_public_api_minus_the_decisions_table(server: str) -> None:
    """Each public method is served, or the table says why it is not."""
    api_class, branch_prefixes = REFLECTED_SERVERS[server]
    reflected = _reflected_tool_names(api_class(), branch_prefixes)
    assert reflected

    undecided = sorted(reflected - DECISIONS.keys())
    assert not undecided, (
        f"Public methods with no row in mcp_servers/inventory/tool_dispositions.py DECISIONS: {undecided}. "
        "Decide whether an AI may use each one, add the row, and run "
        "`python -m mcp_servers.inventory --write-runtime-manifests`."
    )

    manifest = load_manifest(RUNTIME_MANIFEST_PATHS[server])
    withheld = find_withheld_tools(sorted(reflected), manifest, "developer")
    served = reflected - withheld.keys()
    not_served_by_decision = {name for name in reflected if DECISIONS[name].disposition != "keep"}
    assert served == reflected - not_served_by_decision - INTERIM_BLOCKS.keys()


@pytest.mark.parametrize("server", sorted(RUNTIME_MANIFEST_PATHS))
def test_committed_manifest_matches_a_fresh_inventory(
    server: str, inventory: dict[str, list[ToolRecord]]
) -> None:
    """A changed decision or a new tool shows up as an out-of-date manifest."""
    committed = json.loads(RUNTIME_MANIFEST_PATHS[server].read_text(encoding="utf-8"))
    assert committed == build_runtime_manifest(server, inventory[server]), (
        f"The {server} tool manifest is out of date. "
        "Run `python -m mcp_servers.inventory --write-runtime-manifests` and commit the result."
    )
