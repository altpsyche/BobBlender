"""Dev launcher for the agent-side MCP server (the `bob-mcp` console script).

The MCP server now lives INSIDE the extension (blender/extensions/bob_blender_tools/mcp_agent/)
so it ships with the product and runs standalone. For in-repo dev this shim keeps `bob-mcp`
and the repo .mcp.json working: it puts the extension root on sys.path so `mcp_agent` imports
as a top-level package (never touching the bpy-bound bob_blender_tools/__init__.py), then runs
the same server. Standalone installs launch mcp_agent/__main__.py directly (see docs/MCP.md).
"""

import os
import sys

_EXT = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "..",
        "blender", "extensions", "bob_blender_tools",
    )
)


def main() -> None:
    sys.path.insert(0, _EXT)
    from mcp_agent.server import main as _main

    _main()


if __name__ == "__main__":
    main()
