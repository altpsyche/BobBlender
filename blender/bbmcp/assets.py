"""Real CC0 model assets for the scatter (BobShaders whole-look).

Imports glTF models downloaded under `library/models/<biome>/` into the scatter asset
collections (`BOB_Assets_<Kind>`), replacing the block-out proxies so the scatter instances
real, geographically-coherent meshes (all from one Poly Haven scan location). bpy-only.

glTF, not .blend: it keeps the download light and self-describes its PBR textures. Assets import
with their native materials and become BobShaders by Convert - automatically when a biome is
applied (Apply Biome's "weather scattered assets"), or manually from the Shaders panel's scatter
view. Convert routes only Base Color / Roughness / Metallic through S_SurfaceMaster and leaves
Alpha, Normal, and Emission untouched, so trees and grass keep their alpha-leaf cutout while
gaining per-instance variation and the weather layer (docs/SCATTER-SHADING-UX.md). GN instancing
references each mesh once, so even a multi-million-poly scanned mesh is memory-cheap to scatter;
keep density modest for render time.

A biome folder carries a `manifest.json`. It is read through one normalizing reader,
`biome_manifest()`, which returns a self-describing v2 dict with five sections:

    meta     : name / description / climate / source / license / version (attribution)
    models   : {kind: [entry, ...]} for the scatter kinds trees / rocks / grass / plants;
               an entry is a bare file string OR {"file", "scale"?, "rotation"?, "weight"?,
               "max_polys"?} (rotation is XYZ degrees on import, scale multiplies, weight
               biases a random pick, max_polys triggers a decimate when exceeded)
    terrain  : {"layers": [{"layer": "<preset>", "texture": "<set>"}, ...]} so a terrain
               comes with the right library texture sets
    scatter  : {kind: {"density", "scale", "min_normal_z", "align", ...}} the biome's
               placement recipe (the panel fills any missing key from its LAYER_TYPES)
    world    : {season, weather, time_of_day, ...} bbt_env defaults for the biome

Back-compatibility: a v1 flat manifest ({kind: [files], "terrain": {...}}) still works. The
reserved top-level keys are meta/models/terrain/scatter/world; ANY other top-level list is a
legacy model kind, folded under `models`. So the readers below (biome_models/biome_scatter/
biome_world/biome_meta and the existing biome_terrain/list_biomes) speak v2 while old folders
keep functioning unchanged, and `validate_biome()` flags the common authoring mistakes.
"""

import json
import math
import os

import bpy
from mathutils import Euler, Matrix

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
    "season", "weather", "temperature", "wetness", "snow", "cloud_cover",
    "wind_direction", "wind_strength",
)


def biome_dir(name):
    """`library/models/<name>`, resolved from this file's repo location."""
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(repo, "library", "models", name)


def _models_root():
    return os.path.dirname(biome_dir("_"))  # library/models


def _textures_root():
    return os.path.join(os.path.dirname(_models_root()), "textures")  # library/textures


def list_biomes():
    """Biome folder names under library/models/ that carry a manifest.json."""
    root = _models_root()
    if not os.path.isdir(root):
        return []
    return sorted(n for n in os.listdir(root)
                  if os.path.isfile(os.path.join(root, n, "manifest.json")))


def _load_manifest(biome):
    """The raw manifest dict for a biome, or {} when missing/unreadable/not an object."""
    base = biome_dir(biome) if not os.path.isabs(biome) else biome
    try:
        with open(os.path.join(base, "manifest.json")) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _norm_entries(entries):
    """Normalize a model kind's list to [{"file", ...}], dropping malformed entries. A bare
    string becomes {"file": string}; an object must carry a string "file"."""
    out = []
    if not isinstance(entries, list):
        return out
    for e in entries:
        if isinstance(e, str):
            out.append({"file": e})
        elif isinstance(e, dict) and isinstance(e.get("file"), str):
            out.append(dict(e))
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


