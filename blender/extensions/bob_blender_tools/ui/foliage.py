"""BobFoliage: the tree-authoring panel (docs/FOLIAGE.md 4.2, 4.3; BobFoliage).

A panel of its own rather than a fold of Scatter's, and that is the count forcing it: eight trunk
knobs plus six per level is around thirty, and burying a tree editor under the Active Layer would
cost scatter its own controls. Paths is the precedent -- BobSplines has its own panel and also feeds
scatter -- and this is the same shape.

**What does NOT move here is the decision.** Filling a scatter kind stays one choice in one place:
Make Proxies, Apply Biome, Generate Asset and Grow in Foliage sit together in Scatter, because
that is where an artist already picks between them. Grow sends you here with a tree already made;
this panel is where you then tune it. So the suite gains a panel and no duplicated decision.

The data model is the Scatter panel's, one capability over:

- a tree is an OBJECT carrying the `foliage` modifier, stamped `bbt_foliage` and filed in the
 BOB_Foliage collection, so the list is a `template_list` over real objects rather than a
 CollectionProperty of pointers that goes stale when one is deleted;
- the LIVE knobs (height, taper, the per-level shape, the cards, the wind) are the modifier's own
 inputs, drawn in place -- editing one is instant, there is no sync code, and there is nothing for
 the panel and the modifier to disagree about;
- `Scene.bbt_foliage` holds UI state only: which tree is active, and the STRUCTURAL choices staged
 for the next Build (levels, profile, the two texture sets, Skeleton Only).

**No panel state reaches a recipe except as a plain param.** Every operator here resolves its
context and calls `core/foliage_build.py`, which takes arguments and hands them to `build_geonodes`;
it imports no ui module and reads no PropertyGroup. That is why BobFoliage adds no MCP op and is not
on the live-bridge-only list every curve op is on (docs/MCP.md, known gap) -- the headless gate
builds trees through the same functions this panel does, with the addon not registered at all.

**Make Variants arrived with the variant pass**, and not before: a button that
reports "coming soon" teaches an artist to distrust every other button beside it. It is the last
hop of the track -- one authored tree becomes the pool a scatter layer instances -- so it closes the
loop the Scatter panel's Grow button opened, and it reports which `BOB_Assets_<Kind>` it filled so
an artist ends up back at Scatter holding the assets they just grew (docs/FOLIAGE.md 4.5).
"""

import random

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup, UIList

from ..core import assets, foliage_build, foliage_variants
from . import helpers

# Live knobs, grouped by the sub-panel that draws them. A name absent from this build's interface is
# skipped by `helpers.live_knobs`, which is what lets one list serve levels=1 and levels=4.
# `Taper Curve`, `Flare`, `Collar` and `Lobe` (the wood shaping) sit with the trunk rather than in a box of their
# own: they are all statements about how thick wood is where, which is what the Trunk box already is,
# and a fifth box called Shape next to one called Trunk would be a coin toss to an artist.
_TRUNK_KNOBS = ["Height", "Trunk Radius", "Taper", "Taper Curve", "Flare", "Collar", "Lobe",
                "Lean", "Gnarl", "Segments", "Branch Segments"]
_LEVEL_KNOBS = ["Branches", "Angle", "Length", "Radius", "Phyllotaxy", "Start", "Sag"]
_CARD_KNOBS = ["Cards", "Card Size", "Card Width", "Droop", "Spread",
               "Leaf Level", "Leaf Start", "Bark Scale", "Atlas Columns", "Atlas Rows"]
_WIND_KNOBS = ["Wind", "Wind Direction", "Sway", "Leaf Flutter"]

_SEED_KNOBS = {"Seed"}

# Species enum, cached module-side for the same enum-GC pitfall the texture-set and biome enums hit:
# Blender does not keep a reference to the strings an items callback returns.
_SPECIES_ITEMS = [("NONE", "(none)", "No species preset ships in any pack", "", 0)]
_SPECIES_IDS = {}

