"""Purpose: Start the astrometrics-backend MCP server.

Description: Run it with ``python -m mcp_servers.backend``. It logs to
stderr, offers the tools the chosen profile allows, and serves one client
over stdin and stdout. Every tool reaches the running backend through its
``/api/rpc`` route, so the backend must be started first.
"""

import logging

from astrometricslib import configure_logging
from mcp_servers.backend.definition import MANIFEST_PATH, SERVER_NAME, NotificationResource, build_registry
from mcp_servers.common.profile import GAP_REPORT_GUIDANCE
from mcp_servers.common.server import run_server


def main() -> None:
    """Serve the backend tools and the notifications resource to one client."""
    configure_logging("mcp_backend", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    registry = build_registry()
    registry.apply_profile(MANIFEST_PATH)
    run_server(SERVER_NAME, GAP_REPORT_GUIDANCE, registry, resources=NotificationResource())


if __name__ == "__main__":
    main()
