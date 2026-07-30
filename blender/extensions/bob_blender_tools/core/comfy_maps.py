"""Albedo to texture-set maps, in numpy, plus the PNG IO the round trip needs.

Map derivation is Bob's, not the graph's (docs/GENERATION.md, the texture family): deterministic,
tunable, no submodule, and it reuses the numpy that `core/heightfields` already depends on. This
module is bpy-free and imports nothing but the stdlib and numpy, so the venv, the headless scripts,
and a future MCP tool all drive the same code.

The variants gate derives five maps from one albedo, all from a single shared relief field so they
agree with each other instead of each re-deriving the surface:

    basecolor  the generation, untouched height     a wrap-safe high-pass of the luminance (the
    relief field, recentred on 0.5) normal     the relief field's gradient, OpenGL convention, unit
    length ao         multi-scale occlusion of the relief field; the sampler folds this into the
    albedo roughness  local-contrast, percentile-stretched, NOT the first spike global band

`cavity_from()` is a signal rather than a written map: the roughness consumes it in memory and no
master reads a cavity file, so writing one would be work nothing loads. Metallic is skipped (no
shipped set has one and nature surfaces are dielectric). The normal map IS written even though
neither master carries a normal socket today (BobFirmament drives relief from a bump instead, see
core/materials/texset.py): it is part of the texture-set contract and the mesh-texturing family
needs it, and an unread file costs 0.1 s.

The macro-mask gate added one derivation that is not part of a texture set: `macro_field` /
`macro_from`, the terrain macro mask (the macro-heightmap family). It reuses the luminance and the
box blur the five maps already share and takes the LOW side of the same cutoff, which is why the
macro-heightmap family needed no module of its own.

BobFoliage added two more that are not derivations at all. `grain_report` MEASURES the dominant
gradient axis, because bark needs a direction and `seam_report` only measures continuity. And the
`orient_sprite` / `place_sprite` / `atlas_compose` / `atlas_cells` group composes a leaf atlas out
of one generated sprite per cell, because a diffusion model asked for a 2x2 grid returns five sprays
in a ring -- measured. Both are here rather than in `comfy.py` for this module's usual reason: they
are numpy over pixels with no HTTP in them, so a unit test drives them with no server.

The PNG codec here is minimal on purpose: 8-bit, non-interlaced, which is what ComfyUI's SaveImage
writes and what a texture set wants. Blender's bundled Python ships no PIL and the derivation must
run in-process, so the 40 lines are cheaper than the alternative of routing pixels through `bpy` and
losing the bpy-free property.
"""

import struct
import zlib

import numpy as np

try:
    from . import gen_bars
except ImportError:  # `core` itself on sys.path: the MCP venv and the headless routes, where there
    import gen_bars  # is no parent package -- the same fallback `gen_receipt` uses


def _bar(name):
    """One bar's value, off the registry (`core/gen_bars.py`)."""
    return gen_bars.value(name)

# Rec.709 luma. The albedo is sRGB-encoded and stays that way: these are crude perceptual
# derivations, not a linear-light computation, and the variants gate is where that distinction
# starts to matter.
_LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

# Roughness band. A raw 1-minus-luminance puts a bright sand at 0.1 roughness, which reads as wet
# plastic, so the inverse is remapped into a plausible ground range instead of used directly.
ROUGHNESS_RANGE = (0.35, 0.95)

# How much of the roughness signal is the GLOBAL inverse luminance and how much is the LOCAL
# deviation from it. The first spike was 1.0 global, which is why a bright albedo parked the whole
# map at the top of the band (measured 117-242, mean 206). Mostly-local means a pale stone and a
# dark stone both get the full band, and the variation follows the surface rather than the paint.
ROUGHNESS_GLOBAL = 0.35
ROUGHNESS_LOCAL_FRACTION = 1.0 / 8.0

# Height is a high-pass of the luminance: the low frequency is albedo variation, not relief.
# Radius is a fraction of the image so the split follows resolution rather than a pixel count.
HEIGHT_LOWPASS_FRACTION = 1.0 / 32.0

# Cavity is the same idea at a much smaller radius: the crevice, not the boulder.
CAVITY_FRACTION = 1.0 / 128.0

# The macro mask (the macro-heightmap family) is the SAME split read from the other side: keep the
# low frequency and throw the detail away. A twelfth of the image is the coarsest cutoff that still
# resolves a separate massif and a separate basin in one frame; anything finer starts handing the
# erosion stack structure it would rather generate itself (the bit-depth floor).
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
# in a way that is worse than no AO, which is exactly why the first spike shipped none.
AO_STRENGTH = 0.6

# Normal-map slope gain. The relief field is already normalised to -0.5..0.5, so this is a look
# knob rather than a unit conversion.
NORMAL_STRENGTH = 6.0

# Delighting, and the flatness measure that decides whether a set wants it (`delight`,
# `flatness_report`).
#
# An eighth of the image for the blur, which is deliberately coarser than
# `HEIGHT_LOWPASS_FRACTION`'s thirty-second. Those two answer different questions: the relief split
# is "what is detail", and this one is "what is the LIGHT", which lives at a much larger scale than
# any surface feature. At a thirty-second the correction starts flattening real surface variation;
# at an eighth a sky gradient and a shadow across the frame go and the mossy patches stay.
DELIGHT_FRACTION = 1.0 / 8.0

# How much of the division to apply, when a caller asks for delighting at all. Not 1.0, because a
# full divide also removes genuine large-scale albedo variation: a patch of moss really is a
# different colour from the earth beside it, and a texture that has been flattened to one tone reads
# as vinyl. 0.75 removes most of a 1.2-stop ramp and leaves the surface reading as a surface.
DELIGHT_STRENGTH = 0.75

