"""Put the single-source heightfields compute on sys.path for venv consumers.

The compute is the ONE committed copy inside the extension
(`blender/extensions/bob_blender_tools/core/heightfields`); there is no venv copy, which is the
single-compute rule. The
venv reaches it by importing from there. Its parent addon package (`bob_blender_tools`) imports
bpy at module scope, so it cannot be imported as `bob_blender_tools.core.heightfields` in the
venv; instead this puts the `core` dir on sys.path so `import heightfields` resolves to that one
source. Import this module for its side effect, then `import heightfields`.
"""

import os
import sys

_CORE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..",
                 "blender", "extensions", "bob_blender_tools", "core")
)
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)
