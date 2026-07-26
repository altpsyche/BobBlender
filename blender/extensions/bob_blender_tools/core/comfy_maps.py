"""Albedo to texture-set maps, in numpy, plus the PNG IO the round trip needs.

Map derivation is Bob's, not the graph's (docs/COMFYUI.md, track A): deterministic, tunable, no
submodule, and it reuses the numpy that `core/heightfields` already depends on. This module is
bpy-free and imports nothing but the stdlib and numpy, so the venv, the headless scripts, and a
future MCP tool all drive the same code.

G2 derives five maps from one albedo, all from a single shared relief field so they agree with
each other instead of each re-deriving the surface:

    basecolor  the generation, untouched
    height     a wrap-safe high-pass of the luminance (the relief field, recentred on 0.5)
    normal     the relief field's gradient, OpenGL convention, unit length
    ao         multi-scale occlusion of the relief field; the sampler folds this into the albedo
    roughness  local-contrast, percentile-stretched, NOT the G1 global band

`cavity_from()` is a signal rather than a written map: the roughness consumes it in memory and no
master reads a cavity file, so writing one would be work nothing loads. Metallic is skipped (no
shipped set has one and
nature surfaces are dielectric). The normal map IS written even though neither master carries a
normal socket today (S3 drives relief from a bump instead, see core/materials/texset.py): it is
part of the texture-set contract and track B needs it, and an unread file costs 0.1 s.

G5 added one derivation that is not part of a texture set: `macro_field` / `macro_from`, the terrain
macro mask (track E). It reuses the luminance and the box blur the five maps already share and takes
the LOW side of the same cutoff, which is why track E needed no module of its own.

The PNG codec here is minimal on purpose: 8-bit, non-interlaced, which is what ComfyUI's SaveImage
writes and what a texture set wants. Blender's bundled Python ships no PIL and the derivation must
run in-process, so the 40 lines are cheaper than the alternative of routing pixels through `bpy`
and losing the bpy-free property.
"""

import struct
import zlib

import numpy as np

# Rec.709 luma. The albedo is sRGB-encoded and stays that way: these are crude perceptual
# derivations, not a linear-light computation, and G2 is where that distinction starts to matter.
_LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

# Roughness band. A raw 1-minus-luminance puts a bright sand at 0.1 roughness, which reads as wet
# plastic, so the inverse is remapped into a plausible ground range instead of used directly.
ROUGHNESS_RANGE = (0.35, 0.95)

# How much of the roughness signal is the GLOBAL inverse luminance and how much is the LOCAL
# deviation from it. G1 was 1.0 global, which is why a bright albedo parked the whole map at the
# top of the band (measured 117-242, mean 206). Mostly-local means a pale stone and a dark stone
# both get the full band, and the variation follows the surface rather than the paint.
ROUGHNESS_GLOBAL = 0.35
ROUGHNESS_LOCAL_FRACTION = 1.0 / 8.0

# Height is a high-pass of the luminance: the low frequency is albedo variation, not relief.
# Radius is a fraction of the image so the split follows resolution rather than a pixel count.
HEIGHT_LOWPASS_FRACTION = 1.0 / 32.0

# Cavity is the same idea at a much smaller radius: the crevice, not the boulder.
CAVITY_FRACTION = 1.0 / 128.0

# The macro mask (track E) is the SAME split read from the other side: keep the low frequency and
# throw the detail away. A twelfth of the image is the coarsest cutoff that still resolves a
# separate massif and a separate basin in one frame; anything finer starts handing the erosion stack
# structure it would rather generate itself (R7).
MACRO_LOWPASS_FRACTION = 1.0 / 12.0

# Percentiles the macro mask is stretched between. Not 0 and 100: one blown-out highlight in the
# generation would otherwise compress the whole landform into the bottom of the range.
MACRO_STRETCH = (1.0, 99.0)

# How hard a crevice is pushed toward rough, before the percentile stretch.
CAVITY_ROUGHNESS = 0.5

# AO radii, coarse to fine. One radius gives an outline; three give a falloff, which is what
# occlusion actually looks like. Fractions of the image, for the same resolution-independence.
AO_FRACTIONS = (1.0 / 6.0, 1.0 / 16.0, 1.0 / 48.0)

# How dark full occlusion goes. Deliberately shy of black: the terrain sampler multiplies this
# straight into the albedo (core/materials/texset.py), so an over-confident AO darkens a material
# in a way that is worse than no AO, which is exactly why G1 shipped none.
AO_STRENGTH = 0.6

