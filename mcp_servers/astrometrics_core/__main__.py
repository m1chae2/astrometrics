"""Purpose: Start the astrometricslib-core MCP server.

Description: Run it with ``python -m mcp_servers.astrometrics_core``. It
logs to stderr, offers the tools the chosen profile allows, closes jobs that
an earlier server left open, and serves one client over stdin and stdout.
"""

import logging

from astrometricslib import close_interrupted_jobs, configure_logging, get_configuration
from mcp_servers.astrometrics_core.definition import MANIFEST_PATH, build_registry
from mcp_servers.common.profile import GAP_REPORT_GUIDANCE
from mcp_servers.common.server import run_server


def prepare() -> None:
    """Watch the configuration file and close jobs a stopped server left open."""
    get_configuration().watch_for_changes()
    close_interrupted_jobs()


def main() -> None:
    """Serve the astrometricslib-core tools to one client."""
    configure_logging("mcp_astrometricslib", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    registry = build_registry()
    registry.apply_profile(MANIFEST_PATH)
    run_server("astrometrics-core", GAP_REPORT_GUIDANCE, registry, before_start=prepare)


if __name__ == "__main__":
    main()
