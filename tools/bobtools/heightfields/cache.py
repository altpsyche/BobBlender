"""Bake cache: a stable hash of the bake params.

The hash goes in the PNG's sidecar. A bake whose params hash matches the existing
sidecar is a no-op that returns the cached stats, so re-baking unchanged params
is free. The hash covers the recipe only, not the backend, so a CPU and a GPU
bake of the same params share a cache entry (the two are close but not
bit-identical; the flag on the sidecar records which produced it).
"""

import hashlib
import json

# Bump when the generation or erosion math changes in a way that alters output,
# so old sidecars do not falsely count as cache hits.
FORMAT_VERSION = 1


def params_hash(params: dict) -> str:
    payload = {"v": FORMAT_VERSION, "params": params}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:16]
