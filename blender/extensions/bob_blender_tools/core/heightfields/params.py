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

PREVIEW_SIZE = 256    # bake(preview=True) resolution for a stack WITHOUT amplify
DEFAULT_SIZE = 768    # a full bake's default resolution

# A stack that ends in an `amplify` op runs its coarse macro at this fixed resolution, then amplify
# climbs to the bake `size` in resolution-doubling levels. Fixing the macro resolution (instead of
# running the whole stack at `size`) is what makes a preview a faithful PREFIX of a full bake: both
# share the identical coarse macro and the same doubling schedule, differing only in how far up they
# climb. A preview of an amplify preset bakes at AMPLIFY_PREVIEW (one climb level above the base).
AMPLIFY_BASE = 256
AMPLIFY_PREVIEW = 512

_DEFAULT_KNOBS = dict(
    preset="alpine", seed=7, size=DEFAULT_SIZE, backend="auto",
    relief=0.5, detail=0.5, erosion=0.5, warp=0.5, macro=None,
)

# Generators that take a procedural seed. Each is offset so the Seed knob varies
# every generator in a stack independently (two noise ops do not move in lockstep).
_SEED_OPS = ("noise", "dunes", "voronoi", "strata", "warp", "amplify", "rill")


def has_amplify(stack) -> bool:
    """True if the stack ends in (or contains) an amplify op -- its macro runs at AMPLIFY_BASE."""
    return any(op.get("kind") == "amplify" for op in stack)


def macro_size(stack, size) -> int:
    """The resolution the coarse macro runs at: AMPLIFY_BASE if the stack amplifies, else `size`."""
    return AMPLIFY_BASE if has_amplify(stack) else int(size)

# Ops whose slope-relaxation threshold can be authored as a real repose ANGLE (repose_deg) instead
# of a hand-picked normalised talus. Maps each op kind to the param name that carries that threshold.
_REPOSE_PARAM = {"thermal": "talus", "scarp": "talus", "fluvial": "talus", "deposit": "settle_talus"}


def _resolve_repose(stack, bake_res, relief_ratio):
    """Turn any op's `repose_deg` into a resolution-correct talus, in place.

    A preset may author a slope-relaxation pass by real angle (`repose_deg`) so the rendered slope
    holds that PHYSICAL angle at any bake resolution or tile size; here, once the bake resolution and
    the preset's relief ratio are known, that becomes the concrete normalised talus the engine op
    takes (see presets.talus_for_angle). Ops still take a raw `talus` when a structural, non-repose
    slope is wanted (cap-rock cliffs), so the two are not mutually exclusive."""
    for op in stack:
        if "repose_deg" not in op:
            continue
        key = _REPOSE_PARAM.get(op["kind"], "talus")
        op[key] = presets.talus_for_angle(op.pop("repose_deg"), bake_res, relief_ratio)
    return stack


def resolve_amplify_targets(stack, size, relief_ratio):
    """Set the resolution target and relief ratio on any amplify op, in place (neutral, no knob
    scaling). The preset+knobs bake path does this inside resolve_stack; the panel stack-editor
    mirror (gen_panel_presets) calls this directly so a committed editor stack carries a concrete
    `to`, not None, and its custom-stack bake climbs to the reference resolution."""
    for op in stack:
        if op.get("kind") == "amplify":
            op["to"] = int(size)
            op.setdefault("relief", float(relief_ratio))
    return stack


# The generator kinds a preset stack can open with. `with_macro` demotes whichever one it finds
# first, so a macro mask composes with every family (noise mountains, strata canyons, dune seas)
# instead of only the noise ones.
_GENERATORS = ("noise", "dunes", "voronoi", "strata")

# The macro mask's default share of the base relief, and the blur (as a fraction of the field width)
# that keeps it a mask. 0.6 leaves the preset a real 40% of the base, which is what stops a prompted
# silhouette from arriving as a bare blurred blob with erosion painted on top.
MACRO_WEIGHT = 0.6
MACRO_SMOOTH = 0.02


