"""render: render the current scene to an image file, and return its path.

Closes the loop so an agent can SEE its result: build a scene over MCP, then render
a frame to a PNG it can read back. Runs bpy-side (in the live session or the headless
runner), so the output path it is given is already absolute (the agent-side tool
resolves a workdir-relative path before sending the op).

Engine enums are Blender 5.2: BLENDER_EEVEE (not _NEXT) and CYCLES. Cycles can render
on the GPU (CUDA/OPTIX) when a device is present; the GPU enable is best-effort and
falls back to CPU rather than failing the render.
"""

import bpy

_ENGINES = {"BLENDER_EEVEE", "CYCLES"}


def _get(params, key, default):
    v = params.get(key, default)
    return default if v is None else v


def _enable_cycles_gpu():
    """Best-effort: turn on the first available Cycles GPU backend. Returns the device
    type used ("CUDA"/"OPTIX"/...) or None when no GPU is available (stays on CPU)."""
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except (KeyError, AttributeError):
        return None
    for backend in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
        try:
            prefs.compute_device_type = backend
        except TypeError:
            continue  # backend not compiled in
        prefs.get_devices()
        gpus = [d for d in prefs.devices if d.type == backend]
        if gpus:
            for d in prefs.devices:
                d.use = d.type == backend
            return backend
    return None


def render(op: dict) -> dict:
    params = op.get("params", op)
    scene = bpy.context.scene

    camera_name = params.get("camera")
    if camera_name:
        cam = bpy.data.objects.get(camera_name)
        if cam is None or cam.type != "CAMERA":
            raise ValueError(f"render: no camera object named {camera_name!r}")
        scene.camera = cam
    if scene.camera is None:
        raise ValueError("render: no scene camera set (pass camera=, or add_camera first)")

    engine = _get(params, "engine", "BLENDER_EEVEE")
    if engine not in _ENGINES:
        raise ValueError(f"render: engine must be one of {sorted(_ENGINES)}, got {engine!r}")
    scene.render.engine = engine

    samples = int(_get(params, "samples", 64))
    device_info = engine
    if engine == "CYCLES":
        scene.cycles.samples = samples
        if _get(params, "device", "GPU") == "GPU":
            backend = _enable_cycles_gpu()
            scene.cycles.device = "GPU" if backend else "CPU"
            device_info = f"CYCLES/{backend or 'CPU'}"
        else:
            scene.cycles.device = "CPU"
            device_info = "CYCLES/CPU"
    else:
        scene.eevee.taa_render_samples = samples

    res = _get(params, "resolution", (1920, 1080))
    scene.render.resolution_x = int(res[0])
    scene.render.resolution_y = int(res[1])
    scene.render.resolution_percentage = int(_get(params, "resolution_percentage", 100))

    output = params.get("output")
    if not output:
        raise ValueError("render: 'output' path is required")
    scene.render.image_settings.file_format = _get(params, "file_format", "PNG")
    scene.render.filepath = output

    bpy.ops.render.render(write_still=True)

    info = f"{device_info} {scene.render.resolution_x}x{scene.render.resolution_y} spp={samples}"
    return {"op": "render", "created": [output], "info": info}