_SET_ITEMS = [("NONE", "(none)", "Solid tint: no texture set", "", 0)]
_SET_IDS = {"NONE": 0}


def _species_items(self, context):
    global _SPECIES_ITEMS
    items = []
    for name in assets.list_foliage_species():
        if name not in _SPECIES_IDS:
            _SPECIES_IDS[name] = len(_SPECIES_IDS) + 1
        spec = assets.foliage_species(name)
        meta = spec.get("meta", {})
        items.append((name, meta.get("name", name.replace("_", " ").title()),
                      meta.get("description", f"Grow a {name} ({meta.get('kind', 'trees')})"), "",
                      _SPECIES_IDS[name]))
    _SPECIES_ITEMS = items or [("NONE", "(none)", "No species preset ships in any pack", "", 0)]
    return _SPECIES_ITEMS


def _set_items(self, context):
    global _SET_ITEMS
    items = [("NONE", "(none)", "Solid tint: no texture set", "", 0)]
    for name in assets.list_texture_sets():
        if name not in _SET_IDS:
            _SET_IDS[name] = len(_SET_IDS)
        items.append((name, name.replace("_", " ").title(), f"Wear the {name} set", "",
                      _SET_IDS[name]))
    _SET_ITEMS = items
    return _SET_ITEMS


def _trees(context):
    return foliage_build.foliage_objects(context.scene)


def _active_tree(context):
    """The tree the sub-panels edit: the panel's active index, or the active object when it is a
 tree. Reading the ACTIVE OBJECT as a fallback is what makes clicking a tree in the viewport
 select it here too, which is the behaviour an artist expects from a per-object editor."""
    obj = context.active_object
    if foliage_build.is_foliage(obj):
        return obj
    trees = _trees(context)
    if not trees:
        return None
    scn = context.scene.bbt_foliage
    return trees[min(max(scn.active, 0), len(trees) - 1)]


def _coll(context):
    return bpy.data.collections.get(foliage_build.FOLIAGE_COLL)


def _structural(scn):
    """The staged structural choices, as build_geonodes params. `None` means "leave as built", so a
 freshly added species keeps its own levels until someone changes the number."""
    out = {"levels": int(scn.levels), "profile_segments": int(scn.profile_segments),
           "skeleton": bool(scn.skeleton)}
    if scn.bark_set != "NONE":
        out["bark_set"] = scn.bark_set
    if scn.atlas != "NONE":
        out["atlas"] = scn.atlas
    return out


