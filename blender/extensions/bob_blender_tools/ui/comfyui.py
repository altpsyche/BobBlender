"""The ComfyUI panel: what the generator is doing, and what it produced.

Every generation action in the suite runs on one background worker (`core/comfy_jobs.py`), and until
this panel existed nothing said so. The block lived inside the collapsed **Advanced** panel, next to
the MCP bridge and the pack rescan -- so the artist-facing half of generation was filed under dev
tooling -- and the job row it drew was built from `comfy_jobs.active()`, which means a job
DISAPPEARED from the UI the moment it finished. The artist's report was exactly that:

    "I clicked the button in advance section. I dont know if the stylisation is ended or not."

Both halves of that are addressed here. The panel is its own pipeline stage (`bl_order = 8`, ahead of
Advanced, which moves to 9), and it lists the session's jobs whether they are running, finished or
failed, each with its wall clock, its thumbnail, and a button that opens the folder its output landed
in. A result an artist cannot find is a result they do not have.

**Why this is named for ComfyUI and not "Generate".** It generates nothing. Every action that does
lives with the thing it produces -- texture sets and Paint (stylised) under Shaders, Generate Asset
under Scatter -- so a panel called Generate would have been a stage that owns no generators, and the
name would not have said what it holds. What it holds is one external tool's connection, queue and
output, which is also what the add-on preferences already call it (ComfyUI URL, ComfyUI Folder).

**What lives here and what does not.** This panel owns the SERVICE (is the server up, how much of the
card is free, what is queued, what came back) and the one action whose output is not scene data:
Stylise Last Render, which makes a pitch frame. The actions that DO produce scene data stay with the
pipeline stage that owns their result -- texture sets and Paint (stylised) under Shaders, Generate
Asset under Scatter -- because a panel hop to texture a material would be worse than the problem this
fixes. Wherever the button was, the progress and the image show up here.

The service OPERATORS (Test Connection, Free VRAM, Start/Stop Server) stay in the addon root: they
own a subprocess handle whose lifetime is the addon's, not this panel's. This module draws them.
"""

import os

import bpy
from bpy.props import FloatProperty, IntProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

# How many finished results are remembered, and how many of those are drawn with a thumbnail. The
# list is session state: it is what "did my earlier one finish?" reads, so it outlives the job
# registry's own `active()` and is cleared on file load like the registry is.
RESULTS_KEPT = 8
RESULTS_DRAWN = 5

# Loaded previews, path -> collection key, so several thumbnails can be on screen at once. The
# staged-variant picker in `ui/shaders.py` had its own single-slot version of this that cleared the
# collection on every change of pick; one implementation serves both, and a results list needs more
# than one image live.
_previews = None
_loaded = {}

# What finished, newest first. Each entry is {label, image, folder, seconds, error}.
_results = []


def thumbnail(path):
    """The icon id for an image on disk, or 0 when there is nothing to show.

    Keyed on the file's IDENTITY -- path, size and mtime -- not on its path. A preview collection
    caches by key and never re-reads the file, so keying on the path alone shows the picture that
    was there the first time it was drawn. That is not hypothetical: the panel reported a restyle
    while showing the previous one, because the two presses wrote the same filename and the second
    press's image never reached the screen (`comfy.unique_file_name` is the other half of that fix).

    0 is also what a `--background` Blender returns for a perfectly good preview (there is no icon
    manager without a UI), which is why every caller treats it as "draw no thumbnail" rather than as
    an error.
    """
    global _loaded
    if _previews is None or not path or not os.path.isfile(path):
        return 0
    try:
        stat = os.stat(path)
    except OSError:
        return 0
    ident = (path, stat.st_size, int(stat.st_mtime))
    key = _loaded.get(ident)
    if key is None:
        key = f"bob_{len(_loaded)}_{int(stat.st_mtime)}_{os.path.basename(path)}"
        try:
            _previews.load(key, path, "IMAGE")
        except (KeyError, RuntimeError):
            return 0
        _loaded[ident] = key
        # Bounded, because a long session of generating would otherwise keep every preview it ever
        # drew. The cap is the same order as the list this serves.
        while len(_loaded) > RESULTS_KEPT * 2:
            oldest = next(iter(_loaded))
            del _previews[_loaded.pop(oldest)]
    entry = _previews.get(key)
    return entry.icon_id if entry else 0


