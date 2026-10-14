"""Purpose: Run the MCP tool inventory from the command line.

Description: ``python -m mcp_servers.inventory`` lists every tool, drafts a
class for each, and with ``--write-runtime-manifests`` writes each server's
``tool_manifest.json``. See `mcp_servers.inventory.tool_inventory`.
"""

import sys

from mcp_servers.inventory.tool_inventory import main

if __name__ == "__main__":
    sys.exit(main())
