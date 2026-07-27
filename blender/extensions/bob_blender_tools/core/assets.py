"""Biome manifest readers and the asset-pack resolver for the scatter / terrain / world pipeline.

Art lives OUTSIDE this repo, in asset packs. A pack is a plain folder with `models/<biome>/
manifest.json` biomes and `textures/<set>/` texture sets (optionally a `pack.json` root
manifest). Packs are discovered over an ordered search path (`asset_roots()`): the
`$BOB_ASSET_PACKS` env list, the addon-preference folder list, the dev repo `library/` when
present, and the block-out pack bundled inside the extension as the always-present floor. First
hit wins on a name collision, so a pack can override a bundled biome.

A biome is a folder `<pack>/models/<biome>/` carrying a `manifest.json`. The canonical biome is
a block-out biome: its props are procedural proxies (core.proxies), its terrain is a solid-tint
material, and it references no external model files. This module only reads and validates
manifests; the proxy geometry and scatter layers are built by the panels + proxies. It is
bpy-free (read by the panels, never importing them), so the pack-preference folders are pushed
in from the addon via `set_pref_roots()` rather than read from bpy here.

The manifest is read through one normalizing reader, `biome_manifest()`, which returns a
self-describing v2 dict with five sections:

    meta     : name / description / climate / source / license / version + optional
               "proxy": true (a proxy biome supplies geometry from bbmcp.proxies, so the
               validator skips the model-file and terrain-texture checks for it)
    models   : {kind: [entry, ...]} for the scatter kinds trees / rocks / grass / plants; kept
               for back-compat, empty for a proxy biome. An entry is a bare file string OR
               {"file", ...}
    terrain  : {"layers": [{"layer": "<preset>", "texture"?: "<set>"}, ...]}. A layer with no
               "texture" renders as a solid tint (the block-out default)
    scatter  : {kind: {"density", "scale", "min_normal_z", "align", ...}} the biome's
               placement recipe (the panel fills any missing key from its LAYER_TYPES)
    world    : {season, weather, time_of_day, ...} bbt_env defaults for the biome

Back-compatibility: a v1 flat manifest ({kind: [files], "terrain": {...}}) still works. The
reserved top-level keys are meta/models/terrain/scatter/world; ANY other top-level list is a
legacy model kind, folded under `models`. `validate_biome()` flags common authoring mistakes.
"""

import json
import os

# Reserved top-level manifest keys (a v2 section). ANY other top-level list is a legacy model
# kind (v1 back-compat), folded under `models` by biome_manifest.
_RESERVED_KEYS = ("meta", "models", "terrain", "scatter", "world")

# The scatter kinds the biome system knows. Mirrors scatter_panel.LAYER_TYPES (minus "empty");
# duplicated here so bbmcp stays free of the extension import, the same split the panel keeps
# for its Blender-side preset dicts.
_SCATTER_KINDS = ("trees", "rocks", "plants", "grass")

# Valid terrain layer-preset keys. Mirrors shaders_panel.TERRAIN_LAYER_PRESETS (same reason).
_TERRAIN_LAYER_KEYS = ("soil", "grass", "rock", "cliff", "scree", "sand")

# Valid world fields: the Scene.bbt_env property names (env.py BBT_EnvProps). A world block may
# only set these. Duplicated for the same acyclic-dependency reason (assets is read by the
# panels, not the other way round).
_WORLD_FIELDS = (
    "time_of_day", "year", "month", "day", "utc_offset", "latitude", "longitude",
    "season", "weather", "temperature", "wetness", "snow_line", "cloud_cover",
    "wind_direction", "wind_strength",
)


# -- Asset-pack search path ------------------------------------------------------------------
# realpath so a dev symlink install resolves to the real repo tree (the depth walk below counts
# real dirs, not the bl_ext symlink location).
def _here_up(n):
    p = os.path.realpath(__file__)
    for _ in range(n):
        p = os.path.dirname(p)
    return p


def _bundled_root():
    """The block-out pack bundled inside the extension: `<ext>/assets`. core/assets.py -> core
    -> bob_blender_tools -> assets. Always the floor of the search path."""
    return os.path.join(_here_up(2), "assets")


def _repo_library_root():
    """The dev asset pack: `<repo>/library`. core/assets.py is five levels below the repo root
    (core / bob_blender_tools / extensions / blender / repo). Absent on a packaged install
    (no repo above the addon), so it is filtered out there and only helps in-repo dev."""
    return os.path.join(_here_up(5), "library")


