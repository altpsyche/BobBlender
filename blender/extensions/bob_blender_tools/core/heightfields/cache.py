"""Bake cache: a stable hash of the RESOLVED bake recipe.

The hash goes in the PNG's sidecar. A bake whose recipe hash matches the existing
sidecar is a no-op that returns the cached stats, so re-baking unchanged params is
free. The caller (pipeline.bake) hashes the fully resolved recipe -- size, seed,
the resolved backend name (not "auto", so two machines that resolve it differently
do not share an entry), the scaled pass list, and the generate block -- plus a
fingerprint of the erosion/generation source. That source fingerprint means editing
the math (not just the params) invalidates old sidecars automatically, instead of
relying on a hand-bumped version number.
"""

import hashlib
import json
from pathlib import Path

# Files whose contents define a bake's output. A change to any of them shifts the
# fingerprint, so old sidecars stop counting as cache hits with no manual bump.
_SOURCE_FILES = ("engine.py", "generate.py", "ops_generate.py", "ops_erode.py",
                 "ops_filter.py", "ops_select.py", "ops_carve.py", "presets.py",
                 "params.py", "pipeline.py")


def _source_sig() -> str:
    h = hashlib.sha256()
    here = Path(__file__).resolve().parent
    for name in _SOURCE_FILES:
        try:
            h.update(here.joinpath(name).read_bytes())
        except OSError:
            h.update(b"?")
    return h.hexdigest()[:12]


_SOURCE_SIG = _source_sig()


def params_hash(params: dict) -> str:
    """Hash a resolved recipe dict. Include the source fingerprint so a code change
    to the erosion/generation math invalidates old sidecars automatically."""
    payload = {"src": _SOURCE_SIG, "params": params}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:16]