# The floor the blurred luminance is divided by. A texel in deep shadow would otherwise be
# multiplied up by a huge factor and come back as amplified sensor noise, which is the one way this
# correction looks worse than the defect it fixes. 0.08 of full range is about two and a half stops
# below mid grey, which is below every low-frequency value the shipped sets actually contain.
DELIGHT_FLOOR = 0.08


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
    default because every the texture family map is a tile. `wrap=False` replicates the edge
    instead, for the one signal that is NOT a tile: a terrain macro mask (the macro-heightmap
    family), where wrapping would bleed the far side of the landform into this one and put a phantom
    massif on the opposite border.
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
    and normalising by its own percentile would amplify that noise to full range. With the floor, no
    real relief means zero, which is the honest answer. (Found by a the first spike unit test, not
    by eye.)
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
    which is the honest answer to whether the macro-heightmap family needed its own derivation
    module. It did not.

    Two differences from the texture family maps, and both are because a terrain tile is not a
    texture tile. The blur does NOT wrap (see `_box_blur`), and the result is percentile-stretched
    to fill 0..1 rather than centred on 0.5, because the op stack reads it as an elevation ordering
    where 0 is the basin floor and 1 is the highest ground, not as a signed displacement.

    It is a MASK, not a heightfield (the bit-depth floor): every real slope, drainage line and rill
    comes from the erosion stack afterwards. `MACRO_LOWPASS_FRACTION` is the whole claim, in one
    number -- a twelfth of the image, so nothing finer than a massif survives to compete with the
    erosion.
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
    """`macro_field` as the 8-bit PNG the terrain op stack reads. 8 bits on purpose, per the bit-depth
    floor: 256 levels of a mask that is about to be blurred and eroded is not the same claim as
    256 levels of a heightfield, and the macro-mask gate measures the difference rather than
    asserting it."""
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

    The first spike's version was a global remap of the inverse luminance, and it had one measurable
    defect: on a bright albedo every pixel lands near the top of the band, so the map reads "rough
    everywhere, very slightly less so on the bright bits" (measured 117-242 of 255, mean 206).

    Three changes. Most of the signal is now the deviation from the LOCAL mean, so the map responds
    to the surface rather than to how pale the paint is; a crevice (`cavity`, when the caller has a
    relief field to derive one from) is pushed rougher, because it holds dust and damp regardless of
    its brightness; and the result is stretched by its own 2nd and 98th percentiles, so the band is
    actually occupied instead of being a range the values happen to sit in one corner of. A little
    global inverse luminance is kept, because a genuinely dark damp patch really is rougher than the
    dry stone beside it.
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


def flatness_report(rgb, fraction=DELIGHT_FRACTION):
    """How much of an albedo is a LIGHTING ramp rather than the surface, as a number and its parts.

    `low_freq_variation` is the standard deviation of the low-frequency luminance over its mean: a
    genuinely flat-lit tile has almost none, and a photograph with a sky gradient or a shadow across
    it has a lot. Scale-free by construction, so a dark set and a pale set are on the same axis --
    which is the whole reason it is a ratio and not a spread in 8-bit steps.

    The measure the texture family had no equivalent of, and the largest thing this round of
    generated sets found. Every other property of a generated set is measured -- `seam_report` for
    the wrap, `grain_report` for bark direction, atlas cell opacity for a card -- and the one that
    decides whether the surface can be RELIT was not, so a lit albedo reached a hero render before
    anyone said so. Measured across ten generated sets, and the spread is wide enough to gate on:

        bark_conifer                              0.0247   flat, and the prompt clause worked
        very_dark_green_damp_forest_moss           0.0355
        very_dark_wet_bare_earth_footpath          0.0492
        leaf_conifer                              0.0452
        leaf_grass                                0.0509
        bark_broadleaf                            0.0667
        very_dark_wet_grey_granite_bedrock         0.0740
        weathered_silvered_grey_barn_siding        0.0742
        leaf_broadleaf                            0.0965   lit: a sprite's own key and shadow
        very_dark_damp_forest_floor_rotting_brown  0.0989   lit: a raking light across the litter

    `total_variation` is the same ratio over the whole image, and it is reported beside it so the
    two cannot be confused: a mossy floor SHOULD have a lot of total variation, because that is
    texture. Only the low-frequency part is lighting.

    One floor to know before quoting it: the blur window has to hold enough pixels to average the
    texture away. At 1024 the radius is 128 and the window is 257 square, so 66,000 samples and pure
    noise reads as 0.001; at 32 square the window holds 49 and the same noise leaks 0.063 into the
    answer. Every generated set is 1024 or larger, so this is a caveat for a thumbnail rather than a
    limit in practice, and the unit test pins it rather than asserting it away.
    """
    rgb = np.asarray(rgb)
    if rgb.ndim == 2:
        rgb = np.repeat(rgb[:, :, None], 3, axis=2)
    lum = luminance(np.ascontiguousarray(rgb[:, :, :3], dtype=np.uint8))
    low = _box_blur(lum, _radius(lum.shape, fraction))
    mean = max(float(low.mean()), 1e-6)
    return {"low_freq_variation": round(float(low.std()) / mean, 4),
            "total_variation": round(float(lum.std()) / max(float(lum.mean()), 1e-6), 4),
            "low_freq_mean": round(mean, 4),
            "fraction": fraction}


def mask_stops(rgb, opacity, floor=200, percentiles=(5.0, 95.0)):
    """How many STOPS the albedo luminance spans inside an opacity mask, p5 to p95.

    The flatness measure asked the way a leaf card asks it. `flatness_report` reads the whole sheet,
    and on an atlas most of the sheet is cleared background the card never shows; what matters is
    the variation inside the sprite, because that is one material lit from both sides at once.

    Stops rather than a ratio of standard deviations, because that is the unit the answer is obvious
    in: a real leaf varies by a fraction of a stop in colour, so anything over about one stop is the
    sprite's own key and shadow. Measured on three generated atlases -- 1.21
    (broadleaf), 1.82 (conifer), 1.84 (grass).

    Returns None when the mask is empty or the darker percentile lands at zero, which is the honest
    answer rather than an infinity.
    """
    rgb = np.asarray(rgb)
    if rgb.ndim == 2:
        rgb = np.repeat(rgb[:, :, None], 3, axis=2)
    lum = luminance(np.ascontiguousarray(rgb[:, :, :3], dtype=np.uint8)) * 255.0
    mask = np.asarray(opacity)
    if mask.ndim == 3:
        mask = mask[:, :, -1]
    inside = lum[mask >= floor]
    if inside.size < 64:
        return None
    lo, hi = (float(v) for v in np.percentile(inside, percentiles))
    if lo <= 0.5:
        return None
    return round(float(np.log2(hi / lo)), 3)


