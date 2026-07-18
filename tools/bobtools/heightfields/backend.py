"""Compute backend selection for heightfield generation and erosion.

CPU (numpy) is always available and is the deterministic reference: seeded runs
are bit-reproducible, so it backs the golden tests. GPU backends accelerate the
same array work. CuPy covers NVIDIA CUDA today and, with a ROCm build, AMD later
(it imports as `cupy` either way and reports its platform), so one GpuBackend
serves both. A different path (a portable Vulkan or Taichi backend, or a
hand-rolled AMD one) is added by writing another Backend and registering it below;
nothing above this module changes.

Selection is CPU-safe. Callers ask for 'auto' (prefer GPU, fall back to CPU) or a
specific name; the BOB_HF_BACKEND env var overrides both. Pure module: numpy is
imported only when a CPU backend is built, cupy only when a GPU one is. No bpy, no
MCP, no repo config, so this stays extractable with the rest of the package.
"""

from __future__ import annotations

import os


class Backend:
    """Array module plus device transfer. Erosion code is written against this."""

    name = "base"
    platform = None  # 'cpu' | 'cuda' | 'hip' | ...

    def __init__(self, xp):
        self.xp = xp  # a numpy-compatible array module

    @property
    def is_gpu(self) -> bool:
        return False

    def asarray(self, a):
        """Host numpy array -> this backend's array."""
        return self.xp.asarray(a)

    def asnumpy(self, a):
        """This backend's array -> host numpy array."""
        return a

    def synchronize(self):
        """Block until queued device work finishes. No-op on CPU."""

    def info(self) -> dict:
        return {"name": self.name, "platform": self.platform, "gpu": self.is_gpu}


class CpuBackend(Backend):
    name = "cpu"
    platform = "cpu"

    def __init__(self):
        import numpy as np

        super().__init__(np)


class GpuBackend(Backend):
    """CuPy-backed GPU. Serves NVIDIA CUDA and, with a ROCm CuPy build, AMD/HIP."""

    name = "gpu"

    def __init__(self):
        import cupy as cp

        super().__init__(cp)
        self._cp = cp
        self.platform = "hip" if cp.cuda.runtime.is_hip else "cuda"

    @property
    def is_gpu(self) -> bool:
        return True

    def asnumpy(self, a):
        return self._cp.asnumpy(a)

    def synchronize(self):
        self._cp.cuda.runtime.deviceSynchronize()

    def info(self) -> dict:
        cp = self._cp
        props = cp.cuda.runtime.getDeviceProperties(cp.cuda.runtime.getDevice())
        device = props.get("name")
        if isinstance(device, bytes):
            device = device.decode()
        _, total = cp.cuda.runtime.memGetInfo()
        cc = f"{props.get('major')}.{props.get('minor')}"
        return {
            "name": self.name,
            "platform": self.platform,
            "gpu": True,
            "device": device,
            "compute_capability": cc,
            "mem_total_mib": total // (1024 * 1024),
            "runtime": cp.cuda.runtime.runtimeGetVersion(),
        }


def _make_cpu():
    return CpuBackend()


def _make_gpu():
    try:
        import cupy as cp

        if cp.cuda.runtime.getDeviceCount() < 1:
            return None
        return GpuBackend()
    except Exception:
        return None


# 'auto' preference order: GPU first, CPU last so it is always a safe fallback.
# AMD/ROCm rides the same GpuBackend (a cupy-rocm build). Add other device
# backends here (for example a portable Vulkan path) keeping CPU last.
_AUTO = ("gpu", "cpu")
_FACTORIES = {
    "cpu": _make_cpu,
    "gpu": _make_gpu,
}


def _try(name):
    factory = _FACTORIES.get(name)
    if factory is None:
        return None
    try:
        return factory()
    except Exception:
        return None


def available() -> list[str]:
    """Backend names that can run here, in preference order."""
    return [name for name in _AUTO if _try(name) is not None]


def select(prefer: str = "auto") -> Backend:
    """Return a Backend. Honors BOB_HF_BACKEND, then `prefer`, then auto order."""
    order = []
    forced = os.environ.get("BOB_HF_BACKEND")
    if forced:
        order.append(forced)
    if prefer and prefer != "auto":
        order.append(prefer)
    order.extend(_AUTO)

    seen = set()
    for name in order:
        if name in seen:
            continue
        seen.add(name)
        backend = _try(name)
        if backend is not None:
            return backend
    return CpuBackend()  # constructing numpy should never fail