# Normal-map slope gain. The relief field is already normalised to -0.5..0.5, so this is a look
# knob rather than a unit conversion.
NORMAL_STRENGTH = 6.0


# -- PNG -------------------------------------------------------------------------------------
def read_png(data):
    """Decode 8-bit non-interlaced PNG bytes to a uint8 array, (h, w) or (h, w, c).

    None and Up defilter vectorised. Sub, Average and Paeth each need the reconstructed LEFT
    neighbour, so they are sequential in x and in y and no numpy shape saves them; the loop runs
    on `bytes` and a `bytearray` rather than on numpy scalars, which is 2.5x faster than indexing
    an array element by element (1024 square Paeth: 0.64 s against 1.59 s). This is not academic:
    ComfyUI saves through PIL, which picks Paeth for essentially every row.
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    idat, header, pos = [], None, 8
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if header is None:
        raise ValueError("PNG has no IHDR")
    width, height, depth, colour, _, _, interlace = header
    if depth != 8 or interlace:
        raise ValueError(f"unsupported PNG: depth {depth}, interlace {interlace} "
                         "(8-bit non-interlaced only)")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(colour)
    if channels is None or colour == 3:
        raise ValueError(f"unsupported PNG colour type {colour}")

    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    ch = channels
    out = bytearray(height * stride)
    prev = bytes(stride)
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        line = raw[pos + 1:pos + 1 + stride]
        pos += 1 + stride
        if ftype == 0:
            rec = bytearray(line)
        elif ftype == 2:  # Up: the previous row alone, so numpy does the whole scanline
            rec = bytearray(((np.frombuffer(line, np.uint8).astype(np.uint16)
                              + np.frombuffer(prev, np.uint8)) & 0xFF).astype(np.uint8).tobytes())
        elif ftype in (1, 3, 4):
            rec = bytearray(stride)
            for i in range(stride):
                left = rec[i - ch] if i >= ch else 0
                if ftype == 1:
                    pred = left
                else:
                    above = prev[i]
                    if ftype == 3:
                        pred = (left + above) >> 1
                    else:
                        upleft = prev[i - ch] if i >= ch else 0
                        p = left + above - upleft
                        pa = p - left if p >= left else left - p
                        pb = p - above if p >= above else above - p
                        pc = p - upleft if p >= upleft else upleft - p
                        pred = left if (pa <= pb and pa <= pc) else (above if pb <= pc else upleft)
                rec[i] = (line[i] + pred) & 0xFF
        else:
            raise ValueError(f"unknown PNG filter {ftype} on row {y}")
        out[y * stride:(y + 1) * stride] = rec
        prev = bytes(rec)
    img = np.frombuffer(bytes(out), dtype=np.uint8).reshape(height, width, channels)
    return img[:, :, 0] if channels == 1 else img


def write_png(path, array, level=6):
    """Write a uint8 (h, w) grey or (h, w, 3) RGB array as an 8-bit PNG, filter 0."""
    array = np.ascontiguousarray(array, dtype=np.uint8)
    if array.ndim == 2:
        colour, channels = 0, 1
    elif array.ndim == 3 and array.shape[2] == 3:
        colour, channels = 2, 3
    else:
        raise ValueError(f"write_png wants (h, w) or (h, w, 3), got {array.shape}")
    height, width = array.shape[:2]
    rows = np.concatenate(
        [np.zeros((height, 1), dtype=np.uint8), array.reshape(height, width * channels)], axis=1)

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, colour, 0, 0, 0)))
        fh.write(chunk(b"IDAT", zlib.compress(rows.tobytes(), level)))
        fh.write(chunk(b"IEND", b""))
    return path


# -- Derivation ------------------------------------------------------------------------------
def _box_blur(a, radius, wrap=True):
    """A separable moving-average blur. Cumulative sums rather than a convolution: O(n) at any
    radius.

    `wrap=True` wraps at both edges, so blurring a seamless tile leaves it seamless, and it is the
    default because every track A map is a tile. `wrap=False` replicates the edge instead, for the
    one signal that is NOT a tile: a terrain macro mask (track E), where wrapping would bleed the
    far side of the landform into this one and put a phantom massif on the opposite border.
    """
    if radius < 1:
        return a
    n = 2 * radius + 1
    for axis in (1, 0):
        a = np.moveaxis(a, axis, 1)
        if wrap:
            pad = np.concatenate([a[:, -radius:], a, a[:, :radius]], axis=1)
        else:
            pad = np.concatenate([a[:, :1].repeat(radius, axis=1), a,
                                  a[:, -1:].repeat(radius, axis=1)], axis=1)
        cum = np.concatenate([np.zeros((pad.shape[0], 1), np.float32),
                              np.cumsum(pad, axis=1, dtype=np.float32)], axis=1)
        a = np.moveaxis((cum[:, n:] - cum[:, :-n]) / n, 1, axis)
    return a


def _to_u8(a):
    return np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8)


def luminance(rgb):
    """Rec.709 luma of a uint8 RGB array, as float32 in 0..1."""
    return (rgb[:, :, :3].astype(np.float32) / 255.0 @ _LUMA)


def _radius(shape, fraction):
    return max(1, int(round(min(shape[:2]) * fraction)))


def _normalise(signed, percentile=99.0):
    """A signed field scaled so its 99th absolute percentile lands at 0.5, clipped to -0.5..0.5.

    Floored at one 8-bit step: on a flat or very smooth albedo the field is float rounding noise,
    and normalising by its own percentile would amplify that noise to full range. With the floor,
    no real relief means zero, which is the honest answer. (Found by a G1 unit test, not by eye.)
    """
    scale = max(float(np.percentile(np.abs(signed), percentile)), 1.0 / 255.0)
    return np.clip(signed / (2.0 * scale), -0.5, 0.5)


def relief(rgb, fraction=HEIGHT_LOWPASS_FRACTION):
    """The shared relief field: float32 in -0.5..0.5, negative into the surface.

    A high-pass of the luminance, NOT a Sobel gradient magnitude (which the plan suggested):
    gradient magnitude is an edge map, so it raises a ridge on BOTH sides of every crevice and the
    bump comes out embossed rather than carved. Subtracting a wrap-around low-pass keeps the sign,
    so dark detail sinks and bright detail rises, and dropping the low frequency stops overall
    albedo brightness from tilting the whole ground.

    Height, normal, cavity and AO all read THIS, rather than each re-deriving a surface from the
    albedo, so the four maps describe one surface instead of four similar ones.
    """
    lum = luminance(rgb)
    return _normalise(lum - _box_blur(lum, _radius(lum.shape, fraction)))


def height_from(rgb, fraction=HEIGHT_LOWPASS_FRACTION):
    """The relief field as an 8-bit map centred on 0.5."""
    return _to_u8(0.5 + relief(rgb, fraction))


def macro_field(rgb, fraction=MACRO_LOWPASS_FRACTION, wrap=False, percentiles=MACRO_STRETCH):
    """A generated image as a low-frequency macro MASK for the terrain op stack: float32 in 0..1.

    This is `relief()`'s other half and not a new idea: relief keeps `lum - lowpass(lum)` because a
    texture's information is its detail, and this keeps `lowpass(lum)` because a landform's
    information is its large scale. Same luminance, same box blur, the opposite side of one cutoff,
    which is the honest answer to whether track E needed its own derivation module. It did not.

    Two differences from the track A maps, and both are because a terrain tile is not a texture
    tile. The blur does NOT wrap (see `_box_blur`), and the result is percentile-stretched to fill
    0..1 rather than centred on 0.5, because the op stack reads it as an elevation ordering where
    0 is the basin floor and 1 is the highest ground, not as a signed displacement.

    It is a MASK, not a heightfield (R7): every real slope, drainage line and rill comes from the
    erosion stack afterwards. `MACRO_LOWPASS_FRACTION` is the whole claim, in one number -- a
    twelfth of the image, so nothing finer than a massif survives to compete with the erosion.
    """
    lum = luminance(rgb)
    low = _box_blur(lum, _radius(lum.shape, fraction), wrap=wrap)
    lo, hi = (float(v) for v in np.percentile(low, percentiles))
    spread = hi - lo
    if spread < 1.0 / 255.0:
        # A flat generation carries no landform. Half everywhere is the honest answer: the stack's
        # own generator then owns the whole shape, which is what a terrain with no mask does.
        return np.full(low.shape, 0.5, dtype=np.float32)
    return np.clip((low - lo) / spread, 0.0, 1.0).astype(np.float32)


def macro_from(rgb, fraction=MACRO_LOWPASS_FRACTION, wrap=False):
    """`macro_field` as the 8-bit PNG the terrain op stack reads. 8 bits on purpose, per R7: 256
    levels of a mask that is about to be blurred and eroded is not the same claim as 256 levels of
    a heightfield, and G5 measures the difference rather than asserting it."""
    return _to_u8(macro_field(rgb, fraction, wrap))


def cavity_from(height, fraction=CAVITY_FRACTION):
    """Small-scale concavity of a relief field: 0.5 flat, below it a crevice, above it a ridge.

    Not written as a map file, because no master reads one. It is what separates "this pixel is
    dark" from "this pixel is in a hole", which is why the roughness takes it as an input: a
    crevice holds dust and damp and is rougher than the face beside it at the same brightness.
    """
    return 0.5 + _normalise(height - _box_blur(height, _radius(height.shape, fraction)))


def ao_from(height, fractions=AO_FRACTIONS, strength=AO_STRENGTH):
    """Multi-scale ambient occlusion of a relief field, as an 8-bit map (255 = unoccluded).

    At each radius the neighbourhood mean minus the pixel is how far below its surroundings the
    pixel sits, which is the cheap stand-in for a hemisphere raycast that every texture tool uses.
    Three radii rather than one, because a single radius gives an outline and occlusion is a
    falloff. Only the positive side counts: a ridge is not anti-occluded.
    """
    occ = np.zeros(height.shape[:2], dtype=np.float32)
    for fraction in fractions:
        below = _box_blur(height, _radius(height.shape, fraction)) - height
        scale = max(float(np.percentile(below, 99)), 1.0 / 255.0)
        occ += np.clip(below / scale, 0.0, 1.0)
    return _to_u8(1.0 - strength * (occ / len(fractions)))


def normal_from(height, strength=NORMAL_STRENGTH):
    """A tangent-space normal map from a relief field: uint8 RGB, OpenGL convention (+Y up).

    Central differences taken with `np.roll`, so the gradient wraps and a seamless height gives a
    seamless normal. Row index grows downward while the green channel points up, hence the sign
    difference between the two channels.
    """
    dcol = 0.5 * (np.roll(height, -1, axis=1) - np.roll(height, 1, axis=1))
    drow = 0.5 * (np.roll(height, -1, axis=0) - np.roll(height, 1, axis=0))
    vec = np.stack([-dcol * strength, drow * strength,
                    np.ones(height.shape[:2], dtype=np.float32)], axis=2)
    vec /= np.linalg.norm(vec, axis=2, keepdims=True)
    return _to_u8(0.5 * vec + 0.5)


def roughness_from(rgb, band=ROUGHNESS_RANGE, fraction=ROUGHNESS_LOCAL_FRACTION,
                   global_weight=ROUGHNESS_GLOBAL, cavity=None):
    """Roughness from local contrast, percentile-stretched into the band.

    G1's version was a global remap of the inverse luminance, and it had one measurable defect:
    on a bright albedo every pixel lands near the top of the band, so the map reads "rough
    everywhere, very slightly less so on the bright bits" (measured 117-242 of 255, mean 206).

    Three changes. Most of the signal is now the deviation from the LOCAL mean, so the map
    responds to the surface rather than to how pale the paint is; a crevice (`cavity`, when the
    caller has a relief field to derive one from) is pushed rougher, because it holds dust and
    damp regardless of its brightness; and the result is stretched by its own 2nd and 98th
    percentiles, so the band is actually occupied instead of being a range the values happen to
    sit in one corner of. A little global inverse luminance is kept, because a genuinely dark damp
    patch really is rougher than the dry stone beside it.
    """
    inv = 1.0 - luminance(rgb)
    local = _box_blur(inv, _radius(inv.shape, fraction))
    signal = global_weight * inv + (1.0 - global_weight) * (inv - local + 0.5)
    if cavity is not None:
        signal = signal + CAVITY_ROUGHNESS * (0.5 - np.asarray(cavity, dtype=np.float32))
    lo_p, hi_p = (float(v) for v in np.percentile(signal, (2.0, 98.0)))
    spread = hi_p - lo_p
    # A flat albedo has no roughness information, so the honest answer is the middle of the band,
    # not a stretch of rounding noise across all of it.
    unit = (np.clip((signal - lo_p) / spread, 0.0, 1.0) if spread >= 1.0 / 255.0
            else np.full(signal.shape, 0.5, dtype=np.float32))
    lo, hi = band
    return _to_u8(lo + unit * (hi - lo))


def derive(albedo_png):
    """{role: array} for the maps Bob writes, from raw PNG bytes or a uint8 RGB array.

    One relief field feeds height, normal and AO; see the module docstring for why cavity is a
    signal here rather than a sixth file.
    """
    rgb = read_png(albedo_png) if isinstance(albedo_png, (bytes, bytearray)) else albedo_png
    rgb = np.asarray(rgb)
    if rgb.ndim == 2:
        rgb = np.repeat(rgb[:, :, None], 3, axis=2)
    rgb = np.ascontiguousarray(rgb[:, :, :3], dtype=np.uint8)
    height = relief(rgb)
    return {"basecolor": rgb,
            "roughness": roughness_from(rgb, cavity=cavity_from(height)),
            "height": _to_u8(0.5 + height),
            "normal": normal_from(height),
            "ao": ao_from(height)}


# -- Seam measurement ------------------------------------------------------------------------
def tile3x3(array):
    """The array tiled three by three, which is how a seam is looked at rather than asserted."""
    return np.tile(array, (3, 3) + (1,) * (array.ndim - 2))


def wrap_pad(array, pad):
    """The array with `pad` pixels of its opposite edge wrapped around all four sides.

    The fix for any operation that pads at the image border and so cannot know the image is a
    torus. Measured need: UltimateSDUpscale reintroduced a seam at ratio 3.43 on an input that
    measured 0.94, because its ESRGAN pass and its per-tile crops both pad at the border and a
    circular-padded UNet only ever sees a tile. Wrap-padding first, and cropping after, makes the
    border a real neighbourhood instead.
    """
    if pad < 1:
        return array
    width = [(pad, pad), (pad, pad)] + [(0, 0)] * (array.ndim - 2)
    return np.pad(array, width, mode="wrap")


def crop_pad(array, pad):
    """The inverse of `wrap_pad`: drop `pad` pixels from every side."""
    return array if pad < 1 else array[pad:-pad, pad:-pad]


def _blend_axis(array, pad):
    """Crop `pad` from both ends of axis 0 and cross-fade the duplicated band, so the result is
    periodic along that axis. Assumes the input came from `wrap_pad`."""
    core = array[pad:array.shape[0] - pad].astype(np.float32)
    # The last `pad` rows of the padded array are a second, independently processed render of the
    # core's FIRST `pad` rows. Fading from that copy into this one puts the continuous join on the
    # wrap line and pushes the discontinuity into the middle of the band, where the ramp absorbs
    # it -- the offset-blend trick, but between two renders of the same content rather than
    # between unrelated pixels, which is why it costs far less contrast than the G1 measurement of
    # the WAS blend did.
    alt = array[array.shape[0] - pad:].astype(np.float32)
    ramp = np.linspace(0.0, 1.0, pad, endpoint=False, dtype=np.float32)
    ramp = ramp.reshape((pad,) + (1,) * (core.ndim - 1))
    core[:pad] = (1.0 - ramp) * alt + ramp * core[:pad]
    return core


def crop_wrap_blend(array, pad):
    """Crop a wrap-padded image back to its core, made periodic again.

    `wrap_pad` alone is not enough after a non-periodic operation. The padded image's two copies
    of each edge band are processed independently and drift apart, so a plain crop just moves the
    seam rather than removing it (measured on W3: pad-and-crop took the upscale from ratio 3.43 to
    2.08, and no crop position does better, because a non-periodic image has no periodic window).
    Cross-fading the duplicate bands does remove it. X first on the full-height array, then Y, so
    the rows the Y pass fades are already periodic in X and the corners come out right.
    """
    if pad < 1:
        return np.asarray(array)
    a = np.asarray(array)
    a = np.swapaxes(_blend_axis(np.swapaxes(a, 0, 1), pad), 0, 1)  # X
    return np.clip(_blend_axis(a, pad) + 0.5, 0, 255).astype(np.uint8)  # Y


def seam_report(array):
    """Mean absolute difference across the wrap seam versus across an interior line, in 0..255.

    A genuinely tileable image has `seam` about equal to `interior`, because the wrap is just
    another pair of adjacent columns. A post-hoc offset blend leaves `seam` LOWER than interior
    (it blurred that line), and an untreated image leaves it much higher. `ratio` is seam over
    interior, so 1.0 is the target and the direction of the miss says which failure it is.
    """
    a = np.asarray(array, dtype=np.float32)
    if a.ndim == 3:
        a = a[:, :, :3].mean(axis=2)
    h, w = a.shape
    seam_x = float(np.abs(a[:, w - 1] - a[:, 0]).mean())
    seam_y = float(np.abs(a[h - 1, :] - a[0, :]).mean())
    # Every interior column pair, not one arbitrary line: a single line can be flat by luck and
    # would make any seam look bad.
    interior_x = float(np.abs(np.diff(a, axis=1)).mean())
    interior_y = float(np.abs(np.diff(a, axis=0)).mean())
    seam = (seam_x + seam_y) / 2.0
    interior = (interior_x + interior_y) / 2.0
    return {"seam_x": seam_x, "seam_y": seam_y, "seam": seam,
            "interior_x": interior_x, "interior_y": interior_y, "interior": interior,
            "ratio": (seam / interior) if interior else float("inf")}