def mask_light_split(rgb, opacity, floor=200, percentiles=(5.0, 95.0)):
    """(ramp stops, detail stops) inside an opacity mask: the light on a sprite, and its own relief.

    `mask_stops` answers "how much does this sprite vary", and the gate treated the whole of that as
    baked light. For a needle spray that is wrong, and the artist said so: most of a conifer sprig's
    variation is one needle shadowing the next, which is real geometry a flat card cannot carry and
    therefore belongs in the albedo. What cannot be relit is a RAMP -- a key across the sprite, a
    gradient out of the reference photograph -- and that is a different frequency.

    So a least-squares plane is fitted to log2 luminance over the masked texels and the two parts
    are reported apart: the plane's p5..p95 span is the ramp, the residual's is the relief. A plane
    rather than a blur because a blur needs a radius as a fraction of something, and a sprite is a
    thin shape in a mostly empty cell, so "a fraction of the cell" measures the wrong scale and "a
    fraction of the sprite" is a second knob. A plane has no scale.

    Measured on the gate's three atlases, per cell, and on the conifer with a synthetic key baked
    across each cell to prove the split:

        atlas                     ramp                    detail
        leaf_broadleaf            0.16 0.13 0.44 0.09     0.47 0.74 0.30 0.28
        leaf_conifer              0.48 0.17 0.33 0.24     0.98 0.88 1.15 1.14
        leaf_grass                0.35 0.45 0.30 0.13     0.61 1.76 0.49 0.63
        conifer + 1.0 stop key    0.72 0.65 0.54 0.21     0.99 0.89 1.16 1.15
        conifer + 2.0 stop key    0.99 1.12 0.75 0.44     0.99 0.89 1.17 1.15

    The detail column does not move under a key the ramp column tracks stop for stop, which is what
    makes this a split and not a reweighting. `leaf_conifer` is the case in question: 1.143 stops
    total against the old 1.0 bar, of which 0.48 at most is light.

    Returns (None, None) when the mask holds too little to fit, which is the honest answer.
    """
    rgb = np.asarray(rgb)
    if rgb.ndim == 2:
        rgb = np.repeat(rgb[:, :, None], 3, axis=2)
    lum = luminance(np.ascontiguousarray(rgb[:, :, :3], dtype=np.uint8)) * 255.0
    mask = np.asarray(opacity)
    if mask.ndim == 3:
        mask = mask[:, :, -1]
    ys, xs = np.nonzero(mask >= floor)
    if len(ys) < 64:
        return None, None
    value = lum[ys, xs]
    lit = value > 0.5              # log2 of a black texel is not a light level
    ys, xs, value = ys[lit], xs[lit], value[lit]
    if len(value) < 64:
        return None, None
    logs = np.log2(value.astype(np.float64))
    basis = np.stack([np.ones(len(ys)), ys.astype(np.float64), xs.astype(np.float64)], axis=1)
    coefficients, *_ = np.linalg.lstsq(basis, logs, rcond=None)
    plane = basis @ coefficients
    lo, hi = (float(v) for v in np.percentile(plane, percentiles))
    rlo, rhi = (float(v) for v in np.percentile(logs - plane, percentiles))
    return round(hi - lo, 3), round(rhi - rlo, 3)


def cell_light_split(rgb, opacity, cols, rows, floor=200):
    """`mask_light_split` per atlas cell, cell 0 bottom-left, row-major upward.

    Per cell rather than per sheet, because a sheet's own answer is dominated by four sprites being
    four different greens, which is `cell_distinctness` doing its job and not a light on any card.
    """
    a = np.asarray(opacity)
    if a.ndim == 3:
        a = a[:, :, -1]
    cols, rows = int(cols), int(rows)
    h, w = a.shape
    ch, cw = h // rows, w // cols
    out = []
    for r in range(rows):
        for c in range(cols):
            y0, x0 = h - (r + 1) * ch, c * cw
            ramp, detail = mask_light_split(np.asarray(rgb)[y0:y0 + ch, x0:x0 + cw],
                                            a[y0:y0 + ch, x0:x0 + cw], floor=floor)
            out.append({"cell": r * cols + c, "ramp_stops": ramp, "detail_stops": detail})
    return out


def delight(rgb, fraction=DELIGHT_FRACTION, strength=DELIGHT_STRENGTH):
    """Divide a heavily blurred luminance out of an albedo, renormalised to preserve the mean.

    The cheap standard delighting: the low frequency of a photographed surface is overwhelmingly the
    LIGHT on it -- a sky gradient, a shadow falling across it, the sprite's own key -- and dividing
    it out leaves the reflectance. The mean is put back afterwards, so the surface keeps the
    brightness it was authored at and the texture-set brightness rule (which the block-out tints
    were matched against) still holds. Per channel through one shared luminance gain rather than per
    channel independently, or the correction shifts the hue wherever the light was coloured.

    `strength` interpolates from no correction to full division, because a full divide also removes
    genuine large-scale albedo variation -- a patch of moss really is a different colour from the
    earth beside it. `fraction` is the blur radius as a fraction of the image, and it is coarse on
    purpose: anything finer starts eating the surface instead of the light.

    Not the default, and that is a decision rather than caution. Delighting a set that is already
    flat is nearly a no-op, but it is not free, and the master multiplies AO into the albedo
    (`core/materials/texset.py`), so turning it on globally moves every render baseline that reads a
    generated set. `flatness_report` is what says whether a given set wants it; the tools take it as
    an argument and record what they did.
    """
    rgb = np.asarray(rgb)
    if rgb.ndim == 2:
        rgb = np.repeat(rgb[:, :, None], 3, axis=2)
    rgb = np.ascontiguousarray(rgb[:, :, :3], dtype=np.uint8)
    strength = float(np.clip(strength, 0.0, 1.0))
    if strength <= 0.0:
        return rgb
    lum = luminance(rgb)
    low = _box_blur(lum, _radius(lum.shape, fraction))
    target = max(float(low.mean()), 1.0 / 255.0)
    # A gain rather than a difference, because light is multiplicative. Floored well away from zero:
    # a texel in deep shadow would otherwise be multiplied up by a huge factor and come back as
    # amplified noise, which is the one way this correction can look worse than the defect.
    gain = target / np.maximum(low, DELIGHT_FLOOR)
    gain = 1.0 + strength * (gain - 1.0)
    out = np.asarray(rgb, dtype=np.float32) * gain[:, :, None]
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def derive(albedo_png, delight_strength=0.0, delight_fraction=DELIGHT_FRACTION):
    """{role: array} for the maps Bob writes, from raw PNG bytes or a uint8 RGB array.

    One relief field feeds height, normal and AO; see the module docstring for why cavity is a
    signal here rather than a sixth file.

    `delight_strength` above zero runs `delight` FIRST, so every derived map describes the delit
    surface rather than the lit one. That ordering is the point: the derivations are all luminance
    reads, so deriving them from a lit albedo bakes the same lighting into the height, the normal,
    the roughness and the AO, and the AO then gets multiplied back into the albedo by the master.
    Measured on the forest-floor set that gate shipped: AO against basecolor luminance 0.656, and
    against the relief field it is derived from 0.806.
    """
    rgb = read_png(albedo_png) if isinstance(albedo_png, (bytes, bytearray)) else albedo_png
    rgb = np.asarray(rgb)
    if rgb.ndim == 2:
        rgb = np.repeat(rgb[:, :, None], 3, axis=2)
    rgb = np.ascontiguousarray(rgb[:, :, :3], dtype=np.uint8)
    if delight_strength:
        rgb = delight(rgb, fraction=delight_fraction, strength=delight_strength)
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
    # wrap line and pushes the discontinuity into the middle of the band, where the ramp absorbs it
    # -- the offset-blend trick, but between two renders of the same content rather than between
    # unrelated pixels, which is why it costs far less contrast than the first spike measurement of
    # the WAS blend did.
    alt = array[array.shape[0] - pad:].astype(np.float32)
    ramp = np.linspace(0.0, 1.0, pad, endpoint=False, dtype=np.float32)
    ramp = ramp.reshape((pad,) + (1,) * (core.ndim - 1))
    core[:pad] = (1.0 - ramp) * alt + ramp * core[:pad]
    return core