def biome_models(biome):
    """The biome's models: {kind: [{"file", "scale"?, "rotation"?, "weight"?, "max_polys"?}]}.
    v1 flat kinds are folded in. Read by populate_scatter_assets."""
    return biome_manifest(biome)["models"]


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
    = clean). Catches the common authoring mistakes: a missing model file, a malformed model
    entry, a scatter kind with no models to place, an unknown terrain layer key, a missing
    terrain texture set, and an unknown world field. Surfaced in the Import/Apply operator
    reports and printed at import. Poly-budget over-runs are reported separately at import,
    where the mesh is actually counted."""
    raw = _load_manifest(biome)
    if not raw:
        return [f"{biome}: manifest.json missing or unreadable"]
    base = biome_dir(biome) if not os.path.isabs(biome) else biome
    man = biome_manifest(biome)
    warnings = []

    # Models: known kind, well-formed entries, files present, sane max_polys. Compare the
    # normalized count against the raw list to catch entries the normalizer dropped.
    for kind, entries in man["models"].items():
        if kind not in _SCATTER_KINDS:
            warnings.append(f"model kind '{kind}' is not a scatter kind {list(_SCATTER_KINDS)}")
        for e in entries:
            if not os.path.isfile(os.path.join(base, e["file"])):
                warnings.append(f"model file missing: {kind}/{e['file']}")
            mp = e.get("max_polys")
            if mp is not None and (isinstance(mp, bool) or not isinstance(mp, (int, float)) or mp <= 0):
                warnings.append(f"model {kind}/{e['file']}: max_polys must be a positive number")
    for kind, rawlist in _raw_model_lists(raw).items():
        dropped = len(rawlist) - len(man["models"].get(kind, []))
        if dropped > 0:
            warnings.append(f"model kind '{kind}': {dropped} malformed entry/entries ignored "
                            "(want a file string or an object with a \"file\")")

    # Scatter: each configured kind must be a known kind with models to place, and
    # its value must be a placement-settings object (a non-dict crashes Biome Scatter).
    for kind, cfg in man["scatter"].items():
        if kind not in _SCATTER_KINDS:
            warnings.append(f"scatter kind '{kind}' unknown {list(_SCATTER_KINDS)}")
        elif not man["models"].get(kind):
            warnings.append(f"scatter kind '{kind}' has no models to place")
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
            if not tex:
                warnings.append(f"terrain layer '{key}' has no texture set")
            elif not os.path.isdir(os.path.join(_textures_root(), tex)):
                warnings.append(f"terrain texture set missing: library/textures/{tex}")

    # World: only known bbt_env fields.
    for field in man["world"]:
        if field not in _WORLD_FIELDS:
            warnings.append(f"world field '{field}' unknown")

    return warnings


def _raw_model_lists(raw):
    """The raw (pre-normalization) model lists per kind, for the validator's dropped-entry
    check. Same reserved-key/legacy-flat logic as biome_manifest."""
    out = {}
    explicit = raw.get("models")
    if isinstance(explicit, dict):
        for kind, entries in explicit.items():
            if isinstance(entries, list):
                out[kind] = entries
    for key, val in raw.items():
        if key in _RESERVED_KEYS or key in out:
            continue
        if isinstance(val, list):
            out[key] = val
    return out


def _assets_collection(kind):
    """The scatter asset collection for a kind, created if absent. Deliberately NOT linked to
    the scene (the scatter instances it; the source objects should not render directly)."""
    name = f"BOB_Assets_{kind.capitalize()}"
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    return coll


def _clear_collection(coll):
    for o in list(coll.objects):
        coll.objects.unlink(o)
        if o.users == 0:
            bpy.data.objects.remove(o, do_unlink=True)


def _tri_count(mesh):
    """The mesh's triangle count (ngons/quads counted as their triangulation)."""
    mesh.calc_loop_triangles()
    return len(mesh.loop_triangles)


def _decimate_to(o, max_polys):
    """Collapse-decimate a mesh object in place so its triangle count is at most max_polys, and
    return (before, after). A no-op when already under budget. max_polys is an explicit per-asset
    opt-in (a manifest entry sets it); assets without it are never decimated, so the default is
    still "leave the mesh, alpha leaf cards included, alone". Applied by baking the modifier into
    the mesh via the evaluated depsgraph (the headless-safe apply that needs no context override).
    Collapse keeps UV data on the surviving verts, so the asset's textures still map."""
    before = _tri_count(o.data)
    if before <= max_polys:
        return before, before
    mod = o.modifiers.new("BOB_Decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.use_collapse_triangulate = True
    mod.ratio = max(0.001, float(max_polys) / before)
    dg = bpy.context.evaluated_depsgraph_get()
    baked = bpy.data.meshes.new_from_object(o.evaluated_get(dg))
    old = o.data
    o.modifiers.remove(mod)
    o.data = baked
    if old.users == 0:
        bpy.data.meshes.remove(old)
    return before, _tri_count(baked)


def _override_matrix(rotation, scale):
    """A world-space override transform from a manifest entry: rotation (XYZ degrees) applied
    about the origin, then a uniform scale multiply. Identity when both are None."""
    M = Matrix()
    if rotation is not None:
        M = Euler([math.radians(a) for a in rotation], "XYZ").to_matrix().to_4x4() @ M
    if scale is not None:
        M = Matrix.Diagonal((float(scale), float(scale), float(scale), 1.0)) @ M
    return M


def import_gltf(path, scale=None, rotation=None, max_polys=None):
    """Import a glTF and return its mesh objects, unparented with their transform baked into the
    mesh (so a scattered instance starts clean) and the glTF's empties/armatures removed.

    Per-asset overrides from the manifest entry (all optional): max_polys collapse-decimates a
    too-heavy mesh to budget; rotation (XYZ degrees) and scale (uniform multiply) are baked into
    the mesh after the glTF's own transform, to fix an asset's orientation or size on import."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == "MESH"]
    for o in meshes:
        if max_polys is not None:
            b, a = _decimate_to(o, max_polys)
            if a < b:
                print(f"[bbmcp.assets] decimated {os.path.basename(path)}: {b} -> {a} tris")
        mw = o.matrix_world.copy()
        o.parent = None
        o.matrix_world = mw
        o.data.transform(o.matrix_world)  # bake the Y-up->Z-up + placement into the mesh
        o.matrix_world = Matrix()
        if rotation is not None or scale is not None:
            o.data.transform(_override_matrix(rotation, scale))  # orientation/size fix, about origin
    for o in new:
        if o.type != "MESH":
            bpy.data.objects.remove(o, do_unlink=True)
    return meshes


def populate_scatter_assets(biome):
    """Replace the block-out proxies in each BOB_Assets_<Kind> with the biome's real meshes.

    Reads the normalized models (v1 flat kinds and v2 models{} alike), so a folder authored
    either way populates the same shared collections. Reuses an existing asset collection (so a
    scatter's Collection Info keeps pointing at it and its instances update live), else creates
    it. Returns {kind: mesh count}."""
    base = biome_dir(biome) if not os.path.isabs(biome) else biome
    counts = {}
    for kind, entries in biome_models(biome).items():
        coll = _assets_collection(kind)
        _clear_collection(coll)
        n = 0
        for entry in entries:
            for o in import_gltf(os.path.join(base, entry["file"]),
                                 scale=entry.get("scale"),
                                 rotation=entry.get("rotation"),
                                 max_polys=entry.get("max_polys")):
                for c in list(o.users_collection):
                    c.objects.unlink(o)
                coll.objects.link(o)
                n += 1
        counts[kind] = n
    return counts
