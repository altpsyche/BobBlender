"""Venv-side re-export of the ComfyUI client. There is ONE client and it is not here.

The client lives inside the extension, at
`blender/extensions/bob_blender_tools/core/comfy.py`, because Blender's bundled Python has no
`httpx` and the same code has to run on both interpreters (docs/COMFYUI.md, Bob-side constraint
1). It is stdlib only for that reason. This module exists so venv-side code can say
`from bobtools import comfyui` and get that single source instead of a second implementation that
drifts from it.

Until G1 this file WAS that second implementation: a dormant 68-line `httpx` client, with no
caller, that polled `/history` rather than the jobs API. That is the drift the single-source rule
exists to prevent, so it is gone, along with the `[comfyui]` extra in `pyproject.toml` that only
it needed.
"""

from . import _hfpath  # noqa: F401  (side effect: puts the extension's core/ on sys.path)

import comfy as _comfy  # noqa: E402  (the single source, importable only after _hfpath)
import comfy_maps as maps  # noqa: E402  (albedo -> roughness / height, same single-source rule)

ComfyError = _comfy.ComfyError

base_url = _comfy.base_url
reachable = _comfy.reachable
features = _comfy.features
has_jobs_api = _comfy.has_jobs_api
combo_options = _comfy.combo_options
queue = _comfy.queue
job = _comfy.job
cancel = _comfy.cancel
wait = _comfy.wait
images = _comfy.images
view = _comfy.view
load_workflow = _comfy.load_workflow
titles = _comfy.titles
template = _comfy.template
slugify = _comfy.slugify
unique_set_name = _comfy.unique_set_name
texture_set_from_prompt = _comfy.texture_set_from_prompt
