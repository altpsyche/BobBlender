"""Recipe registry. A recipe is a build(ng, out, params) function that fills a
node group. Register one with the @recipe decorator and add it to the import
line below so its decorator runs.
"""

_RECIPES = {}


def recipe(name):
    def register(fn):
        _RECIPES[name] = fn
        return fn

    return register


def get(name):
    return _RECIPES.get(name)


def names():
    return sorted(_RECIPES)


# Warnings a recipe raised about its own params, drained by `build_geonodes` into the op result.
#
# The case this exists for: a scatter layer binds its emitter and its asset collection BY NAME, and
# `bpy.data.objects.get()` on a typo returns None, so the layer builds, reports success, and scatters
# nothing. That is the worst shape a failure can take over MCP, where nobody is looking at the
# viewport. A warning rather than a raise, because an unset emitter is legitimate in some panel flows
# (a layer built before its target exists) and raising would change what the panel does.
_WARNINGS = []


def warn(message):
    """Record a params problem that is worth reporting but not worth refusing to build over."""
    _WARNINGS.append(str(message))


def drain_warnings():
    """Take and clear the warnings recorded during the current build."""
    out, _WARNINGS[:] = list(_WARNINGS), []
    return out


def resolve_named(kind, name, *, what="", required=False):
    """A `bpy.data.<kind>` member by name, warning (or raising) when the name resolves to nothing.

    `kind` is a `bpy.data` collection name ("objects", "collections"). An EMPTY name is silence: it
    means the caller did not ask for one. A non-empty name that does not resolve is the typo this
    function exists to report.
    """
    import bpy

    if not name:
        return None
    found = getattr(bpy.data, kind).get(name)
    if found is None:
        label = what or kind.rstrip("s")
        detail = (f"{label} {name!r} does not exist, so this build cannot use it"
                  f" (have: {', '.join(sorted(m.name for m in getattr(bpy.data, kind))[:8]) or 'none'})")
        if required:
            raise ValueError(detail)
        warn(detail)
    return found


# Import recipe modules so their @recipe decorators run.
from . import (  # noqa: E402,F401
    curve_overlay,
    curve_water,
    heightmap_terrain,
    particulates,
    scatter,
    scatter_along,
    snow,
    snow_shell,
    terrain,
    volumetrics,
    wave_grid,
)
