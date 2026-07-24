"""Heightfield IO: 16-bit grayscale PNG plus a params sidecar.

The PNG codec is pure numpy + zlib (no PIL, no bpy): the compute now runs in-process
inside Blender, whose bundled Python ships no PIL, so the package cannot depend on it.
PNG is lossless, so the round-trip is bit-exact regardless of codec; the writer uses
filter 0 on every row and the reader fast-paths that (vectorised), falling back to full
per-row unfiltering for any foreign PNG.

The sidecar (`<name>.json`) written beside each PNG is the full recipe that produced the
field, so any heightfield is reproducible and the cache can tell whether a re-bake is
needed. Absolute paths only; the caller resolves them.
"""

import json
import os
import struct
import zlib

import numpy as np

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def to_png16(h, path):
    """Write a normalised [0, 1] field as a 16-bit grayscale PNG (color type 0, bit depth 16)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    data = np.clip(np.asarray(h, dtype=np.float64), 0.0, 1.0)
    if data.ndim != 2:
        raise ValueError(f"to_png16 expects a 2D grayscale field, got shape {data.shape}")
    img = (data * 65535.0 + 0.5).astype(np.uint16)
    rows, cols = img.shape
    # Big-endian uint16 samples (PNG is network byte order), one filter byte (0 = None) per row.
    be = np.ascontiguousarray(img.astype(">u2")).view(np.uint8).reshape(rows, cols * 2)
    raw = np.concatenate([np.zeros((rows, 1), dtype=np.uint8), be], axis=1)
    ihdr = struct.pack(">IIBBBBB", cols, rows, 16, 0, 0, 0, 0)
    with open(path, "wb") as fh:
        fh.write(_PNG_SIG)
        fh.write(_chunk(b"IHDR", ihdr))
        fh.write(_chunk(b"IDAT", zlib.compress(raw.tobytes(), 6)))
        fh.write(_chunk(b"IEND", b""))


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter_general(dec, rows, stride, bpp):
    """Reconstruct scanlines under any of the five PNG filter types (byte-wise). Only reached for a
    foreign PNG whose writer used a filter other than None; our own writer never triggers it."""
    out = np.zeros((rows, stride), dtype=np.uint8)
    i = 0
    prev = [0] * stride
    for y in range(rows):
        ftype = dec[i]; i += 1
        line = dec[i:i + stride]; i += stride
        recon = [0] * stride
        for x in range(stride):
            a = recon[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            v = line[x]
            if ftype == 0:
                r = v
            elif ftype == 1:
                r = v + a
            elif ftype == 2:
                r = v + b
            elif ftype == 3:
                r = v + ((a + b) >> 1)
            elif ftype == 4:
                r = v + _paeth(a, b, c)
            else:
                raise ValueError(f"unknown PNG filter type {ftype}")
            recon[x] = r & 0xFF
        out[y] = recon
        prev = recon
    return out


def read_png16(path):
    """Read a 16-bit grayscale PNG back to a [0, 1] float64 array."""
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:8] != _PNG_SIG:
        raise ValueError(f"not a PNG: {path}")
    pos = 8
    cols = rows = bitdepth = colortype = None
    idat = bytearray()
    while pos + 8 <= len(raw):
        length = struct.unpack(">I", raw[pos:pos + 4])[0]
        tag = raw[pos + 4:pos + 8]
        data = raw[pos + 8:pos + 8 + length]
        pos += 12 + length  # length + tag + data + CRC
        if tag == b"IHDR":
            cols, rows, bitdepth, colortype = struct.unpack(">IIBB", data[:10])
        elif tag == b"IDAT":
            idat += data
        elif tag == b"IEND":
            break
    if bitdepth != 16 or colortype != 0:
        raise ValueError(f"expected 16-bit grayscale PNG, got bitdepth={bitdepth} colortype={colortype}")
    dec = zlib.decompress(bytes(idat))
    bpp = 2  # 1 channel * 16-bit
    stride = cols * bpp
    grid = np.frombuffer(dec, dtype=np.uint8).reshape(rows, stride + 1)
    if np.all(grid[:, 0] == 0):  # fast path: every row filter 0 (our writer)
        body = grid[:, 1:]
    else:
        body = _unfilter_general(dec, rows, stride, bpp)
    samples = body.reshape(rows, cols, bpp)
    vals = (samples[..., 0].astype(np.uint32) << 8) | samples[..., 1].astype(np.uint32)
    return vals.astype(np.float64) / 65535.0


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
