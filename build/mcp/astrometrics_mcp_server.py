"""Backward-compatible entry point for the Astrometrics MCP server.

This script delegates to backend.mcp.__main__ to support legacy MCP
client configurations expecting scripts/mcp/astrometrics_mcp_server.py.
"""

import asyncio
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def run_server() -> None:
    """Execute the stdio MCP server loop for Astrometrics backend."""
    from backend.mcp.__main__ import main

    asyncio.run(main())


if __name__ == "__main__":
    run_server()