def record(label, *, image=None, folder=None, seconds=0.0, error=None):
    """Record what a finished job produced, for the results list to draw.

    Called from the `landed` callback of whichever operator ran, which is the one place that already
    knows the shape of its own result: a stylise returns one PNG, a texture set returns a directory
    of variants, a paint returns five maps and a material. Nothing here has to know any of those
    shapes, which is why this takes the three values a reader needs rather than a result object.

    `folder` defaults to the image's own directory, because that is what Open Folder wants and every
    caller would otherwise compute it identically.
    """
    if folder is None and image:
        folder = os.path.dirname(os.path.abspath(image))
    _results.insert(0, {"label": str(label), "image": image or "", "folder": folder or "",
                        "seconds": float(seconds), "error": str(error) if error else ""})
    del _results[RESULTS_KEPT:]


def results():
    return list(_results)


def clear_results():
    _results.clear()


class BBT_OT_open_folder(Operator):
    bl_idname = "bob_blender_tools.open_folder"
    bl_label = "Open Folder"
    bl_description = ("Open the folder this output was written to in the system file browser. The "
                      "generated pack's staging folder is where undecided results live, and it is "
                      "not somewhere an artist should have to go looking for")

    path: StringProperty(default="", subtype="DIR_PATH")

    def execute(self, context):
        target = bpy.path.abspath(self.path or "")
        if not target or not os.path.isdir(target):
            self.report({"ERROR"}, f"No such folder: {target or '(none)'}")
            return {"CANCELLED"}
        try:
            bpy.ops.wm.path_open(filepath=target)
        except RuntimeError as exc:  # no desktop handler (a headless or minimal session)
            self.report({"WARNING"}, f"Could not open it here; the folder is {target} ({exc})")
            return {"CANCELLED"}
        return {"FINISHED"}


def _installed_loras():
    """The LoRA filenames this server has, from the CACHED `/object_info` only.

    Cached-only on purpose: this feeds an operator enum, and Blender evaluates enum items while it
    draws the menu. A fetch there would be a socket call inside a draw, which is the freeze the
    cached status line exists to avoid -- and `/object_info` is several MB. The cache is filled by
    Test Connection and by any graph preflight, so the honest empty state is "press Test Connection",
    not a stall.
    """
    from ..core import comfy

    if comfy.base_url() not in comfy._OBJECT_INFO:
        return None
    try:
        return comfy.combo_options("LoraLoader", "lora_name",
                                   info=comfy._OBJECT_INFO[comfy.base_url()])
    except (KeyError, TypeError):
        return []


class BBT_OT_pick_lora(Operator):
    bl_idname = "bob_blender_tools.pick_lora"
    bl_label = "Pick LoRA"
    bl_description = ("Choose a style LoRA from the ones this ComfyUI server actually has. The field "
                      "stays a plain filename so a .blend saved with a LoRA still opens on a machine "
                      "that does not have it -- a dynamic enum would raise on the assignment instead")

    def _items(self, context):
        names = _installed_loras()
        if names is None:
            return [("", "press Test Connection first", "The server's node list has not been read yet")]
        if not names:
            return [("", "no LoRAs installed on this server", "Drop one in ComfyUI's models/loras")]
        return [(n, os.path.splitext(os.path.basename(n))[0], n) for n in names]

    name: bpy.props.EnumProperty(name="LoRA", items=_items)

    def execute(self, context):
        context.scene.bbt_stylise.lora = self.name or ""
        self.report({"INFO"}, f"LoRA: {self.name or 'none'}")
        return {"FINISHED"}