# Pack roots pushed in from the addon preferences (bpy-free here; the addon owns bpy). The env
# var and the bundled/dev roots are read directly.
_PREF_ROOTS = []

# The generated-output pack (`<output>/packs/generated`), pushed in the same way and for the same
# reason: the output folder is an addon preference and this module is bpy-free. Registered as a
# search root so anything a generator writes (the ComfyUI track, docs/COMFYUI.md) shows up in the
# biome and texture-set pickers with no configuration step.
_GENERATED_ROOT = None

# Pack roots an OP carried in, registered for the rest of the session. There are two generated
# packs in play whenever generation and Blender are different processes: the one the MCP tool wrote
# into (`paths.generated_pack()`, `$BOB_GENERATED` or `<workdir>/packs/generated`) and the one a
# LIVE addon registered from its own output-folder preference. When they disagree, a set that was
# just generated is invisible to the resolver and `apply_texture_set` fails with "no texture set"
# on a folder that exists. Every generation tool already returns the `pack_dir` it used; this is
# where an op hands that back so the resolver can see it.
_OP_ROOTS = []


def set_pref_roots(paths):
    """Register the addon-preference "Asset Pack Folders" list. Called by the addon on register,
    on a preference change, and by Rescan Asset Packs."""
    global _PREF_ROOTS
    _PREF_ROOTS = [str(p) for p in (paths or []) if p]


def set_generated_root(path):
    """Register the generated-output pack root. Called alongside set_pref_roots by the addon,
    which owns the output-folder preference. None or "" unregisters it."""
    global _GENERATED_ROOT
    _GENERATED_ROOT = str(path) if path else None


def add_pack_root(path):
    """Register an extra pack root for the rest of the session and return it (None for a falsy
    path). Idempotent, and ordered so the most recently added root is searched first.

    What an op's `pack_dir` argument does. It is deliberately not a per-call override threaded
    through every resolver: a texture set assigned from a generated pack has to stay resolvable
    afterwards too, or the material rebuilds a Shaders edit triggers drop back to a solid tint.
    """
    global _OP_ROOTS
    if not path:
        return None
    root = os.path.abspath(str(path))
    _OP_ROOTS = [root] + [r for r in _OP_ROOTS if r != root]
    return root


def op_roots():
    """The extra roots registered by `add_pack_root`, most recent first."""
    return list(_OP_ROOTS)


def generated_root():
    """The generated-output pack root: the one the addon registered, else `$BOB_GENERATED`, else None.

    The env var is what makes the pack reachable from a process the addon never registered in, which
    is now two of the three ways this code runs: the MCP server (no bpy at all) and the Blender the
    MCP executor spawns headlessly (the extension is imported, not enabled). Both need to agree with
    each other on where a generated asset landed, or `comfy_mesh` writes into one pack and
    `import_generated` reads from another. A registered root still wins, because in a live session
    the addon's own output-folder preference is the authority.
    """
    return _GENERATED_ROOT or (os.environ.get("BOB_GENERATED") or None)


