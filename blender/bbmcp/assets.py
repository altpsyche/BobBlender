"""Biome manifest readers for the scatter / terrain / world pipeline.

A biome is a folder under `library/models/<biome>/` carrying a `manifest.json`. The canonical
biome is a block-out biome: its props are procedural proxies (bbmcp.proxies), its terrain is a
solid-tint material, and it references no external model files. This module only reads and
validates manifests; the proxy geometry and scatter layers are built by the panels + proxies.

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

    # Models are inert (no importer): scatter always uses the block-out proxies. Flag the block
    # so an author knows the files it names will not be placed, but do not check the files exist
    # or gate scatter kinds on them -- that would validate a dead path as if it were live.
    if man["models"]:
        warnings.append("models block is ignored: no model importer, scatter uses block-out "
                        "proxies (bbmcp.proxies). Remove the block or author it as a proxy biome")

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
            # texture set folder when a layer actually names one.
            if tex and not os.path.isdir(os.path.join(_textures_root(), tex)):
                warnings.append(f"terrain texture set missing: library/textures/{tex}")

    # World: only known bbt_env fields.
    for field in man["world"]:
        if field not in _WORLD_FIELDS:
            warnings.append(f"world field '{field}' unknown")

    return warnings