class BBT_OT_clear_lora(Operator):
    bl_idname = "bob_blender_tools.clear_lora"
    bl_label = "Clear LoRA"
    bl_description = ("Run with no LoRA. Empty removes the LoraLoader from the graph entirely rather "
                      "than running it at zero strength, because a placeholder filename fails the "
                      "server's validator on a machine with none installed")

    def execute(self, context):
        context.scene.bbt_stylise.lora = ""
        return {"FINISHED"}


class BBT_StyliseProps(PropertyGroup):
    """What a stylised restyle needs, for both routes that do one (docs/GENERATION.md).

    Two consumers, one set of knobs, because they are the same graph: "Stylise Last Render" restyles
    one camera frame, and Shaders > Paint (stylised) restyles a turntable of an object and projects
    it back. A second property group would have been the same four fields under different names, and
    an artist who set a style for one would find the other had not heard of it.

    Short, not ten fields: the ControlNet strengths, the sampler and the negative prompt are values
    in `core/comfy.py` because they have measured defaults. Strength is the one knob that genuinely
    trades style against silhouette; `views` and `size` are the paint route's own two, and both
    change what the route COSTS rather than only how it looks.
    """

    prompt: StringProperty(
        name="Style",
        description=("The look to push the render towards. Bob appends a clause naming the "
                     "composition, camera and layout, because a style prompt that does not is a "
                     "prompt for a different picture"),
        default="painted concept art, warm evening light",
    )
    denoise: FloatProperty(
        name="Strength",
        description=("How far from the render the restyle may travel, and the knob that decides "
                     "whether your GEOMETRY is reinterpreted or merely painted. Measured on one "
                     "block-out cube, same seed and prompt: at 0.55 it comes back a painted cube "
                     "with a whole city invented around it, because the depth pass says cube; at "
                     "0.85 the same cube becomes rooftop architecture and the framing survives. "
                     "0.55 is right for restyling a FINISHED render, 0.8 and up for turning a proxy "
                     "into a picture of the real thing"),
        default=0.55, min=0.1, max=0.95,
    )
    samples: IntProperty(
        name="Samples",
        description="Render samples for the frame Bob stylises. The depth and normal passes are "
                    "constant per pixel, so they always cost one sample",
        default=64, min=1, max=4096,
    )
    lora: StringProperty(
        name="LoRA",
        description=("Optional style LoRA, by filename as ComfyUI lists it. Empty removes the "
                     "LoRA node from the graph entirely rather than running it at zero strength"),
        default="",
    )
    views: IntProperty(
        name="Views",
        description=("Turntable views around the object for the paint route. This is the RING "
                     "count; Bob adds a high and a low view on top, because a ring alone leaves a "
                     "closed shape's top and underside to the hole fill. Every view is one "
                     "ComfyUI job, so this is the route's main cost knob"),
        default=6, min=3, max=16,
    )
    size: IntProperty(
        name="Size",
        description=("Render and texture resolution for the paint route. The projection reads "
                     "render pixels into texels, so a texture much larger than the render gains "
                     "nothing"),
        default=1024, min=256, max=2048, step=256,
    )
    seed: IntProperty(
        name="Seed",
        description="The same seed and prompt repaint the same look; change it to reroll",
        default=0, min=0,
    )