class BBT_FoliageProps(PropertyGroup):
    """BobFoliage's UI state. Not the tree (that is the object and its modifier), not the world
 (that is bbt_env): only which tree is active and what the next Build should be."""

    active: IntProperty(name="Active Tree", default=0, min=0)
    species: EnumProperty(
        name="Species", items=_species_items,
        description="The species preset Add grows and Load applies. A species is DATA in a pack "
                    "(<pack>/foliage/<name>.json), so a pack can ship one and an artist can hand "
                    "one to someone else")
    levels: IntProperty(
        name="Levels", default=3, min=1, max=4,
        description="Branch levels below the trunk. Structural: three is a tree, one is a shrub, "
                    "and changing it rebuilds the graph")
    profile_segments: IntProperty(
        name="Profile", default=6, min=3, max=24,
        description="Sides of the tube swept along every limb. Structural, and the mesh's main "
                    "cost knob: the vertex count is exactly linear in it")
    skeleton: BoolProperty(
        name="Skeleton Only", default=False,
        description="Emit the curves and skip the sweep. Much faster to tune structure in, and the "
                    "only view where a detached branch is visible rather than hidden in a trunk")
    bark_set: EnumProperty(
        name="Bark", items=_set_items,
        description="The texture set the trunk and limbs wear. A species preset already names the "
                    "bark it wants, so this is for overriding that choice")
    atlas: EnumProperty(
        name="Leaf Atlas", items=_set_items,
        description="The leaf/needle atlas the cards sample. The set declares its own grid, so "
                    "assigning one needs no numbers")
    bark_prompt: StringProperty(
        name="Bark Prompt", default="rough conifer bark",
        description="What the bark is. Generation adds the measured grain clause for you: naming "
                    "the FEATURE and its direction is what makes bark run along the trunk")
    atlas_prompt: StringProperty(
        name="Leaf Prompt", default="spruce needle spray",
        description="What one leaf or needle spray is. Bob generates one sprite per cell and "
                    "composes the grid itself, because a diffusion model cannot be asked for a grid")
    atlas_cols: IntProperty(name="Columns", default=2, min=1, max=8)
    atlas_rows: IntProperty(name="Rows", default=2, min=1, max=8)
    gen_seed: IntProperty(
        name="Seed", default=0, min=0,
        description="Same prompt and seed give the same texture set")
    variant_count: IntProperty(
        name="Variants", default=foliage_variants.DEFAULT_VARIANTS, min=1, max=32,
        description="How many seeds to bake into the asset pool. Eight is enough that a repeat is "
                    "not findable in a frame and few enough to bake in a minute")
    variant_lods: BoolProperty(
        name="LOD Ladder", default=True,
        description="Also build each variant at two cheaper rungs, into BOB_Foliage_LODs. A "
                    "rebuild of the recipe at a lower branch depth, never a decimate, which would "
                    "spike the twigs and destroy the card quads")
    variant_pack: BoolProperty(
        name="Write to Pack", default=False,
        description="Also export the bake as portable assets in the generated pack. A packed "
                    "variant is FROZEN -- glTF carries no node group, so the wind and the leaf "
                    "season stay behind -- but its entry records the species and the seed, so a "
                    "Bob file can regrow the exact variant alive")


# -- Operators. Each resolves context and calls core/foliage_build; none builds a tree itself. ----
class BBT_OT_foliage_add(Operator):
    bl_idname = "bob_blender_tools.foliage_add"
    bl_label = "Add Tree"
    bl_description = ("Grow a tree from a species preset at the 3D cursor. Needs no ComfyUI "
                      "server: the geometry is a recipe and only the bark and leaf textures ever "
                      "come from generation")
    bl_options = {"REGISTER", "UNDO"}

    species: EnumProperty(name="Species", items=_species_items)

    def execute(self, context):
        name = self.species
        spec = assets.foliage_species(name)
        if not spec:
            self.report({"ERROR"}, f"No species preset '{name}' in any pack")
            return {"CANCELLED"}
        label = foliage_build.unique_name(spec["meta"].get("name", name.replace("_", " ").title()))
        obj = foliage_build.grow(label, dict(spec["params"], seed=random.randint(0, 99999)),
                                 species=name, scene=context.scene,
                                 location=context.scene.cursor.location.copy())
        if obj is None:
            self.report({"ERROR"}, "The foliage build produced no object")
            return {"CANCELLED"}
        for other in context.selected_objects:
            other.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        scn = context.scene.bbt_foliage
        scn.species = name
        trees = _trees(context)
        scn.active = trees.index(obj) if obj in trees else 0
        missing = ", ".join(v for _k, _l, v in assets.foliage_missing_sets(name))
        self.report({"INFO"}, f"Grew {obj.name}"
                              + (f"; solid tint until {missing} is generated" if missing else ""))
        return {"FINISHED"}


class BBT_OT_foliage_remove(Operator):
    bl_idname = "bob_blender_tools.foliage_remove"
    bl_label = "Remove Tree"
    bl_description = "Delete the active tree and its node group"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _active_tree(context)
        if obj is None:
            self.report({"ERROR"}, "No tree to remove")
            return {"CANCELLED"}
        name = obj.name
        group = bpy.data.node_groups.get(name)
        bpy.data.objects.remove(obj, do_unlink=True)
        if group is not None and group.users == 0:
            bpy.data.node_groups.remove(group)
        scn = context.scene.bbt_foliage
        scn.active = max(0, min(scn.active, len(_trees(context)) - 1))
        self.report({"INFO"}, f"Removed {name}")
        return {"FINISHED"}


