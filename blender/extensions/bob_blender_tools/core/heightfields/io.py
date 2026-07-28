"""Heightfield IO: 16-bit grayscale PNG plus a params sidecar.

The PNG codec is pure numpy + zlib (no PIL, no bpy): the compute now runs in-process
inside Blender, whose bundled Python ships no PIL, so the package cannot depend on it.
PNG is lossless, so the round-trip is bit-exact regardless of codec; the writer uses
filter 0 on every row and the reader fast-paths that (vectorised), falling back to full
per-row unfiltering for any foreign PNG.

Reading comes in two strengths. `read_png` takes any non-interlaced 8 or 16-bit grey or RGB(A)
file, which is what a foreign input is (a diffusion-generated macro mask, a hand-painted mask);
`read_png16` is the strict entry for Bob's own bake, and it stays strict because an 8-bit file
accepted as a terrain base would terrace it into 256 benches without a word.

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


_CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}   # grey, RGB, grey+alpha, RGBA. Palette (3) is unsupported.


def read_png(path):
    """Read a non-interlaced PNG to a [0, 1] float64 2D array, averaging colour channels.

    8 or 16 bits per sample, grey or RGB(A). Both depths, because the two things a heightfield reads
    are not the same file: a baked terrain is Bob's own 16-bit grey (`to_png16`), and a macro mask
    (docs/GENERATION.md, the macro heightmap) is whatever a diffusion model saved, which is 8-bit
    RGB. Alpha is dropped rather than composited: a mask has no background to composite over.
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:8] != _PNG_SIG:
        raise ValueError(f"not a PNG: {path}")
    pos = 8
    cols = rows = bitdepth = colortype = interlace = None
    idat = bytearray()
    while pos + 8 <= len(raw):
        length = struct.unpack(">I", raw[pos:pos + 4])[0]
        tag = raw[pos + 4:pos + 8]
        data = raw[pos + 8:pos + 8 + length]
        pos += 12 + length  # length + tag + data + CRC
        if tag == b"IHDR":
            cols, rows, bitdepth, colortype, _, _, interlace = struct.unpack(">IIBBBBB", data[:13])
        elif tag == b"IDAT":
            idat += data
        elif tag == b"IEND":
            break
    channels = _CHANNELS.get(colortype)
    if bitdepth not in (8, 16) or channels is None or interlace:
        raise ValueError(f"unsupported PNG {path}: bitdepth={bitdepth} colortype={colortype} "
                         f"interlace={interlace} (8/16-bit grey or RGB(A), non-interlaced)")
    dec = zlib.decompress(bytes(idat))
    bpp = channels * (bitdepth // 8)
    stride = cols * bpp
    grid = np.frombuffer(dec, dtype=np.uint8).reshape(rows, stride + 1)
    if np.all(grid[:, 0] == 0):  # fast path: every row filter 0 (our writer)
        body = grid[:, 1:]
    else:
        body = _unfilter_general(dec, rows, stride, bpp)
    if bitdepth == 16:
        samples = body.reshape(rows, cols, channels, 2)
        vals = (((samples[..., 0].astype(np.uint32) << 8) | samples[..., 1])
                .astype(np.float64) / 65535.0)
    else:
        vals = body.reshape(rows, cols, channels).astype(np.float64) / 255.0
    colour = channels - (1 if colortype in (4, 6) else 0)   # drop the alpha channel
    # Take the one channel rather than averaging it: the common case here is a 4096-square terrain
    # bake, and a mean over a single channel is a second full-size array for no result.
    return vals[..., 0] if colour == 1 else vals[..., :colour].mean(axis=2)


def read_png16(path):
    """`read_png` restricted to Bob's own 16-bit grayscale write, which is what a baked heightfield
    is. Strict on purpose: silently accepting an 8-bit file as a terrain base would terrace it into
    256 benches and nothing downstream would report a problem."""
    with open(path, "rb") as fh:
        head = fh.read(26)
    bitdepth, colortype = (head[24], head[25]) if head[:8] == _PNG_SIG else (None, None)
    if bitdepth != 16 or colortype != 0:
        raise ValueError(f"expected 16-bit grayscale PNG, got bitdepth={bitdepth} colortype={colortype}")
    return read_png(path)


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