class BBT_OT_comfy_stylise(Operator):
    bl_idname = "bob_blender_tools.comfy_stylise"
    bl_label = "Stylise Last Render"
    bl_description = ("Render the current camera with TRUE depth and normal passes, then restyle "
                      "the frame in ComfyUI under both as ControlNet. The passes are Blender's own "
                      "geometry, not an estimate, which is the whole point of this route. Output is "
                      "a pitch frame beside the render, never geometry")
    bl_options = {"REGISTER"}

    def execute(self, context):
        from ..core import assets, comfy, gen_views, render
        from .shaders import _COMFY_STATE, _comfy_job_running, _submit

        scene = context.scene
        props = scene.bbt_stylise
        if scene.camera is None:
            self.report({"ERROR"}, "The scene has no camera to render from")
            return {"CANCELLED"}
        if _comfy_job_running():
            self.report({"WARNING"}, "A ComfyUI job is already running")
            return {"CANCELLED"}
        pack = assets.generated_root()
        if not pack:
            self.report({"ERROR"}, "No generated pack folder (set an output folder in the "
                                   "add-on preferences)")
            return {"CANCELLED"}

        out_dir = os.path.join(pack, "_staging", "stylise")
        stem = comfy.slugify(props.prompt or "stylise")
        # The render is main-thread work by nature, so it happens HERE, before the job is queued:
        # a render inside the worker would touch bpy off the main thread, which is the one thing
        # the job model forbids (the threading rule).
        try:
            shot = gen_views.render_passes(out_dir, stem, samples=int(props.samples),
                                           resolution=max(scene.render.resolution_x,
                                                          scene.render.resolution_y),
                                           transparent=False)
        except Exception as exc:  # a render failure is a message, not a traceback in the console
            self.report({"ERROR"}, f"Render failed: {exc}")
            return {"CANCELLED"}
        # Hand the card back before the restyle asks for it: a Cycles or EEVEE frame and an SDXL job
        # in one session are the VRAM-handback rule's two halves, and this is the half Bob controls.
        render.release_gpu()

        prompt, denoise = props.prompt, float(props.denoise)
        lora = (props.lora or "").strip() or None
        # A unique name per press, the same rule `unique_file_name`'s docstring gives for a control
        # export: a second restyle is a NEW frame, not a replaced one. It used to overwrite, which
        # destroyed the previous frame and left every result row pointing at one file -- so a panel
        # that had just reported a restyle was showing the one before it.
        target = comfy.unique_file_name(out_dir, stem + "_styled", ".png")

        def work(job):
            return comfy.stylize_render(shot["beauty"], target, prompt,
                                        depth=shot["depth"], normal=shot["normal"],
                                        denoise=denoise, size=shot["resolution"], lora=lora,
                                        on_queued=job.note_prompt_id, on_progress=job.report)

        def landed(job):
            info = job.result or {}
            seconds = info.get("seconds", 0)
            _COMFY_STATE.update(ok=True, detail=f"stylised in {seconds:.0f}s: "
                                               f"{os.path.basename(info.get('path', ''))}")
            record(f"stylise: {prompt[:40]}", image=info.get("path"), seconds=seconds)

        _submit(f"stylise: {prompt[:32]}", work, landed)
        self.report({"INFO"}, f"Rendered {os.path.basename(shot['beauty'])} with depth and normal "
                              f"passes; stylising in the background")
        return {"FINISHED"}


def draw_jobs(layout):
    """Every job this session, running first, then what finished. The answer to "is it done yet".

    Running jobs come from the registry (which knows their progress line and their clock) and
    finished ones from `record`, because the registry's `active()` drops a job the moment it lands --
    which is precisely how a finished stylise used to leave no trace in the UI at all.
    """
    from ..core import comfy_jobs

    running = comfy_jobs.active()
    for job in running:
        box = layout.box()
        row = box.row(align=True)
        row.label(text=f"{job.label} ({job.seconds:.0f}s)", icon="SORTTIME")
        row.operator("bob_blender_tools.comfy_cancel", text="", icon="X").job_id = job.id
        line = box.row()
        line.enabled = False
        line.label(text=job.progress or job.state)

    done = results()
    if not done:
        if not running:
            cap = layout.row()
            cap.enabled = False
            cap.label(text="nothing generated yet this session")
        return
    layout.label(text="Results (this session)", icon="IMAGE_DATA")
    for entry in done[:RESULTS_DRAWN]:
        box = layout.box()
        row = box.row(align=True)
        row.label(text=f"{entry['label']} ({entry['seconds']:.0f}s)",
                  icon="ERROR" if entry["error"] else "CHECKMARK")
        if entry["folder"]:
            row.operator("bob_blender_tools.open_folder", text="",
                         icon="FILE_FOLDER").path = entry["folder"]
        if entry["error"]:
            line = box.row()
            line.enabled = False
            line.label(text=entry["error"][:90])
            continue
        icon_id = thumbnail(entry["image"])
        if icon_id:
            box.template_icon(icon_value=icon_id, scale=8)
        if entry["image"]:
            name = box.row()
            name.enabled = False
            name.label(text=os.path.basename(entry["image"]))
    if len(done) > RESULTS_DRAWN:
        more = layout.row()
        more.enabled = False
        more.label(text=f"and {len(done) - RESULTS_DRAWN} earlier this session")


