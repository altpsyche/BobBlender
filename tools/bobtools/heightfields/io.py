"""Heightfield IO: 16-bit PNG plus a params sidecar.

The sidecar (`<name>.json`) written beside each PNG is the full recipe that
produced the field, so any heightfield is reproducible and the cache can tell
whether a re-bake is needed. Absolute paths only; the caller resolves them.
"""

import json
import os

import numpy as np
from PIL import Image


def to_png16(h, path):
    """Write a normalised [0, 1] field as a 16-bit grayscale PNG."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    data = np.clip(h, 0.0, 1.0)
    img = (data * 65535.0 + 0.5).astype(np.uint16)
    # Pillow infers 16-bit grayscale (I;16) from a uint16 array; the explicit
    # mode arg is deprecated.
    Image.fromarray(img).save(path)


def read_png16(path):
    """Read a 16-bit grayscale PNG back to a [0, 1] float array."""
    img = np.asarray(Image.open(path))
    return img.astype(np.float64) / 65535.0


def sidecar_path(png_path):
    return os.path.splitext(png_path)[0] + ".json"


def write_sidecar(png_path, meta):
    """Write the recipe/stats sidecar next to the PNG."""
    with open(sidecar_path(png_path), "w") as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)


def read_sidecar(png_path):
    """Return the sidecar dict, or None if the PNG or sidecar is missing."""
    side = sidecar_path(png_path)
    if not (os.path.exists(png_path) and os.path.exists(side)):
        return None
    try:
        with open(side) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None
