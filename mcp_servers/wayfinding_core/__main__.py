"""Purpose: Start the wayfindinglib-core MCP server.

Description: Run it with ``python -m mcp_servers.wayfinding_core``. It logs
to stderr, watches the config file for changes, offers the tools the chosen
profile allows, and serves one client over stdin and stdout.
"""

import logging

from astrometricslib import configure_logging, get_configuration
from mcp_servers.common.profile import GAP_REPORT_GUIDANCE
from mcp_servers.common.server import run_server
from mcp_servers.wayfinding_core.definition import MANIFEST_PATH, build_registry


def prepare() -> None:
    """Watch the config file, so a changed host or path reaches this server."""
    get_configuration().watch_for_changes()


def main() -> None:
    """Serve the wayfindinglib-core tools to one client."""
    configure_logging("mcp_wayfindinglib", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    registry = build_registry()
    registry.apply_profile(MANIFEST_PATH)
    run_server("wayfindinglib-core", GAP_REPORT_GUIDANCE, registry, before_start=prepare)


if __name__ == "__main__":
    main()