def with_macro(stack, path, *, weight=MACRO_WEIGHT, smooth=MACRO_SMOOTH, invert=False):
    """A copy of `stack` with a macro-mask generator as op 0 and its own generator demoted to a
    detail ADD of the remaining relief. The one place that composition is written.

    Why demote rather than insert-and-hope: every shipped preset opens with a generator whose `mix`
    is `replace`, so a mask prepended in front of one would be overwritten on the very next op and
    the whole feature would silently do nothing. The macro takes `weight` of the base relief and the
    preset's generator takes `1 - weight` on top, so the artist's landform and the family's
    character are a weighted sum, and the erosion that follows sees one field either way.

    Everything downstream is untouched: `fluvial`, `thermal` and `amplify` cannot tell a mask-based
    macro from a noise-based one, which is why the macro-heightmap family needed no new erosion path (R7).
    """
    out = copy.deepcopy(list(stack))
    weight = max(0.0, min(1.0, float(weight)))
    for op in out:
        if op.get("kind") in _GENERATORS:
            if op.get("mix", "replace") == "replace":
                op["mix"] = "add"
                op["amount"] = 1.0 - weight
            else:
                op["amount"] = float(op.get("amount", 1.0)) * (1.0 - weight)
            break
    return [{"kind": "macro", "path": str(path), "mix": "replace", "amount": weight,
             "smooth": float(smooth), "invert": bool(invert)}] + out


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


def resolve_stack(preset, *, relief=0.5, detail=0.5, erosion=0.5, warp=0.5, seed=7,
                  size=DEFAULT_SIZE):
    """Copy a preset stack and modulate it by the five global knobs, returning an engine-ready stack.

    All knobs at 0.5 reproduce the preset exactly (every factor is 1.0, no octave
    shift, only the seed is injected). Factors are centred on 0.5 so a knob reads
    the same way on any preset. `size` is the bake resolution any `repose_deg` pass is resolved
    against (see _resolve_repose), so the returned stack carries a concrete talus the engine op takes,
    never a raw angle; callers that bake at a non-default resolution pass their size."""
    stack = copy.deepcopy(presets.stack(preset))
    salt = _preset_salt(preset)
    rugged = 0.4 + 1.2 * float(relief)       # 0.4 .. 1.6  (x1.0 at 0.5)
    erode = 0.5 + 1.0 * float(erosion)       # 0.5 .. 1.5
    meander = 0.3 + 1.4 * float(warp)        # 0.3 .. 1.7
    sharp = 0.5 + 1.0 * float(detail)        # 0.5 .. 1.5
    oct_shift = int(round((float(detail) - 0.5) * 4))   # -2 .. +2 octaves
    relief_ratio = presets.relief(preset)
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
        elif kind == "rill":
            # Erosion knob deepens the dissection by adding groove iterations (more, denser gullies).
            op["iterations"] = _clampi(op.get("iterations", 10) * erode, 2, 40)
        elif kind == "glacial":
            # Erosion knob scales the ice-sculpting passes (more abrasion + planing = deeper troughs).
            op["iterations"] = _clampi(op.get("iterations", 60) * erode, 12, 160)
        elif kind == "thermal":
            op["iterations"] = _clampi(op.get("iterations", 4) * erode, 0, 60)
        elif kind == "sharpen":
            op["amount"] = op.get("amount", 0.5) * sharp
        elif kind == "amplify":
            # amplify climbs to the bake resolution; the Detail knob scales its detail amplitude,
            # matching how it scales sharpen. `relief` makes the aeolian repose settle scale-correct.
            op["to"] = int(size)
            op["relief"] = relief_ratio
            op["strength"] = op.get("strength", 0.025) * sharp
        # voronoi / terrace / curve / smooth / falloff keep their preset values;
        # their character is structural, not a global-knob axis.
    # A repose_deg pass becomes a concrete talus, resolved against the resolution the pass actually
    # RUNS at: the macro resolution (AMPLIFY_BASE) when the stack amplifies, else the bake size. The
    # amplify op resolves its own per-level repose internally, so only the macro passes are handled
    # here. This keeps a physical slope angle correct whether or not a cascade follows.
    _resolve_repose(stack, macro_size(stack, size), presets.relief(preset))
    return stack


def build_params(knobs: dict | None = None) -> dict:
    """Expand flat knobs (preset + globals) into a bake params dict with a resolved stack.

    `macro` is an optional dict of `with_macro` keywords (`path` and, optionally, `weight`,
    `smooth`, `invert`). It lands here rather than in the panel so the panel, the CLI and the MCP
    tool all get a prompted macro base from the same one line.
    """
    k = {**_DEFAULT_KNOBS, **(knobs or {})}
    stack = resolve_stack(k["preset"], relief=k["relief"], detail=k["detail"],
                          erosion=k["erosion"], warp=k["warp"], seed=k["seed"], size=int(k["size"]))
    if k.get("macro"):
        stack = with_macro(stack, **k["macro"])
    return {
        "size": int(k["size"]), "seed": int(k["seed"]), "backend": k["backend"],
        "preset": k["preset"], "stack": stack,
        "globals": {"relief": float(k["relief"]), "detail": float(k["detail"]),
                    "erosion": float(k["erosion"]), "warp": float(k["warp"])},
    }
