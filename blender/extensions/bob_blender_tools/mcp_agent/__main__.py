"""Launcher for the agent-side MCP server.

Run standalone, from anywhere, with the deps provided by uv/pipx:

    uv run --with "mcp>=1.2" --with "pydantic>=2" --with "numpy>=1.26" \
        python /path/to/bob_blender_tools/mcp_agent/__main__.py

The extension's Advanced panel has a "Copy MCP Config" button that prints the exact
.mcp.json snippet with this install's resolved path. See docs/MCP.md.

This puts the extension root on sys.path so `mcp_agent` imports as a top-level package
(never touching the bpy-bound bob_blender_tools/__init__.py), then runs the server.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # <ext>

from mcp_agent.server import main  # noqa: E402

if __name__ == "__main__":
    main()
