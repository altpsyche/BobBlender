"""BobBlenderHeightFields: terrain heightfield generation and erosion.

Pure venv package (numpy on CPU, CuPy/CUDA on GPU), no bpy and no MCP. Kept
extractable as a standalone repo later. See docs/UNIFIED-SYSTEM.md.

Public surface: bake() evaluates a terrain op stack (generators write a base,
filters and flow-accumulation erosion shape it -- see engine.run_stack) and writes
a PNG + reproducibility sidecar with a params-hash cache; select() picks the
compute backend.
"""

from . import backend, cache, engine, erode, generate, io, maps, params, presets, pipeline
from .backend import available, select
from .params import build_params
from .pipeline import bake

__all__ = [
    "backend", "cache", "engine", "erode", "generate", "io", "maps", "params",
    "presets", "pipeline", "available", "select", "bake", "build_params",
]