class BBT_OT_foliage_duplicate(Operator):
    bl_idname = "bob_blender_tools.foliage_duplicate"
    bl_label = "Duplicate Tree"
    bl_description = ("Grow another tree of the active tree's species with a fresh seed. For a hero "
                      "tree placed by hand; a stand comes from Make Variants and a scatter layer")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _active_tree(context)
        if obj is None:
            self.report({"ERROR"}, "No tree to duplicate")
            return {"CANCELLED"}
        species = foliage_build.species_of(obj)
        params = foliage_build.build_params(obj, species=species)
        params["seed"] = random.randint(0, 99999)
        base = obj.name.split(".")[0]
        new = foliage_build.grow(foliage_build.unique_name(base), params, species=species,
                                 scene=context.scene, location=obj.location.copy())
        if new is None:
            self.report({"ERROR"}, "The foliage build produced no object")
            return {"CANCELLED"}
        trees = _trees(context)
        context.scene.bbt_foliage.active = trees.index(new) if new in trees else 0
        self.report({"INFO"}, f"Grew {new.name} at seed {params['seed']}")
        return {"FINISHED"}


class BBT_OT_foliage_load_species(Operator):
    bl_idname = "bob_blender_tools.foliage_load_species"
    bl_label = "Load Species"
    bl_description = ("Apply the chosen species preset to the ACTIVE tree. The tree keeps its "
                      "place and its identity -- a preset is params applied to a tree, not a new "
                      "tree -- so anything pointing at it still is")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _active_tree(context)
        scn = context.scene.bbt_foliage
        if obj is None:
            self.report({"ERROR"}, "Add or pick a tree first")
            return {"CANCELLED"}
        if scn.species == "NONE":
            self.report({"ERROR"}, "No species preset in any pack")
            return {"CANCELLED"}
        if foliage_build.load_species(obj, scn.species, scene=context.scene) is None:
            self.report({"ERROR"}, f"Species '{scn.species}' did not resolve")
            return {"CANCELLED"}
        warn = assets.validate_foliage_species(scn.species)
        if warn:
            print("[bob_blender_tools] foliage species warnings:", warn)
        self.report({"INFO"}, f"{obj.name} is now a {scn.species}")
        return {"FINISHED"}


class BBT_OT_foliage_build(Operator):
    bl_idname = "bob_blender_tools.foliage_build"
    bl_label = "Build This Tree"
    bl_description = ("Rebuild the active tree with the structural choices above (levels, profile, "
                      "the two texture sets, Skeleton Only). Tuned live knobs are kept by socket "
                      "name, so a rebuild costs no tuning")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _active_tree(context)
        if obj is None:
            self.report({"ERROR"}, "Add or pick a tree first")
            return {"CANCELLED"}
        obj = foliage_build.rebuild(obj, overrides=_structural(context.scene.bbt_foliage),
                                    scene=context.scene)
        self.report({"INFO"}, f"Rebuilt {obj.name}")
        return {"FINISHED"}


class BBT_OT_foliage_random_seed(Operator):
    bl_idname = "bob_blender_tools.foliage_random_seed"
    bl_label = "Reshuffle Seed"
    bl_description = "Draw a new seed for this tree. Live: the whole skeleton re-grows, no rebuild"
    bl_options = {"REGISTER", "UNDO"}

    socket: StringProperty(default="Seed")

    def execute(self, context):
        obj = _active_tree(context)
        inp = foliage_build.live_input(obj, self.socket) if obj is not None else None
        if inp is None:
            self.report({"ERROR"}, f"No '{self.socket}' knob on this tree")
            return {"CANCELLED"}
        inp.value = random.randint(0, 99999)
        obj.update_tag()
        return {"FINISHED"}