def crop_wrap_blend(array, pad):
    """Crop a wrap-padded image back to its core, made periodic again.

    `wrap_pad` alone is not enough after a non-periodic operation. The padded image's two copies of
    each edge band are processed independently and drift apart, so a plain crop just moves the seam
    rather than removing it (measured on `tex_upres`: pad-and-crop took the upscale from ratio 3.43
    to 2.08, and no crop position does better, because a non-periodic image has no periodic window).
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


# -- Grain direction (BobFoliage: bark) ------------------------------------------------------
# `seam_report` measures CONTINUITY across the wrap and says nothing about DIRECTION, which is the
# property bark actually needs: grain runs along the trunk, and a tileable SDXL pass has no reason
# to keep an axis. Measured: the two failures are different and only one of them is caught by
# an intensity measure:
#
#   "rough conifer bark" (no clause)      grain 83.8 deg off vertical, coherence 0.487
#   "grey beech bark" (no clause)         grain 18.3 deg off vertical, coherence 0.018
#   + "vertical bark, deep furrows
#      running top to bottom"             grain 1.6 to 17.6 deg off vertical, coherence 0.41 to 0.48
#
# So a set can be strongly directional in the WRONG direction (the first line came back as
# polygonal mud cracks, which have plenty of coherent edges), and a set can have no direction at all
# (the second was isotropic). The angle catches the first, the coherence catches the second, and
# neither catches both -- which is why this returns both and `comfy.BARK_SUFFIX` exists.
GRAIN_BINS = 18  # 10 degrees per bin over the 180 an AXIS spans


def grain_report(array, bins=GRAIN_BINS, blocks=4):
    """The dominant gradient AXIS of an image, its concentration, and how much it wanders.

    Doubled-angle (structure-tensor) averaging, because grain is an axis and not a direction: a
    furrow edge points left on one side and right on the other, and averaging the raw angles cancels
    them to nothing. Doubling maps both to the same place, so the mean survives.

    Reported in two frames, because the useful question is asked in the second:

      `dominant_deg`   the gradient axis, 0 = the gradient runs across the image (vertical stripes)
      `grain_deg`      that axis turned 90 degrees, i.e. the direction the FEATURES run
      `off_vertical`   how far the grain is from straight up, 0..90. This is the bark number: the
                       sweep's V runs along the limb, so vertical grain in the image is grain along
                       the branch.

    `coherence` is 0 for an isotropic image and 1 for perfect stripes -- measured, 0.003 on white
    noise and 1.000 on a sine grating. `block_spread_deg` is the circular spread of the per-block
    axes, which is the "consistent across the wrap" half: one number for the whole image can be
    dominated by a strong band and say nothing about whether the rest of the tile agrees with it.
    """
    a = np.asarray(array, dtype=np.float32)
    if a.ndim == 3:
        a = a[:, :, :3].mean(axis=2)
    gy, gx = np.gradient(a)
    mag = np.hypot(gx, gy)
    theta = np.arctan2(gy, gx)
    two = 2.0 * theta
    total = float(mag.sum()) or 1.0
    cx, cy = float((mag * np.cos(two)).sum()), float((mag * np.sin(two)).sum())
    dominant = float(np.degrees(np.arctan2(cy, cx) / 2.0) % 180.0)
    hist = np.zeros(int(bins), dtype=np.float64)
    idx = ((np.degrees(theta) % 180.0) / (180.0 / bins)).astype(np.int32) % int(bins)
    np.add.at(hist, idx.ravel(), mag.ravel())
    hist /= hist.sum() or 1.0

    axes = []
    if blocks > 1:
        h, w = a.shape
        bh, bw = h // blocks, w // blocks
        for by in range(blocks):
            for bx in range(blocks):
                tile = a[by * bh:(by + 1) * bh, bx * bw:(bx + 1) * bw]
                if tile.size:
                    axes.append(grain_report(tile, bins=bins, blocks=0)["dominant_deg"])
    grain = (dominant + 90.0) % 180.0
    return {"dominant_deg": dominant, "grain_deg": grain,
            "off_vertical_deg": min(abs(grain - 90.0), 180.0 - abs(grain - 90.0)),
            "coherence": float(np.hypot(cx, cy) / total),
            "hist": [float(v) for v in hist], "block_axes": axes,
            "block_spread_deg": axis_spread(axes) if axes else 0.0}


def axis_spread(angles):
    """The circular spread of a set of AXES (each mod 180), in degrees. 0 = all the same axis.

    The circular standard deviation of the doubled angles, halved back. Doubled for `grain_report`'s
    reason, and halved so the number is readable in the frame it was given in.
    """
    if len(angles) == 0:
        return 0.0
    two = np.radians(np.asarray(angles, dtype=np.float64) * 2.0)
    r = float(np.hypot(np.cos(two).mean(), np.sin(two).mean()))
    return float(np.degrees(np.sqrt(max(0.0, -2.0 * np.log(max(r, 1e-9))))) / 2.0)


# -- Leaf atlases (BobFoliage) --------------------------------------------------------------- A
# card reads ONE CELL of an atlas, so an atlas has two properties a texture set does not: every cell
# must carry a sprite (an empty cell is a card that renders as nothing), and each sprite must grow
# from its cell's BOTTOM EDGE, because the card's v is 0 at the twig it hangs from (docs/FOLIAGE.md
# 2.3, 4.4).
#
# **SDXL cannot be asked for a grid.** Measured with a 2x2 grid-layout prompt through
# `mesh_subject`: the result was FIVE sprays arranged in a ring, straddling the cell boundaries,
# each pointing a different way and none touching a cell's bottom edge. Per-cell coverage passed (8
# to 11% opaque in every quadrant) and the atlas was still unusable, which is exactly the
# "tree-shaped is not right" failure this whole track is about. So Bob generates ONE sprite per cell
# and composes the grid itself: the layout becomes something Bob guarantees rather than something it
# hopes for, a different seed per cell makes the cells differ by construction, and 4 sprites at 512
# cost less than 1 at 1024.
ATLAS_MARGIN = 0.02       # of a cell, kept clear so a sprite's silhouette is not clipped by its edge
ATLAS_BLEED_PASSES = 8    # how far the leaf colour is pushed into the transparent region


# The bars below are `core/gen_bars.py`'s, read rather than restated -- their evidence and their
# derivation dates live there. Everything else in this module that looks like a threshold is not
# a bar: the delight, roughness, AO, cavity and normal constants SHAPE a map rather than judging
# one, and no asset passes or fails on them.
ATLAS_OPAQUE = _bar("atlas_opaque")


# How much of the axis, at each end, the base test looks at. The twig is thin only near its cut
# end: measured over a whole HALF the needles dilute it to a coin toss (a symmetric spray came back
# 46.5 against 53.7, a 1.15x separation), and over the outer fifth the same spray separates 4.3
# against 32.4. Narrow enough to see the bare stub, wide enough not to ride on a dozen pixels.
AXIS_END_BAND = 0.20


def _mask_axis(mask, band=AXIS_END_BAND):
    """(centroid, unit axis, end widths) of a boolean mask's principal axis, or None if empty.

    The axis is the eigenvector of the mass's covariance with the larger eigenvalue, i.e. the
    direction a spray is longest along. Oriented so it points toward the FAN, because that is how
    the base is told from the tip: a needle spray is a bare twig at one end and a fan at the other,
    so the end that is NARROW across the axis is the end that attaches to a branch.

    Geometric on purpose. The obvious alternative -- the twig is brown and the needles are green --
    separates the clear cases better (green excess -14 against +25 on the same spray) and assumes a
    colour, so it would pick the wrong end of an autumn or dead-foliage atlas. `atlas_cells` reports
    `base_taper` either way, so a sprite this gets backwards is measured, not hidden.
    """
    ys, xs = np.nonzero(mask)
    if len(ys) < 8:
        return None
    y0, x0 = float(ys.mean()), float(xs.mean())
    dy, dx = ys - y0, xs - x0
    cov = np.array([[float((dy * dy).mean()), float((dy * dx).mean())],
                    [float((dy * dx).mean()), float((dx * dx).mean())]])
    vals, vecs = np.linalg.eigh(cov)
    axis = vecs[:, int(np.argmax(vals))]        # (dy, dx), unit
    t = dy * axis[0] + dx * axis[1]             # along the axis
    s = -dy * axis[1] + dx * axis[0]            # across it
    span = float(t.max() - t.min()) or 1.0
    near = np.abs(s[t <= t.min() + band * span])
    far = np.abs(s[t >= t.max() - band * span])
    lo = float(np.sqrt((near ** 2).mean())) if near.size else 0.0
    hi = float(np.sqrt((far ** 2).mean())) if far.size else 0.0
    if lo > hi:
        axis = -axis
        lo, hi = hi, lo
    return (y0, x0), axis, (lo, hi)


WOODY_EXCESS = _bar("woody_excess")
WOODY_MIN, WOODY_MAX = 0.01, 0.40


WOODY_SEPARATION = _bar("woody_separation")


def _woody_axis(rgba, mask):
    """The base-to-tip unit axis from the woody/green split, or None when the cue does not apply."""
    rgb = np.asarray(rgba)[:, :, :3].astype(np.float32)
    excess = rgb[:, :, 1] - (rgb[:, :, 0] + rgb[:, :, 2]) / 2.0
    woody = mask & (excess <= WOODY_EXCESS)
    green = mask & (excess > WOODY_EXCESS)
    total = float(mask.sum())
    if not total or not woody.any() or not green.any():
        return None
    fraction = float(woody.sum()) / total
    if not (WOODY_MIN <= fraction <= WOODY_MAX):
        return None
    wy, wx = np.nonzero(woody)
    gy, gx = np.nonzero(green)
    ys, xs = np.nonzero(mask)
    diagonal = float(np.hypot(ys.max() - ys.min(), xs.max() - xs.min())) or 1.0
    delta = np.array([wy.mean() - gy.mean(), wx.mean() - gx.mean()], dtype=np.float64)
    length = float(np.linalg.norm(delta))
    if length / diagonal < WOODY_SEPARATION:
        return None
    return -delta / length          # green -> woody is base-ward, so the tip is the other way


AXIS_ANISOTROPY_MIN = _bar("axis_anisotropy")


AXIS_TAPER_MAX = _bar("axis_taper")


AXIS_STRAND_CONTRAST_MIN = int(_bar("axis_strand_contrast"))


AXIS_STRONG_TAPER_MAX = _bar("axis_strong_taper")

# The gap, across the axis, that separates one strand from the next. A fraction of the mask's own
# diagonal so a 512 cell and a 2048 one count the same needles, with a floor because two touching
# needles are one strand at any resolution.
STRAND_GAP_FRACTION = 0.01
STRAND_GAP_FLOOR = 2.0


def _mask_anisotropy(mask):
    """How much longer than wide a boolean mask is: sqrt of its covariance eigenvalue ratio.

    1.0 is a mask with no long axis, and for such a mask `_mask_axis`'s eigenvector is the direction
    whatever noise broke the tie in, not the direction the sprite grows in.
    """
    ys, xs = np.nonzero(mask)
    if len(ys) < 8:
        return 0.0
    dy, dx = ys - ys.mean(), xs - xs.mean()
    cov = np.array([[float((dy * dy).mean()), float((dy * dx).mean())],
                    [float((dy * dx).mean()), float((dx * dx).mean())]])
    vals = np.linalg.eigvalsh(cov)
    lo, hi = float(vals.min()), float(vals.max())
    return float(np.sqrt(hi / lo)) if lo > 1e-9 else float("inf")


def _strand_counts(mask, centroid, axis, band=AXIS_END_BAND):
    """(strands at the narrow end, strands at the wide end) along an oriented principal axis.

    `axis` points base-to-tip as `_mask_axis` returns it, so the narrow end is at the low end of the
    projection. Counted on slices rather than over the whole band, because a band held together by
    one needle crossing it is still a fan.
    """
    ys, xs = np.nonzero(mask)
    if len(ys) < 8:
        return 0, 0
    cy, cx = centroid
    dy, dx = ys - cy, xs - cx
    along = dy * axis[0] + dx * axis[1]
    across = -dy * axis[1] + dx * axis[0]
    span = float(along.max() - along.min()) or 1.0
    diagonal = float(np.hypot(ys.max() - ys.min(), xs.max() - xs.min())) or 1.0
    gap = max(STRAND_GAP_FLOOR, STRAND_GAP_FRACTION * diagonal)
    counts = []
    for selected in (along <= along.min() + band * span, along >= along.max() - band * span):
        sub_across, sub_along = across[selected], along[selected]
        if len(sub_across) < 8:
            counts.append(0)
            continue
        edges = np.linspace(float(sub_along.min()), float(sub_along.max()), 12)
        runs = []
        for start, stop in zip(edges[:-1], edges[1:]):
            row = np.sort(sub_across[(sub_along >= start) & (sub_along <= stop)])
            if len(row) >= 3:
                runs.append(1 + int((np.diff(row) > gap).sum()))
        counts.append(int(np.median(runs)) if runs else 0)
    return counts[0], counts[1]


def sprite_orientation(rgba, floor=0.5):
    """Which cue decided this sprite's up, and whether that cue could be read at all.

    The diagnosis `orient_sprite` acts on, returned separately so the atlas receipt can carry it. It
    exists because of the second artist rejection: the orienter had two cues and no way to say
    it had neither. A prompt rewritten from "cluster on one short twig" to "pressed flat like a
    herbarium specimen" -- made to bring `flatness.in_mask_stops` down, and it did, 1.82 stops to
    0.657 -- removed the brown stem AND the elongation, so `_woody_axis` returned None on every cell
    (woody fraction 0.000 to 0.002 against a 0.01 floor) and `_mask_axis` fell back to the principal
    axis of a round shape (anisotropy 1.04 to 1.25). Every cell was turned by an arbitrary angle,
    +91, +71, +103 and -11 degrees, and nothing in the receipt said so.

    Keys:

      `cue`        "woody" (the green/brown split, the direct cue), "axis" (the principal axis's
                   narrow end, a proxy) or "none" (the mask is too small for either).
      `resolved`   whether the deciding cue was readable. FALSE means the rotation is arbitrary and
                   the sprite is as likely sideways or upside down as upright -- the one thing no
                   bounding box, coverage figure or `base_taper` can see.
      `angle_deg`  how far the sprite was turned to stand up, signed, in the image frame.
      `conflict_deg`  how far the two cues disagreed, when both applied. Not a failure on its own:
                   measured on the gate's conifer they were 125 degrees apart on the one cell whose
                   twig stuck out sideways, and the woody cue was right.

    plus the cues' own figures: `woody_fraction`, `woody_separation`, `anisotropy`, `end_ratio`,
    `strands` (narrow end, wide end) and their `strand_contrast`.
    """
    src = np.asarray(rgba)
    if src.ndim != 3 or src.shape[2] != 4:
        raise ValueError(f"sprite_orientation wants (h, w, 4), got {src.shape}")
    mask = src[:, :, 3].astype(np.float32) / 255.0 > floor
    out = {"cue": "none", "resolved": False, "angle_deg": 0.0, "conflict_deg": None,
           "woody_fraction": 0.0, "woody_separation": None,
           "anisotropy": _mask_anisotropy(mask), "end_ratio": None,
           "strands": (0, 0), "strand_contrast": 0}

    rgb = src[:, :, :3].astype(np.float32)
    excess = rgb[:, :, 1] - (rgb[:, :, 0] + rgb[:, :, 2]) / 2.0
    total = float(mask.sum())
    if total:
        out["woody_fraction"] = float((mask & (excess <= WOODY_EXCESS)).sum()) / total

    found = _mask_axis(mask)
    if found is None:
        return out
    (cy, cx), axis, (lo, hi) = found
    out["end_ratio"] = float(lo / hi) if hi else None
    narrow, wide = _strand_counts(mask, (cy, cx), axis)
    out["strands"] = (int(narrow), int(wide))
    out["strand_contrast"] = int(wide - narrow)

    woody = _woody_axis(src, mask)
    if woody is not None:
        out["conflict_deg"] = float(np.degrees(np.arccos(
            float(np.clip(np.dot(woody, axis), -1.0, 1.0)))))
        axis = woody
        out["cue"] = "woody"
        out["resolved"] = True
    else:
        ratio = out["end_ratio"]
        out["cue"] = "axis"
        out["resolved"] = bool(out["anisotropy"] >= AXIS_ANISOTROPY_MIN
                               and ratio is not None and ratio <= AXIS_TAPER_MAX
                               and (out["strand_contrast"] >= AXIS_STRAND_CONTRAST_MIN
                                    or ratio <= AXIS_STRONG_TAPER_MAX))
    out["angle_deg"] = float(np.degrees(np.arctan2(float(axis[1]), -float(axis[0]))))
    out["centroid"] = (float(cy), float(cx))
    out["axis"] = (float(axis[0]), float(axis[1]))
    return out


def orient_sprite(rgba, floor=0.5, report=None):
    """Rotate an RGBA sprite so it stands upright with the end that ATTACHES at the bottom.

    Returned on a square canvas big enough that nothing rotates out of frame; the caller crops to
    the alpha box. A sprite whose mask is too small to have an axis comes back untouched.

    The attaching end is found by `_woody_axis` when the sprite is a green fan on a brown stem, and
    by the principal axis's narrow end (`_mask_axis`) otherwise. Which of the two decided, and
    whether it was readable, is `sprite_orientation`, collected into `report` when a dict is passed.
    This rotates by the same answer either way -- a guess still beats leaving a sideways sprite
    alone, and the receipt is where "that was a guess" belongs.

    Why Bob rotates rather than the prompt asking for it: `mesh_subject` was asked for "the cut end
    of the twig at the bottom of the frame, needles fanning upward" and returned sprays lying
    diagonally with the twig at the LEFT, on which a card attaches by its side and reads as a leaf
    growing sideways out of a branch. The prompt clause is kept anyway (it costs nothing and it
    helps), but the guarantee has to be Bob's, and this is the same argument as the composed grid
    above.

    Bilinear, on PREMULTIPLIED colour. Rotating straight RGBA interpolates the colour of transparent
    pixels into the silhouette, which is the white-fringe failure `ATLAS_OPAQUE` describes from the
    other direction.
    """
    src = np.asarray(rgba)
    if src.ndim != 3 or src.shape[2] != 4:
        raise ValueError(f"orient_sprite wants (h, w, 4), got {src.shape}")
    h, w = src.shape[:2]
    diagnosis = sprite_orientation(src, floor=floor)
    if report is not None:
        report.update(diagnosis)
    size = int(np.ceil(np.hypot(h, w)))
    if "axis" not in diagnosis:
        out = np.zeros((size, size, 4), np.uint8)
        out[(size - h) // 2:(size - h) // 2 + h, (size - w) // 2:(size - w) // 2 + w] = src
        return out
    cy, cx = diagnosis["centroid"]
    axis = diagnosis["axis"]
    # The base-to-tip axis must end up pointing at image "up", which is -y. The map below is the
    # INVERSE rotation (destination offset to source offset), so its first column is where
    # destination "down" comes from: the axis, negated. Hence ca = -axis_y and sa = axis_x, and the
    # signs are worth stating rather than trusting -- getting `sa` backwards rotates the sprite to
    # vertical UPSIDE DOWN, which is a spray hanging by its needles with the twig in the air, and
    # every bounding-box check still passes on it. `base_taper` in `atlas_cells` is what catches it.
    ca, sa = -float(axis[0]), float(axis[1])

    oy, ox = np.meshgrid(np.arange(size, dtype=np.float32) - (size - 1) / 2.0,
                         np.arange(size, dtype=np.float32) - (size - 1) / 2.0, indexing="ij")
    # Inverse map: where in the source does this destination pixel come from?
    sy = ca * oy + sa * ox + cy
    sx = -sa * oy + ca * ox + cx
    alpha = src[:, :, 3].astype(np.float32) / 255.0
    pre = np.concatenate([src[:, :, :3].astype(np.float32) * alpha[:, :, None],
                          alpha[:, :, None]], axis=2)
    y0 = np.floor(sy).astype(np.int32)
    x0 = np.floor(sx).astype(np.int32)
    fy, fx = (sy - y0)[:, :, None], (sx - x0)[:, :, None]
    inside = (y0 >= 0) & (y0 < h - 1) & (x0 >= 0) & (x0 < w - 1)
    y0c, x0c = np.clip(y0, 0, h - 2), np.clip(x0, 0, w - 2)
    samp = ((pre[y0c, x0c] * (1 - fy) + pre[y0c + 1, x0c] * fy) * (1 - fx)
            + (pre[y0c, x0c + 1] * (1 - fy) + pre[y0c + 1, x0c + 1] * fy) * fx)
    samp *= inside[:, :, None]
    a = samp[:, :, 3:4]
    rgb = np.where(a > 1e-4, samp[:, :, :3] / np.maximum(a, 1e-4), 0.0)
    return np.concatenate([np.clip(rgb, 0, 255), np.clip(a * 255.0, 0, 255)],
                          axis=2).astype(np.uint8)


def place_sprite(rgba, size, margin=ATLAS_MARGIN, floor=0.5, report=None):
    """One oriented sprite cropped to its alpha box and dropped BOTTOM-ANCHORED into a square cell.

    Nearest-neighbour on the way down, deliberately: a matte wants no colours that were not already
    in it, and a needle one pixel wide survives a nearest resample and is blurred away by a smooth
    one. Horizontally centred on the sprite's own base, not on its bounding box, so a spray that
    leans still hangs from the middle of the cell's bottom edge.
    """
    src = orient_sprite(rgba, floor=floor, report=report)
    alpha = src[:, :, 3].astype(np.float32) / 255.0
    ys, xs = np.nonzero(alpha > floor)
    out = np.zeros((int(size), int(size), 4), np.uint8)
    if not len(ys):
        return out
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    crop = src[y0:y1, x0:x1]
    ch, cw = crop.shape[:2]
    avail = max(1, int(size * (1.0 - 2.0 * margin)))
    scale = min(avail / ch, avail / cw)
    nh, nw = max(1, int(round(ch * scale))), max(1, int(round(cw * scale)))
    small = crop[(np.arange(nh) * ch // nh).clip(0, ch - 1)][:,
                                                             (np.arange(nw) * cw // nw).clip(0, cw - 1)]
    pad = int(size * margin)
    top = max(0, int(size) - pad - nh)
    # The base's own x, so a leaning spray still attaches in the middle of the cell.
    base_rows = small[:, :, 3].astype(np.float32) / 255.0
    bottom_band = base_rows[max(0, nh - max(1, nh // 8)):]
    bx = np.nonzero(bottom_band.sum(axis=0) > 0)[0]
    anchor = int(bx.mean()) if len(bx) else nw // 2
    left = int(np.clip(int(size) // 2 - anchor, 0, max(0, int(size) - nw)))
    out[top:top + nh, left:left + nw] = small
    return out


def alpha_bleed(rgb, alpha, passes=ATLAS_BLEED_PASSES, floor=ATLAS_OPAQUE):
    """Push the opaque colour outward into the transparent region and return the filled RGB.

    An atlas basecolor is written as RGB and its matte as a separate `opacity` map, so the colour of
    a fully transparent texel is not "don't care": bilinear filtering at a silhouette blends it into
    the leaf, and a generation's transparent region is the studio background, so every needle comes
    back with a white rim. Filling outward with leaf colour is the standard fix and it is visible in
    a frame, not only in a number.
    """
    a = np.asarray(alpha, dtype=np.float32)
    out = np.asarray(rgb, dtype=np.float32).copy()
    known = (a >= floor).astype(np.float32)
    if known.sum() == 0:
        return np.clip(out, 0, 255).astype(np.uint8)
    mean = (out * known[:, :, None]).reshape(-1, 3).sum(axis=0) / known.sum()
    for _ in range(int(passes)):
        acc = np.zeros_like(out)
        wacc = np.zeros_like(known)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            acc += np.roll(np.roll(out * known[:, :, None], dy, axis=0), dx, axis=1)
            wacc += np.roll(np.roll(known, dy, axis=0), dx, axis=1)
        fill = (wacc > 0) & (known < 0.5)
        out[fill] = acc[fill] / wacc[fill][:, None]
        known = np.where(fill, 1.0, known)
    out[known < 0.5] = mean  # everything the flood never reached
    return np.clip(out, 0, 255).astype(np.uint8)


def atlas_compose(sprites, cols, rows, size, margin=ATLAS_MARGIN, orientations=None):
    """Compose oriented, bottom-anchored sprites into (basecolor RGB, opacity grey) of `size`.

    Cell 0 is the BOTTOM-LEFT and the order is row-major upward, matching the recipe's cell-to-UV
    map and the placeholder atlas (`tools/scripts/make_leaf_atlas.py`). `sprites` shorter than
    cols*rows leaves the remaining cells empty, which `atlas_cells` then reports rather than hides.

    `orientations`, when a list is passed, collects one `sprite_orientation` per cell in cell order.
    It is the only place the rotation's confidence survives: once a sprite is composed into the
    grid, its cell looks the same whether it was turned by a cue or by a coin toss.
    """
    cols, rows, size = int(cols), int(rows), int(size)
    cell = size // max(cols, rows)
    canvas = np.zeros((cell * rows, cell * cols, 4), np.uint8)
    for i, sprite in enumerate(sprites[:cols * rows]):
        if sprite is None:
            if orientations is not None:
                orientations.append({"cell": i, "cue": "none", "resolved": False})
            continue
        r, c = divmod(i, cols)
        y0 = cell * rows - (r + 1) * cell  # row 0 at the BOTTOM, the way UV space runs
        report = {} if orientations is not None else None
        canvas[y0:y0 + cell, c * cell:(c + 1) * cell] = place_sprite(sprite, cell, margin=margin,
                                                                    report=report)
        if orientations is not None:
            # `centroid` and `axis` built the rotation; they are not figures to read.
            orientations.append({"cell": i, **{k: v for k, v in report.items()
                                               if k not in ("centroid", "axis")}})
    alpha = canvas[:, :, 3].astype(np.float32) / 255.0
    return alpha_bleed(canvas[:, :, :3], alpha), canvas[:, :, 3].copy()


def atlas_cells(opacity, cols, rows, floor=0.5):
    """Per-cell measurements of an atlas matte, cell 0 bottom-left, row-major upward.

    Each entry carries what a card actually depends on:

      `opaque`        the cell's cutout coverage. 0 is a card that renders as nothing.
      `reaches_base`  whether the sprite touches the cell's bottom edge, where the card's v is 0.
                      A sprite floating in its cell is a leaf detached from its twig.
      `base_taper`    the base band's width over the middle band's. Under 1 means the sprite is
                      narrow where it attaches and wide above, i.e. the TWIG end is at the bottom
                      and not the fan -- which is the half of "bottom-anchored" that a bounding box
                      cannot see, and the one an unoriented generation gets wrong.
    """
    a = np.asarray(opacity, dtype=np.float32)
    if a.ndim == 3:
        a = a[:, :, 3] if a.shape[2] == 4 else a[:, :, 0]
    if a.max() > 1.0:
        a = a / 255.0
    cols, rows = int(cols), int(rows)
    h, w = a.shape
    ch, cw = h // rows, w // cols
    out = []
    for r in range(rows):
        for c in range(cols):
            y0 = h - (r + 1) * ch
            solid = a[y0:y0 + ch, c * cw:(c + 1) * cw] > floor
            band = max(1, ch // 8)
            base = float(solid[ch - band:].sum())
            middle = float(solid[ch // 2 - band // 2:ch // 2 + band // 2 + 1].sum())
            out.append({"cell": r * cols + c, "opaque": float(solid.mean()),
                        "reaches_base": bool(solid[ch - max(1, ch // 24):].any()),
                        "base_taper": (base / middle) if middle else float("inf")})
    return out


def cell_distinctness(opacity, cols, rows):
    """The mean absolute matte difference of the most SIMILAR pair of cells, in 0..255.

    The atlas analogue of the gate's "two seeds give different trees": four copies of one spray
    passes every per-cell check above and still renders one leaf ten thousand times.
    """
    a = np.asarray(opacity)
    if a.ndim == 3:
        a = a[:, :, 3] if a.shape[2] == 4 else a[:, :, 0]
    cols, rows = int(cols), int(rows)
    h, w = a.shape
    ch, cw = h // rows, w // cols
    tiles = [a[h - (r + 1) * ch:h - r * ch, c * cw:(c + 1) * cw].astype(np.float32)
             for r in range(rows) for c in range(cols)]
    if len(tiles) < 2:
        return float("inf")
    return min(float(np.abs(x - y).mean())
               for i, x in enumerate(tiles) for y in tiles[i + 1:])