def ensure_generated_pack(root):
    """Create `root` as a real generated pack (its `textures/`, `models/` and `pack.json`) and
    return it. Idempotent.

    Shared rather than duplicated: the addon calls it on register from its output-folder preference
    and the MCP server calls it from `$BOB_GENERATED`, and a pack that only one of them knows how to
    create is a pack the other cannot write into.
    """
    for sub in ("textures", "models"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    manifest = os.path.join(root, "pack.json")
    if not os.path.isfile(manifest):
        with open(manifest, "w") as fh:
            json.dump({"schema": 1, "id": "generated", "name": "Generated",
                       "description": "Data generated by BobBlenderTools"}, fh, indent=2)
    return root


def asset_roots():
    """The ordered, existing, de-duplicated pack roots. First hit wins downstream, most specific
    to least: 1. $BOB_ASSET_PACKS (os.pathsep-separated), 2. the roots an op carried in via
    `add_pack_root`, 3. the addon-preference folders, 4. the generated-output pack, 5. the dev repo
    library/ (in-repo only), 6. the bundled block-out pack (always present).

    Op roots sit above the preferences on purpose: a `pack_dir` an agent just wrote into is more
    specific than a folder list configured once, and it is exactly the disagreement that made a
    freshly generated texture set unreachable. $BOB_ASSET_PACKS still wins, because that is the
    user's own explicit override of the whole search path."""
    raw = []
    env = os.environ.get("BOB_ASSET_PACKS")
    if env:
        raw += env.split(os.pathsep)
    raw += _OP_ROOTS
    raw += _PREF_ROOTS
    # generated_root(), not _GENERATED_ROOT: the env fallback has to reach the RESOLVER, or a set
    # generated in a process the addon never registered in is written into a pack that
    # texture_set_dir cannot see (measured at G6, where the apply step failed on a set that existed).
    generated = generated_root()
    if generated:
        raw.append(generated)
    raw.append(_repo_library_root())
    raw.append(_bundled_root())
    seen, out = set(), []
    for r in raw:
        if not r:
            continue
        r = os.path.abspath(r)
        if r in seen:
            continue
        seen.add(r)
        if os.path.isdir(r):
            out.append(r)
    return out


def read_pack(root):
    """The `pack.json` at a root, or a minimal synthesized manifest (id/name from the folder)
    when absent or unreadable. Lets a folder be a valid pack with no manifest."""
    fid = os.path.basename(root.rstrip("/\\")) or root
    try:
        with open(os.path.join(root, "pack.json")) as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("id", fid)
            data.setdefault("name", fid)
            return data
    except (OSError, ValueError):
        pass
    return {"schema": 1, "id": fid, "name": fid}


def list_packs():
    """[(root, pack_manifest), ...] over the search path, for the Rescan report and diagnostics."""
    return [(root, read_pack(root)) for root in asset_roots()]


# -- Biome / texture resolution over the search path -----------------------------------------
def biome_dir(name):
    """The directory of biome `name`, resolved over the pack search path (first pack whose
    `models/<name>/manifest.json` exists wins). An absolute path is returned as-is. A name with
    no hit falls back to the first root's `models/<name>` so callers still get a path."""
    if os.path.isabs(name):
        return name
    for root in asset_roots():
        cand = os.path.join(root, "models", name)
        if os.path.isfile(os.path.join(cand, "manifest.json")):
            return cand
    roots = asset_roots()
    base = roots[0] if roots else _bundled_root()
    return os.path.join(base, "models", name)


def texture_set_dir(name):
    """The directory of texture set `name` (`<pack>/textures/<name>/`), first pack wins, or None
    when no pack provides it."""
    for root in asset_roots():
        cand = os.path.join(root, "textures", name)
        if os.path.isdir(cand):
            return cand
    return None


# A texture set names its files `<set>_<role>.<ext>`, which is what the Poly Haven sets on disk
# already use and what the generated pack writes (docs/COMFYUI.md, Contracts). Roles are listed
# in no particular order; the sampler picks the ones it consumes.
TEXTURE_MAP_ROLES = ("basecolor", "roughness", "metallic", "normal", "height", "ao")

# Extension preference order per role, first hit wins. The shipped sets mix .jpg (colour, AO,
# roughness) and .png (normal, height), so both have to be probed rather than assumed.
_TEXTURE_MAP_EXTS = (".png", ".jpg", ".jpeg", ".exr", ".tif", ".tiff")


def texture_set_maps(name):
    """{role: absolute path} for the maps texture set `name` actually carries on disk. Only
    roles with a file appear, so a set with no AO simply omits it and the sampler falls back to
    that map's identity. Empty dict when the set does not resolve in any pack.

    Resolution is by ROLE SUFFIX, not by the folder name. The convention is still
    `<set>_<role>.<ext>` and that is tried first, but a set whose folder was RENAMED (or symlinked
    under a friendlier name, which is what the redwood run did to reach the generated pack) used to
    resolve to zero maps and read on screen as a solid tint with no error anywhere -- the folder
    existed, so every check upstream passed. Falling back to any `*_<role>.<ext>` and then a bare
    `<role>.<ext>` makes a rename cosmetic instead of silently destructive.
    """
    base = texture_set_dir(name)
    if base is None:
        return {}
    stem = os.path.basename(base.rstrip("/\\"))
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return {}
    files = {e for e in entries if os.path.isfile(os.path.join(base, e))}
    out = {}
    for role in TEXTURE_MAP_ROLES:
        hit = next((f"{stem}_{role}{ext}" for ext in _TEXTURE_MAP_EXTS
                    if f"{stem}_{role}{ext}" in files), None)
        if hit is None:
            # Any stem, then the bare role. Extension preference is the outer loop so a set that
            # ships both a .png and a .jpg of one role resolves the same way it always did.
            hit = next((e for ext in _TEXTURE_MAP_EXTS for e in entries
                        if e in files and e.lower().endswith(f"_{role}{ext}")), None)
        if hit is None:
            hit = next((f"{role}{ext}" for ext in _TEXTURE_MAP_EXTS
                        if f"{role}{ext}" in files), None)
        if hit is not None:
            out[role] = os.path.join(base, hit)
    return out


def list_texture_sets():
    """Texture-set folder names carrying at least a basecolor map, unioned across all packs
    (first pack wins on a name collision), sorted. What the Shaders texture-set picker lists;
    the basecolor requirement keeps a stray folder out of the enum."""
    seen = set()
    for root in asset_roots():
        tex = os.path.join(root, "textures")
        if not os.path.isdir(tex):
            continue
        for n in sorted(os.listdir(tex)):
            if os.path.isdir(os.path.join(tex, n)):
                seen.add(n)
    return sorted(n for n in seen if texture_set_maps(n).get("basecolor"))


def list_biomes():
    """Biome folder names carrying a manifest.json, unioned across all packs (first pack wins on
    a name collision), sorted."""
    seen = {}
    for root in asset_roots():
        models = os.path.join(root, "models")
        if not os.path.isdir(models):
            continue
        for n in sorted(os.listdir(models)):
            if n in seen:
                continue
            if os.path.isfile(os.path.join(models, n, "manifest.json")):
                seen[n] = root
    return sorted(seen)


def _load_manifest(biome):
    """The raw manifest dict for a biome, or {} when missing/unreadable/not an object."""
    base = biome_dir(biome) if not os.path.isabs(biome) else biome
    try:
        with open(os.path.join(base, "manifest.json")) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# Defaults for the fields a GENERATED model entry carries (docs/COMFYUI.md, Contracts). They are
# defaulted here rather than read by a second loader, which is R11: `biome_manifest()` stays the
# one normalising reader and a caller never has to ask which schema version it is holding.
#
# `height_m` is the load-bearing one. Every image-to-3D model emits a unit-cube-normalised mesh, so
# an entry without a real-world height scatters as a toy; 1.0 is a deliberately obvious wrong
# answer rather than a silent guess, and `validate_biome` flags a generated entry that relies on it.
_MODEL_ENTRY_DEFAULTS = {"height_m": 1.0, "lod": [], "origin": "base", "faces": None}


def _norm_entries(entries):
    """Normalize a model kind's list to [{"file", ...}], dropping malformed entries. A bare
    string becomes {"file": string}; an object must carry a string "file". Every entry comes back
    with the generated-model fields present, so a caller reads `e["height_m"]` without a guard
    whether the manifest is v1, v2, hand-authored or generated."""
    out = []
    if not isinstance(entries, list):
        return out
    for e in entries:
        if isinstance(e, str):
            entry = {"file": e}
        elif isinstance(e, dict) and isinstance(e.get("file"), str):
            entry = dict(e)
        else:
            continue
        for key, default in _MODEL_ENTRY_DEFAULTS.items():
            entry.setdefault(key, list(default) if isinstance(default, list) else default)
        out.append(entry)
    return out


def biome_manifest(biome):
    """The biome's manifest normalized to v2: {meta, models, terrain, scatter, world}.

    Back-compatible. A v1 flat manifest ({kind: [files], "terrain": {...}}) is mapped to v2:
    each top-level list under a non-reserved key becomes a `models` kind; an explicit v2
    `models` object is used as-is; both have their entries normalized to {"file", ...}. Missing
    sections come back as sensible empties (meta with a titled name + inferred version, terrain
    None, scatter/world {}). All the accessors below and the panels read through this."""
    raw = _load_manifest(biome)
    folder = os.path.basename(biome.rstrip("/\\")) if os.path.isabs(biome) else biome

    # Models: an explicit models{} plus any legacy flat top-level kind (a list under a
    # non-reserved key). Explicit wins if a name appears both ways.
    models = {}
    explicit = raw.get("models")
    if isinstance(explicit, dict):
        for kind, entries in explicit.items():
            models[kind] = _norm_entries(entries)
    for key, val in raw.items():
        if key in _RESERVED_KEYS or key in models:
            continue
        if isinstance(val, list):
            models[key] = _norm_entries(val)  # legacy flat kind -> models

    raw_meta = raw.get("meta")
    meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
    meta.setdefault("name", folder.replace("_", " ").title())
    is_v2 = any(k in raw for k in ("meta", "models", "scatter", "world"))
    meta.setdefault("version", 2 if is_v2 else 1)

    terrain = raw.get("terrain")
    terrain = terrain if isinstance(terrain, dict) and terrain.get("layers") else None
    scatter = raw.get("scatter") if isinstance(raw.get("scatter"), dict) else {}
    world = raw.get("world") if isinstance(raw.get("world"), dict) else {}

    return {"meta": meta, "models": models, "terrain": terrain,
            "scatter": scatter, "world": world}


def biome_meta(biome):
    """The biome's meta/attribution block (name/description/climate/source/license/version)."""
    return biome_manifest(biome)["meta"]


def biome_scatter(biome):
    """The biome's scatter recipe: {kind: {density/scale/min_normal_z/align/...}}, or {} when
    the manifest carries none. Read by the Scatter panel's Biome Scatter (fills missing keys
    from LAYER_TYPES)."""
    return biome_manifest(biome)["scatter"]


def biome_world(biome):
    """The biome's world defaults: a subset of the bbt_env fields, or {} when none. Read by
    the World panel's Biome World / Apply Biome."""
    return biome_manifest(biome)["world"]


def biome_terrain(biome):
    """The biome's terrain spec ({"layers": [{"layer", "texture"}, ...]}), or None when the
    manifest carries none. Read by BobShaders to build a biome-matched terrain material."""
    return biome_manifest(biome)["terrain"]


def validate_biome(biome):
    """Static checks on a biome manifest, returned as a list of human-readable warnings (empty
    = clean). Catches the common authoring mistakes: an unknown terrain layer key, a missing
    terrain texture set, an unknown world field, and a malformed scatter config. Surfaced in the
    Apply operator reports and printed at build.

    Models block: there is no model importer (the glTF path was removed; the block-out proxies
    from bbmcp.proxies are the one geometry source). A `models` block is therefore ignored at
    build time. It is not validated as if it were used -- a single note flags that it is inert,
    so an author is not misled into thinking real model files will be placed."""
    raw = _load_manifest(biome)
    if not raw:
        return [f"{biome}: manifest.json missing or unreadable"]
    man = biome_manifest(biome)
    warnings = []

    # Models. A GENERATED pack (meta.generated) has a real importer as of G3
    # (`core.gen_assets.import_generated`), so its entries are checked rather than dismissed. A
    # hand-authored biome's models block is still inert: scatter uses the block-out proxies, and
    # the note says so rather than validating a dead path as if it were live.
    if man["models"] and man["meta"].get("generated"):
        base = biome_dir(biome) if not os.path.isabs(biome) else biome
        for kind, entries in sorted(man["models"].items()):
            for e in entries:
                if not os.path.isfile(os.path.join(base, e["file"])):
                    warnings.append(f"generated model missing on disk: {kind}/{e['file']}")
                if not e.get("height_m") or e["height_m"] == _MODEL_ENTRY_DEFAULTS["height_m"]:
                    warnings.append(f"generated model {e['file']} has no real height_m, so it "
                                    f"scatters at 1 m")
    elif man["models"]:
        warnings.append("models block is ignored: no model importer for a hand-authored biome, "
                        "scatter uses block-out proxies (bbmcp.proxies). Remove the block, or "
                        "mark the manifest meta.generated if a generator wrote it")

    # Scatter: each configured kind must be known, and its value must be a placement-settings
    # object (a non-dict crashes Biome Scatter).
    for kind, cfg in man["scatter"].items():
        if kind not in _SCATTER_KINDS:
            warnings.append(f"scatter kind '{kind}' unknown {list(_SCATTER_KINDS)}")
        if not isinstance(cfg, dict):
            warnings.append(f"scatter kind '{kind}': config must be an object of placement "
                            f"settings, got {type(cfg).__name__}")

    # Terrain: layers must be a list of {layer, texture} objects, each with a known
    # layer key and an existing texture set folder.
    if man["terrain"]:
        layers = man["terrain"].get("layers")
        if not isinstance(layers, list):
            warnings.append("terrain layers must be a list of {layer, texture} objects")
            layers = []
        for L in layers:
            if not isinstance(L, dict):
                warnings.append(f"terrain layer entry must be an object, got {type(L).__name__}")
                continue
            key = L.get("layer")
            if key not in _TERRAIN_LAYER_KEYS:
                warnings.append(f"terrain layer '{key}' unknown {list(_TERRAIN_LAYER_KEYS)}")
            tex = L.get("texture")
            # A layer with no texture renders as a solid tint (the default). Only validate the
            # texture set folder when a layer actually names one; search all packs for it.
            if tex and texture_set_dir(tex) is None:
                warnings.append(f"terrain texture set missing: textures/{tex} (in any pack)")

    # World: only known bbt_env fields.
    for field in man["world"]:
        if field not in _WORLD_FIELDS:
            warnings.append(f"world field '{field}' unknown")

    return warnings