def draw_service(layout):
    """The server block: is it up, how much of the card is free, and the two buttons that change
    that. Reads cached state only -- a socket call inside a draw handler freezes the UI for the
    timeout in exactly the case the row exists to report."""
    from .. import _COMFY_SERVICE

    ok = _COMFY_SERVICE["ok"]
    layout.label(text=f"{_COMFY_SERVICE['url'] or 'not checked'}: {_COMFY_SERVICE['detail']}",
                 icon="PROP_ON" if ok else ("PROP_OFF" if ok is False else "QUESTION"))
    row = layout.row(align=True)
    row.operator("bob_blender_tools.comfy_test", icon="LINKED")
    row.operator("bob_blender_tools.comfy_free", icon="TRASH")
    row = layout.row(align=True)
    row.operator("bob_blender_tools.comfy_start", icon="PLAY")
    row.operator("bob_blender_tools.comfy_stop", icon="PAUSE")


class BBT_PT_comfyui(Panel):
    bl_label = "ComfyUI"
    bl_idname = "BBT_PT_comfyui"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_order = 8  # after the authoring stages, before Advanced (docs/CONVENTIONS.md)

    def draw(self, context):
        layout = self.layout
        layout.label(text="ComfyUI (generation): optional, never required", icon="SHADERFX")
        draw_service(layout)
        layout.separator()
        draw_jobs(layout)


class BBT_PT_comfyui_stylise(Panel):
    bl_label = "Stylise Render"
    bl_idname = "BBT_PT_comfyui_stylise"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_comfyui"

    def draw(self, context):
        layout = self.layout
        props = context.scene.bbt_stylise
        col = layout.column(align=True)
        col.prop(props, "prompt")
        row = col.row(align=True)
        row.prop(props, "denoise")
        row.prop(props, "samples")
        # A LoRA is a filename on the SERVER, so it is picked from what the server has rather than
        # typed: an unguessable string in a text field is a validator failure waiting to happen.
        row = col.row(align=True)
        row.operator_menu_enum("bob_blender_tools.pick_lora", "name",
                               text=f"LoRA: {os.path.basename(props.lora)}" if props.lora
                               else "LoRA: none")
        if props.lora:
            row.operator("bob_blender_tools.clear_lora", text="", icon="X")
        hint = col.row()
        hint.enabled = False
        hint.label(text="Strength 0.55 paints your render; 0.8+ lets it reinterpret a block-out")
        run = col.row()
        # A restyle needs a camera to render from, and saying so before the press beats a
        # post-click error (the empty-state rule).
        run.enabled = context.scene.camera is not None
        run.operator("bob_blender_tools.comfy_stylise", icon="SHADERFX")
        cap = col.row()
        cap.enabled = False
        cap.label(text="renders the camera plus true depth and normal, then restyles it"
                       if context.scene.camera is not None else "no camera in the scene to render")


CLASSES = (
    BBT_StyliseProps,
    BBT_OT_open_folder,
    BBT_OT_pick_lora,
    BBT_OT_clear_lora,
    BBT_OT_comfy_stylise,
    BBT_PT_comfyui,
    BBT_PT_comfyui_stylise,
)


def register():
    global _previews
    _previews = bpy.utils.previews.new()
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bbt_stylise = bpy.props.PointerProperty(type=BBT_StyliseProps)


def unregister():
    global _previews, _loaded
    del bpy.types.Scene.bbt_stylise
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews, _loaded = None, {}
    clear_results()