class BBT_OT_foliage_make_variants(Operator):
    bl_idname = "bob_blender_tools.foliage_make_variants"
    bl_label = "Make Variants"
    bl_description = ("Bake the active tree into N seeds in BOB_Assets_<Kind>, ready for a scatter "
                      "layer. The variants stay LIVE, so the stand keeps the world's wind: an "
                      "instanced tree is still re-evaluated per frame, and the cost is per variant "
                      "rather than per instance")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = _active_tree(context)
        if tree is None:
            self.report({"ERROR"}, "Add or pick a tree first")
            return {"CANCELLED"}
        scn = context.scene.bbt_foliage
        pack = None
        if scn.variant_pack:
            from .shaders import _generated_pack

            pack = _generated_pack()
            if not pack:
                self.report({"ERROR"}, "No generated pack folder (set an output folder in the "
                                       "add-on preferences), or turn Write to Pack off")
                return {"CANCELLED"}
        levels = foliage_variants.LOD_LEVELS if scn.variant_lods else (0,)
        report = foliage_variants.make_variants(
            tree, count=int(scn.variant_count), levels=levels, scene=context.scene,
            overrides=_structural(scn), pack_dir=pack)
        # Which collection it filled, because that is where the artist goes next: the Scatter panel
        # reads exactly this pool (docs/FOLIAGE.md 4.5, the loop closing in the other direction).
        rungs = ", ".join(f"LOD{level} {verts:,}v"
                          for level, verts in sorted(report["lod_verts"].items()))
        self.report({"INFO"}, f"{report['count']} variants in {report['collection']} ({rungs})"
                              + (f"; {len(report['pack'])} written to the pack" if pack else ""))
        return {"FINISHED"}


def _generate(self, context, kind):
    """Queue a bark or leaf-atlas job on the shared ComfyUI worker, then wear the result.

 The two jobs are `comfy.bark_set` and `comfy.leaf_atlas`, already MCP-exposed and gated
 (docs/FOLIAGE.md 3), so the button is assignment plus a press and not new plumbing. The set
 lands in the generated pack under the name the species already asks for where there is one,
 which is what makes "generate the bark" enough with no assignment step after it.
 """
    from ..core import comfy
    from .shaders import _COMFY_STATE, _comfy_job_running, _generated_pack, _submit

    scn = context.scene.bbt_foliage
    obj = _active_tree(context)
    if obj is None:
        self.report({"ERROR"}, "Add or pick a tree first")
        return {"CANCELLED"}
    pack = _generated_pack()
    if not pack:
        self.report({"ERROR"}, "No generated pack folder (set an output folder in the add-on "
                               "preferences)")
        return {"CANCELLED"}
    if _comfy_job_running():
        self.report({"WARNING"}, "A ComfyUI job is already running")
        return {"CANCELLED"}
    species = foliage_build.species_of(obj)
    wanted = assets.foliage_species(species).get("params", {}) if species else {}
    seed, name = int(scn.gen_seed), obj.name
    if kind == "bark":
        prompt = (scn.bark_prompt or "").strip()
        set_name = wanted.get("bark_set") or None
    else:
        prompt = (scn.atlas_prompt or "").strip()
        set_name = wanted.get("atlas") if wanted.get("atlas") != "leaf_atlas_blockout" else None
    if not prompt:
        self.report({"ERROR"}, "Describe the bark or the leaf first")
        return {"CANCELLED"}
    cols, rows = int(scn.atlas_cols), int(scn.atlas_rows)

    # Everything below runs on the worker thread: no bpy, no context, only what is captured here.
    def work(job):
        if kind == "bark":
            return comfy.bark_set(prompt, pack, name=set_name, seed=seed,
                                  on_progress=job.report)
        return comfy.leaf_atlas(prompt, pack, cols=cols, rows=rows, name=set_name, seed=seed,
                                on_progress=job.report)

    def landed(job):
        result = job.result or (None, {})
        got = result[0] if isinstance(result, tuple) else None
        _COMFY_STATE.update(ok=True, detail=f"{kind} set {got}" if got else f"{kind} generated")
        if not got:
            return
        # Rebuild the tree wearing it. The resolver reads the generated pack the moment it is
        # written (the pack_dir plumbing the redwood fixes added), so this needs no import step.
        tree = bpy.data.objects.get(name)
        if tree is not None:
            foliage_build.rebuild(tree, overrides={("bark_set" if kind == "bark" else "atlas"): got})
        setattr(scn, "bark_set" if kind == "bark" else "atlas", got)

    _submit(f"{kind}: {prompt[:32]}", work, landed)
    self.report({"INFO"}, f"Generating the {kind} in the background")
    return {"FINISHED"}


