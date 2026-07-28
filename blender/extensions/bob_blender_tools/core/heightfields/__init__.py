"""BobBlenderHeightFields: terrain heightfield generation and erosion.

The single committed copy of the terrain compute, living inside the extension at
`core/heightfields/`, which lives in the extension and is never copied into a venv. It is bpy-free
and MCP-free pure array code (numpy on CPU, CuPy/CUDA on GPU), so it runs BOTH in-process inside
Blender's bundled Python (the live bake) and in the venv (the golden tests), off one source with no
duplicate. `auto` uses the GPU when a device is present and CPU otherwise; CPU is the deterministic
reference. Kept extractable as a standalone repo later. See docs/ROADMAP.md.

Public surface: bake() evaluates a terrain op stack (generators write a base, filters and
flow-accumulation erosion shape it -- see engine.run_stack) and writes a PNG + reproducibility
sidecar with a params-hash cache; select() picks the compute backend.
"""

from . import backend, cache, engine, erode, generate, io, maps, params, presets, pipeline
from .backend import available, select
from .params import build_params
from .pipeline import bake

__all__ = [
    "backend", "cache", "engine", "erode", "generate", "io", "maps", "params",
    "presets", "pipeline", "available", "select", "bake", "build_params",
]
