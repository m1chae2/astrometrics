"""Purpose: The MCP tool inventory and the reviewed tool decisions.

Description: ``tool_dispositions`` holds the decisions a person made about
each tool. ``tool_inventory`` lists every tool the servers serve and writes
each server's ``tool_manifest.json``. Run it with
``python -m mcp_servers.inventory``. Importing this package loads nothing
else, so the gap server can read the categories without astrometricslib.
"""
