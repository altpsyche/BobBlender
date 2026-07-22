"""Expand a preset plus the five curated global knobs into a bake params dict.

An artist turns five sliders -- Relief, Detail, Erosion, Warp, Seed -- on top of a
chosen landscape preset. This module is the one place that turns those choices into
the op stack the engine runs, so the panel, the CLI, and the MCP tool all share it
instead of each hand-writing a stack.

Each knob is [0, 1] with 0.5 meaning "the preset exactly as authored", and maps to
ONE clear lever per op kind so the response is predictable across every family:

  Relief   ruggedness    -> generator detail-strength / dune sharpness
  Detail   feature size  -> generator octaves / dune frequency / sharpen amount
  Erosion  incision      -> fluvial + thermal iteration counts
  Warp     meander       -> generator domain-warp amplitude
  Seed     variation     -> a decorrelated seed into every procedural generator

Generation and erosion are resolution-independent (world-sampled noise, physical
stream-power exponents), so a preview and a full bake are the same landform; there
is no per-resolution density to scale any more.
"""

import copy

from . import presets

PREVIEW_SIZE = 256    # bake(preview=True) resolution
DEFAULT_SIZE = 768    # a full bake's default resolution

_DEFAULT_KNOBS = dict(
    preset="alpine", seed=7, size=DEFAULT_SIZE, backend="auto",
    relief=0.5, detail=0.5, erosion=0.5, warp=0.5,
)

# Generators that take a procedural seed. Each is offset so the Seed knob varies
# every generator in a stack independently (two noise ops do not move in lockstep).
_SEED_OPS = ("noise", "dunes", "voronoi", "warp")


def default_knobs() -> dict:
    """A copy of the knob defaults, so callers can start from a known baseline."""
    return dict(_DEFAULT_KNOBS)


def _clampi(v, lo, hi):
    return max(lo, min(hi, int(round(v))))


def _preset_salt(name: str) -> int:
    """A stable per-preset seed offset so two presets that share the same base generator
    (e.g. the noise-first mountain and lowland stacks) do not resolve to the identical macro
    skeleton at the same Seed. Deterministic (no hashing that varies by run)."""
    s = 0
    for ch in name:
        s = (s * 131 + ord(ch)) & 0x7fffffff
    return s % 9973


def resolve_stack(preset, *, relief=0.5, detail=0.5, erosion=0.5, warp=0.5, seed=7):
    """Copy a preset stack and modulate it by the five global knobs.

    All knobs at 0.5 reproduce the preset exactly (every factor is 1.0, no octave
    shift, only the seed is injected). Factors are centred on 0.5 so a knob reads
    the same way on any preset."""
    stack = copy.deepcopy(presets.stack(preset))
    salt = _preset_salt(preset)
    rugged = 0.4 + 1.2 * float(relief)       # 0.4 .. 1.6  (x1.0 at 0.5)
    erode = 0.5 + 1.0 * float(erosion)       # 0.5 .. 1.5
    meander = 0.3 + 1.4 * float(warp)        # 0.3 .. 1.7
    sharp = 0.5 + 1.0 * float(detail)        # 0.5 .. 1.5
    oct_shift = int(round((float(detail) - 0.5) * 4))   # -2 .. +2 octaves
    for i, op in enumerate(stack):
        kind = op["kind"]
        if kind in _SEED_OPS:
            op["seed"] = int(seed) + 17 * i + salt
        if kind == "noise":
            op["detail_strength"] = op.get("detail_strength", 0.6) * rugged
            op["octaves"] = _clampi(op.get("octaves", 6) + oct_shift, 1, 10)
            op["warp"] = op.get("warp", 60.0) * meander
        elif kind == "dunes":
            op["sharpness"] = op.get("sharpness", 0.5) * (0.6 + 0.8 * float(relief))
            op["frequency"] = op.get("frequency", 3.0) * (0.7 + 0.6 * float(detail))
            op["warp"] = op.get("warp", 0.14) * meander
        elif kind == "warp":
            op["amount"] = op.get("amount", 0.04) * meander
        elif kind in ("fluvial", "pipe_hydraulic"):
            op["iterations"] = _clampi(op.get("iterations", 60) * erode, 4, 400)
        elif kind == "thermal":
            op["iterations"] = _clampi(op.get("iterations", 4) * erode, 0, 60)
        elif kind == "sharpen":
            op["amount"] = op.get("amount", 0.5) * sharp
        # voronoi / terrace / curve / smooth / falloff keep their preset values;
        # their character is structural, not a global-knob axis.
    return stack


def build_params(knobs: dict | None = None) -> dict:
    """Expand flat knobs (preset + globals) into a bake params dict with a resolved stack."""
    k = {**_DEFAULT_KNOBS, **(knobs or {})}
    stack = resolve_stack(k["preset"], relief=k["relief"], detail=k["detail"],
                          erosion=k["erosion"], warp=k["warp"], seed=k["seed"])
    return {
        "size": int(k["size"]), "seed": int(k["seed"]), "backend": k["backend"],
        "preset": k["preset"], "stack": stack,
        "globals": {"relief": float(k["relief"]), "detail": float(k["detail"]),
                    "erosion": float(k["erosion"]), "warp": float(k["warp"])},
    }
