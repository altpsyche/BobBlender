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


# Import recipe modules so their @recipe decorators run.
from . import heightmap_terrain, scatter, terrain, wave_grid  # noqa: E402,F401
