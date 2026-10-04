"""Purpose: Decide which MCP tools a server offers to its client.

Description: An AI client should only get the tools the project has
reviewed. Each MCP server has a manifest file, ``tool_manifest.json``, that
gives every tool a class (what it can do), a category and a disposition
(what the project plans to do with it). A profile is a rule for which
classes and dispositions a client may use.

The rules fail closed:

* A tool that is not in the manifest is withheld. A new public method
  therefore stays hidden until someone reviews it and adds it.
* A missing or unreadable manifest withholds every tool.
* An unknown profile name falls back to the default profile.

The default profile, ``investigator``, offers tools that look things up or
calculate, and one kind of write: bringing frames from the telescope into the
library (class ``ingest``), which adds files and records and never deletes.
It offers nothing that commands a device, changes settings or runs code.
The ``developer`` profile adds the tools that run the project's own tests
and builds.

The ``backend/mcp/tool_inventory.py`` script writes the manifest files from
the reviewed decisions. For exact behavior, read the code.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROFILE_ENVIRONMENT_VARIABLE = "ASTROMETRICS_MCP_PROFILE"
"""Environment variable that chooses the profile when a server starts."""

DEFAULT_PROFILE = "investigator"
"""The profile used when the variable is unset or not recognized."""

PROFILE_CLASSES = {
    "investigator": frozenset({"observe", "compute", "ingest", "ui-control"}),
    "developer": frozenset({"observe", "compute", "ingest", "ui-control", "develop"}),
}
"""Profile name -> the tool classes it may use."""

SERVED_DISPOSITIONS = frozenset({"keep", "merge"})
"""Dispositions a client may use. A tool marked ``merge`` stays available
until the tool that replaces it exists."""


GAP_REPORT_GUIDANCE = (
    "These tools look things up and calculate. The only write is bringing frames from the telescope into "
    "the library. If none of the tools you can use can do what you need, stop. Do not look "
    "for a workaround: do not chain tools to imitate a missing one, and do not ask for code to be run. "
    "Call report_capability_gap on the astrometrics-gaps server. Say what you tried, why it fell short, "
    "and what tool would help. Then tell the person you cannot do it with the current tools."
)
"""Instructions each MCP server gives its client at the start of a session."""

GAP_REPORT_REMINDER = (
    "If you need this ability, stop and file a report with report_capability_gap on the astrometrics-gaps "
    "server. Do not look for a workaround."
)
"""A short reminder added to every refusal."""


def refusal_message(tool_name: str, reason: str | None = None) -> str:
    """Write the error for a call to a tool the client may not use.

    Parameters
    ----------
    tool_name : `str`
        The tool the client asked for.
    reason : `str`, optional
        Why the profile withholds it. `None` when no server has the tool.

    Returns
    -------
    message : `str`
        An error that starts with "Unknown tool", says why when the reason
        is known, and tells the client to file a gap report.
    """
    detail = f" It is not available to you: {reason}." if reason else ""
    return f"Error: Unknown tool {tool_name}.{detail} {GAP_REPORT_REMINDER}"


def current_profile() -> str:
    """Read the profile name from the environment.

    Returns
    -------
    profile : `str`
        The value of ``ASTROMETRICS_MCP_PROFILE`` if it names a known
        profile, otherwise ``"investigator"``. An unknown name is logged.
    """
    requested = os.environ.get(PROFILE_ENVIRONMENT_VARIABLE, DEFAULT_PROFILE)
    if requested in PROFILE_CLASSES:
        return requested
    logger.warning("Unknown MCP profile %r; using %r instead.", requested, DEFAULT_PROFILE)
    return DEFAULT_PROFILE


def load_manifest(path: Path) -> dict[str, Any] | None:
    """Read a server's tool manifest.

    Parameters
    ----------
    path : `pathlib.Path`
        The ``tool_manifest.json`` file.

    Returns
    -------
    manifest : `dict` [`str`, `Any`] or `None`
        The manifest, or `None` if the file is missing or is not a JSON
        object with a ``tools`` table. The problem is logged.
    """
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.error("Cannot read the MCP tool manifest %s: %s", path, error)
        return None
    if not isinstance(manifest, dict) or not isinstance(manifest.get("tools"), dict):
        logger.error("The MCP tool manifest %s has no 'tools' table.", path)
        return None
    return manifest


def withheld_reason(entry: dict[str, Any] | None, profile: str) -> str | None:
    """Say why a tool is not offered to a profile, or `None` if it is.

    Parameters
    ----------
    entry : `dict` [`str`, `Any`] or `None`
        The tool's manifest entry, or `None` if the manifest has none.
    profile : `str`
        A key of ``PROFILE_CLASSES``.

    Returns
    -------
    reason : `str` or `None`
        A plain-language reason, or `None` when the profile may use the tool.
    """
    if entry is None:
        return "not in the manifest"
    if entry.get("interim_block"):
        return f"blocked for now: {entry['interim_block']}"
    tool_class = entry.get("tool_class")
    if tool_class not in PROFILE_CLASSES[profile]:
        return f"class {tool_class!r} is not offered to the {profile} profile"
    disposition = entry.get("disposition")
    if disposition == "merged":
        return f"it was replaced by {entry.get('merge_into') or 'a newer tool'}"
    if disposition not in SERVED_DISPOSITIONS:
        return f"disposition {disposition!r} is not offered"
    return None


def find_withheld_tools(
    tool_names: list[str], manifest: dict[str, Any] | None, profile: str
) -> dict[str, str]:
    """Find the tools a profile may not use.

    Parameters
    ----------
    tool_names : `list` [`str`]
        Every tool the server has registered.
    manifest : `dict` [`str`, `Any`] or `None`
        The manifest from `load_manifest`. `None` withholds every tool.
    profile : `str`
        A key of ``PROFILE_CLASSES``.

    Returns
    -------
    withheld : `dict` [`str`, `str`]
        Tool name -> why it is withheld. Tools the profile may use are not
        listed.
    """
    if manifest is None:
        return dict.fromkeys(tool_names, "the manifest is missing or unreadable")
    entries = manifest["tools"]
    withheld = {}
    for name in tool_names:
        reason = withheld_reason(entries.get(name), profile)
        if reason is not None:
            withheld[name] = reason
    return withheld
