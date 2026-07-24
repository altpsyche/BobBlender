"""GPU/CPU compute delivery for the terrain bake (P5).

The terrain compute (`core/heightfields`) needs scipy on CPU and CuPy on GPU; Blender's bundled
Python ships neither. This module detects the machine's GPU and CUDA line and installs the
matching wheels into Blender's OWN Python on the user's request (the Enable Compute operator), so
a standalone install gets full in-process terrain with no venv. GPU is a required, first-class
capability: the extension owns delivery end to end via this guided install, never a manual step.

bpy-free: pure host detection plus a pip subprocess. The operator and panel UI live in the addon.
Blender's `sys.executable` IS the bundled interpreter (verified on 5.2), so `pip` installs there.
"""

import importlib
import importlib.util
import os
import re
import subprocess
import sys

# NVIDIA CUDA major line -> the CuPy wheel that targets it.
CUPY_LINES = {"13": "cupy-cuda13x", "12": "cupy-cuda12x", "11": "cupy-cuda11x"}

_CACHE = {"probe": None}


def blender_python():
    """The bundled Python interpreter. On Blender 5.2 sys.executable is the interpreter itself."""
    return sys.executable


def _nvidia_smi():
    try:
        return subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=15)
    except Exception:
        return None


def nvidia():
    """(gpu_name, cuda_major) for an NVIDIA GPU, or None. Parses nvidia-smi for the name and the
    driver CUDA line, handling both 'CUDA Version: X.Y' and the newer 'CUDA UMD Version: X.Y'."""
    p = _nvidia_smi()
    if p is None or p.returncode != 0:
        return None
    m = re.search(r"CUDA (?:UMD )?Version:?\s*([0-9]+)\.[0-9]+", p.stdout)
    cuda_major = m.group(1) if m else None
    name = "NVIDIA GPU"
    try:
        q = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=15)
        if q.returncode == 0 and q.stdout.strip():
            name = q.stdout.strip().splitlines()[0].strip()
    except Exception:
        pass
    return name, cuda_major


def has_module(name):
    """True when `name` is importable in Blender's Python (spec check, no side effects)."""
    importlib.invalidate_caches()
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def probe(refresh=False):
    """Detect the compute situation, cached (nvidia-smi is a subprocess; do not run it per draw).

    Returns {gpu, gpu_name, cuda_major, cupy_pkg, cupy_ok, scipy_ok}. `cupy_pkg` is the wheel to
    install for this machine's CUDA line (None if the line is unknown or there is no GPU)."""
    if _CACHE["probe"] is not None and not refresh:
        return _CACHE["probe"]
    gpu = nvidia()
    cuda_major = gpu[1] if gpu else None
    pr = {
        "gpu": bool(gpu),
        "gpu_name": gpu[0] if gpu else None,
        "cuda_major": cuda_major,
        "cupy_pkg": CUPY_LINES.get(cuda_major) if gpu else None,
        "cupy_ok": has_module("cupy"),
        "scipy_ok": has_module("scipy"),
    }
    _CACHE["probe"] = pr
    return pr


def needed_packages(pr):
    """Wheels to install for full compute here: scipy always (the CPU compute needs it), plus the
    matching CuPy line when an NVIDIA GPU is present. Skips anything already importable."""
    pkgs = []
    if not pr["scipy_ok"]:
        pkgs.append("scipy")
    if pr["gpu"] and pr["cupy_pkg"] and not pr["cupy_ok"]:
        pkgs.append(pr["cupy_pkg"])
    return pkgs


def install(packages, dry_run=False, timeout=1800):
    """pip install `packages` into Blender's Python. Returns (ok, message). Resilient: a network
    failure, a missing wheel for the CUDA line, or a blocked site-packages returns (False, why),
    never raises, so the caller can degrade to CPU with a specific message."""
    if not packages:
        return True, "nothing to install"
    cmd = [blender_python(), "-m", "pip", "install", "--no-input"]
    if dry_run:
        cmd.append("--dry-run")
    cmd += list(packages)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, "Blender's Python or pip was not found"
    except subprocess.TimeoutExpired:
        return False, "install timed out (slow network?)"
    except Exception as exc:
        return False, f"install could not run: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "").strip()[-300:]
    return True, "installed: " + ", ".join(packages)


def verify_gpu():
    """Real device round-trip INCLUDING an NVRTC JIT compile: import CuPy and run a custom
    ElementwiseKernel (which the actual bake uses). (ok, device_name_or_error).

    The JIT step is essential: `cupy.arange().sum()` uses precompiled kernels and passes even when
    NVRTC cannot find libnvrtc-builtins (the exact failure inside a Steam sandbox on CUDA 13). Only
    compiling a kernel exercises the path the bake needs, so this is the honest P5 acceptance check."""
    importlib.invalidate_caches()
    try:
        import cupy  # noqa: PLC0415
        k = cupy.ElementwiseKernel("float32 x", "float32 y", "y = x * 2.0f", "bbt_probe")
        arr = cupy.arange(16, dtype=cupy.float32)
        _ = float(k(arr).sum().get())  # forces NVRTC compile + kernel launch + device->host copy
        try:
            dev = cupy.cuda.runtime.getDeviceProperties(0)["name"].decode()
        except Exception:
            dev = "CUDA device"
        return True, dev
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:140]}"


def status_line(pr=None, venv_exists=False):
    """A short, truthful backend hint for the panel. GPU when CuPy round-trips; else CPU, noting
    whether the in-process CPU path is ready (scipy present) or still needs Enable Compute / the
    dev-venv fallback."""
    pr = pr or probe()
    if pr["cupy_ok"]:
        ok, info = verify_gpu()
        if ok:
            return f"GPU: {info}"
        # CuPy imports but the JIT/device path is broken here (e.g. Steam sandbox hides CUDA on the
        # CUDA-13 line). The bake still gets the GPU via the host venv fallback when a venv exists.
        return "GPU via venv (in-Blender CUDA unavailable)" if venv_exists else "CPU (GPU unavailable)"
    if pr["scipy_ok"]:
        return "CPU ready"
    if pr["gpu"]:
        return "Enable Compute for GPU"
    return "CPU (venv fallback)" if venv_exists else "Enable Compute (CPU)"
