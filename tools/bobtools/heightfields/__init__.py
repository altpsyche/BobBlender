"""BobBlenderHeightFields: terrain heightfield generation and erosion.

Pure venv package (numpy on CPU, CuPy/CUDA on GPU), no bpy and no MCP. Kept
extractable as a standalone repo later. See docs/UNIFIED-SYSTEM.md.

Public surface: bake() runs generate -> erode -> write PNG + sidecar with a
params-hash cache; select() picks the compute backend.
"""

from . import backend, cache, erode, generate, io, params, presets, pipeline
from .backend import available, select
from .params import build_params
from .pipeline import bake

__all__ = [
    "backend", "cache", "erode", "generate", "io", "params", "presets", "pipeline",
    "available", "select", "bake", "build_params",
]