class BBT_OT_foliage_generate_bark(Operator):
    bl_idname = "bob_blender_tools.foliage_generate_bark"
    bl_label = "Generate"
    bl_description = ("Generate a bark texture set with ComfyUI and put it on this tree. Runs in "
                      "the background. The grain clause that makes bark run ALONG the trunk is "
                      "added for you and measured on the way out")
    bl_options = {"REGISTER"}

    def execute(self, context):
        return _generate(self, context, "bark")


class BBT_OT_foliage_generate_atlas(Operator):
    bl_idname = "bob_blender_tools.foliage_generate_atlas"
    bl_label = "Generate"
    bl_description = ("Generate a leaf/needle atlas with ComfyUI and put it on this tree's cards. "
                      "One sprite per cell, composed and oriented Bob-side, because a diffusion "
                      "model returns five sprays in a ring when asked for a 2x2 grid")
    bl_options = {"REGISTER"}

    def execute(self, context):
        return _generate(self, context, "atlas")


class BBT_UL_foliage(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        row = layout.row(align=True)
        species = foliage_build.species_of(item)
        row.label(text=item.name, icon="OUTLINER_OB_CURVES")
        if species:
            sub = row.row()
            sub.enabled = False
            sub.label(text=species.replace("_", " "))
        row.prop(item, "hide_viewport", text="", emboss=False,
                 icon="HIDE_ON" if item.hide_viewport else "HIDE_OFF")


class BBT_PT_foliage(Panel):
    # Plain "Foliage", the way every other top-level panel in the category is a plain noun: World,
    # Biome, Paths, Scatter, Shaders, Atmosphere. "BobFoliage" is the TRACK's name (docs/FOLIAGE.md)
    # and it stays that in the code and the docs; a header is not the place to say it, and it was the
    # only one of the seven that did.
    bl_label = "Foliage"
    bl_idname = "BBT_PT_foliage"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_order = 5  # authoring, right after the Scatter stage that routes here (docs/FOLIAGE.md 4.5)
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        scn = context.scene.bbt_foliage
        layout = self.layout
        tree = _active_tree(context)
        species = foliage_build.species_of(tree) if tree is not None else ""
        header = f"{tree.name} ({species.replace('_', ' ')})" if (tree and species) else (
            tree.name if tree else None)
        helpers.context_header(layout, "Tree", header, icon="OUTLINER_OB_CURVES",
                                  empty="Add a tree to grow one.")

        coll = _coll(context)
        row = layout.row()
        if coll is not None and coll.objects:
            row.template_list("BBT_UL_foliage", "", coll, "objects", scn, "active", rows=3)
        else:
            row.label(text="No trees yet", icon="INFO")
        col = row.column(align=True)
        col.operator_menu_enum("bob_blender_tools.foliage_add", "species", text="", icon="ADD")
        col.operator("bob_blender_tools.foliage_remove", text="", icon="REMOVE")
        col.operator("bob_blender_tools.foliage_duplicate", text="", icon="DUPLICATE")
        if tree is None:
            return

        # Species: staged then applied (the heavy-idiom convention), because loading one replaces every
        # shape param on the tree. It keeps the object, so it is not as heavy as a Build -- but it
        # is not a look tweak either, and firing it on the pick would lose a tuned tree to a
        # mis-click in a dropdown.
        helpers.staged_preset_row(layout, scn, "species",
                                  "bob_blender_tools.foliage_load_species",
                                  text="Species", apply_text="Load Species",
                                  note="replaces this tree's shape; keeps its place and identity")


class BBT_PT_foliage_shape(Panel):
    bl_label = "Shape"
    bl_idname = "BBT_PT_foliage_shape"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_foliage"

    def draw(self, context):
        layout = self.layout
        scn = context.scene.bbt_foliage
        tree = _active_tree(context)
        if tree is None:
            layout.label(text="Add or pick a tree to edit it.", icon="INFO")
            return

        # Structural group : these change what is built, so they apply on a Build press and not
        # from a callback -- rebuilding from an update callback is the re-entrancy the Scatter panel
        # avoids the same way.
        box = layout.box()
        box.label(text="Structural (Build to apply)")
        row = box.row(align=True)
        row.prop(scn, "levels")
        row.prop(scn, "profile_segments")
        box.prop(scn, "skeleton")
        helpers.structural_action(box, "bob_blender_tools.foliage_build",
                                  note="rebuilds this tree's graph (keeps tuned knobs)")

        layout.label(text="Live knobs (instant)")
        helpers.live_knobs(layout, tree, ["Seed"],
                           seed_op="bob_blender_tools.foliage_random_seed", seed_names=_SEED_KNOBS)
        helpers.live_knobs(layout, tree, _TRUNK_KNOBS)
        for level in range(1, 5):
            names = [f"L{level} {k}" for k in _LEVEL_KNOBS]
            mod = next((m for m in tree.modifiers if m.type == "NODES"), None)
            if mod is None or mod.node_group is None:
                break
            present = {it.name for it in mod.node_group.interface.items_tree}
            if names[0] not in present:
                break
            sub = layout.box()
            sub.label(text=f"Level {level}")
            helpers.live_knobs(sub, tree, names)


class BBT_PT_foliage_leaves(Panel):
    bl_label = "Leaves"
    bl_idname = "BBT_PT_foliage_leaves"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_foliage"

    def draw(self, context):
        layout = self.layout
        tree = _active_tree(context)
        if tree is None:
            layout.label(text="Add or pick a tree to edit it.", icon="INFO")
            return
        helpers.live_knobs(layout, tree, _CARD_KNOBS)
        note = layout.row()
        note.enabled = False
        note.label(text="Cards 0 leaves a bare skeleton", icon="INFO")


class BBT_PT_foliage_wind(Panel):
    bl_label = "Wind"
    bl_idname = "BBT_PT_foliage_wind"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_foliage"

    def draw(self, context):
        layout = self.layout
        tree = _active_tree(context)
        if tree is None:
            layout.label(text="Add or pick a tree to edit it.", icon="INFO")
            return
        # Wind and its direction are OWNED by the world when there is one: the applier writes them
        # on every world change, so an edit here would be overwritten by the next slider drag in
        # World. Greyed rather than hidden, which is the "grey the inert knob" idiom Paths and the
        # scatter masks use -- the value is real and worth reading, it is just not yours to set.
        env = getattr(context.scene, "bbt_env", None)
        helpers.live_knobs(layout, tree, _WIND_KNOBS[:2], enabled=env is None)
        helpers.live_knobs(layout, tree, _WIND_KNOBS[2:])
        note = layout.column()
        note.enabled = False
        if env is not None:
            note.label(text=f"World wind {env.wind_strength:.1f} at {env.wind_direction:.0f} deg "
                            f"drives these; Sway and Flutter are this tree's own", icon="INFO")
            note.label(text=f"Autumn colour follows the world season ({env.season})")
        else:
            note.label(text="No world state: these hold whatever they were last set to",
                       icon="INFO")


class BBT_PT_foliage_variants(Panel):
    bl_label = "Variants"
    bl_idname = "BBT_PT_foliage_variants"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_foliage"

    def draw(self, context):
        layout = self.layout
        scn = context.scene.bbt_foliage
        tree = _active_tree(context)
        if tree is None:
            layout.label(text="Add or pick a tree to bake it.", icon="INFO")
            return
        kind = foliage_variants.variant_kind(tree)
        row = layout.row(align=True)
        row.prop(scn, "variant_count")
        row.prop(scn, "variant_lods", toggle=True)
        layout.prop(scn, "variant_pack")
        # A structural action, drawn the way Build is: this replaces a pool a scatter layer may
        # already be instancing, so it is a press and never a callback.
        helpers.structural_action(layout, "bob_blender_tools.foliage_make_variants",
                                  note=f"fills BOB_Assets_{kind.capitalize()} for a scatter layer")
        pooled = foliage_variants.variant_summary(kind)
        cap = layout.column()
        cap.enabled = False
        if pooled:
            cap.label(text=f"{len(pooled)} in BOB_Assets_{kind.capitalize()}: "
                           f"{pooled[0][1]:,} verts each at LOD0", icon="OUTLINER_COLLECTION")
            cap.label(text="they stay live, so the stand feels the world's wind")
        else:
            cap.label(text=f"nothing baked yet; the pool is BOB_Assets_{kind.capitalize()}",
                      icon="INFO")


class BBT_PT_foliage_textures(Panel):
    bl_label = "Textures"
    bl_idname = "BBT_PT_foliage_textures"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_foliage"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        from .scatter import _comfy_reachable_cached

        layout = self.layout
        scn = context.scene.bbt_foliage
        tree = _active_tree(context)
        if tree is None:
            layout.label(text="Add or pick a tree to edit it.", icon="INFO")
            return
        state = _comfy_reachable_cached()
        ready = bool(state.get("ok"))
        if not ready:
            cap = layout.row()
            cap.enabled = False
            cap.label(text=state.get("detail") or "ComfyUI not connected", icon="UNLINKED")

        species = foliage_build.species_of(tree)
        missing = dict((k, v) for k, _l, v in assets.foliage_missing_sets(species)) if species \
            else {}
        for label, prop, prompt_prop, op, key in (
                ("Bark", "bark_set", "bark_prompt",
                 "bob_blender_tools.foliage_generate_bark", "bark_set"),
                ("Leaf Atlas", "atlas", "atlas_prompt",
                 "bob_blender_tools.foliage_generate_atlas", "atlas")):
            box = layout.box()
            box.label(text=label, icon="TEXTURE")
            box.prop(scn, prop, text="")
            if key in missing:
                cap = box.row()
                cap.enabled = False
                cap.label(text=f"{missing[key]} not in any pack yet: solid tint until generated",
                          icon="INFO")
            box.prop(scn, prompt_prop, text="")
            if key == "atlas":
                row = box.row(align=True)
                row.prop(scn, "atlas_cols")
                row.prop(scn, "atlas_rows")
            run = box.row()
            run.enabled = ready
            run.operator(op, icon="SHADERFX")
        helpers.seed_row(layout, scn, "gen_seed", "bob_blender_tools.foliage_random_seed",
                         text="Texture Seed")
        cap = layout.row()
        cap.enabled = False
        cap.label(text="assigning a set rebuilds on the next Build", icon="INFO")


CLASSES = (
    BBT_FoliageProps,
    BBT_OT_foliage_add,
    BBT_OT_foliage_remove,
    BBT_OT_foliage_duplicate,
    BBT_OT_foliage_load_species,
    BBT_OT_foliage_build,
    BBT_OT_foliage_random_seed,
    BBT_OT_foliage_make_variants,
    BBT_OT_foliage_generate_bark,
    BBT_OT_foliage_generate_atlas,
    BBT_UL_foliage,
    BBT_PT_foliage,
    BBT_PT_foliage_shape,
    BBT_PT_foliage_leaves,
    BBT_PT_foliage_wind,
    BBT_PT_foliage_variants,
    BBT_PT_foliage_textures,
)


def register():
    from . import world

    _SPECIES_IDS.clear()  # a reload may have added a pack
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bbt_foliage = PointerProperty(type=BBT_FoliageProps)
    # The world feed: raising Wind Strength moves every tree with no rebuild and no per-tree press.
    world.register_applier(foliage_build.apply_world_wind)


def unregister():
    from . import world

    world.unregister_applier(foliage_build.apply_world_wind)
    del bpy.types.Scene.bbt_foliage
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
