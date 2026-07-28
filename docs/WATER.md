# Water: the river and stream surface

The water half of BobSplines. A river or stream curve builds two things: the carved bed, which is
[SPLINES.md](SPLINES.md)'s business, and a **water-surface ribbon** with its own object, its own
attributes and its own BobShader master. This file is the ribbon and the shader.

[SPLINES.md](SPLINES.md) is the curve system it hangs off; [SHADERS.md](SHADERS.md) is the master
contract it is one of the three kinds of.

## What it is

`S_WaterMaster`, the third BobShader master, fed by a swept ribbon that `curve_water` builds along the
same centreline the terrain overlay carves. The one-line thesis: **the geometry carries the physical
facts and the shader carries the look**, and the split is enforced by where the knobs live.

The verdict this exists to answer was blunt: *"very basic, no flow, no interaction, no waves, no
foam, doesn't freeze."* All five are addressed, plus the render setting that turned out to be causing
most of it.

## How it works, in the order data flows

1. **`curve_water` sweeps the ribbon** along the centreline and writes four attributes:
   - `bbt_flow` (FLOAT_VECTOR) the unit downhill tangent scaled by speed, measured |v| 0.36 to 2.2
   - `bbt_foam` (0.02 to 0.50) banks and rapids
   - `bbt_shore` (0 to 1) 0 mid-channel, 1 at the banks, computed from the across-width **profile
     factor** `abs(2*vfac - 1)` so it is width-INDEPENDENT
   - `bbt_depth` metres of water column, `Bed Depth - Water Depth` mid-channel, thinning to 0 at the
     banks
   - `bbt_water_uv` (FLOAT_VECTOR) arc length downstream as U, the profile as V
2. **Gerstner waves displace the ribbon** — three trochoidal components travelling downstream, heading
   from `bbt_flow`, animated by a Scene Time node so they move on playback with no bake, amplitude
   flattened toward the banks, smooth-shaded.
3. **`S_WaterMaster` shades it**, driving Base Color, Roughness, Metallic, Normal, IOR, Alpha and
   Transmission Weight into one Principled BSDF, and ending in the shared `S_Weather` layer like every
   other master.
4. **The scene's EEVEE ray tracing is enabled** when a water material is built, because without it the
   0.92 Transmission never refracts.

### Where the knobs live, and do not duplicate them

Two layers, and confusing them is how you end up with two knobs that fight:

- **Geometry attributes** come from `curve_water` inputs (Width, Water Depth, Bed Depth, Flow Base,
  Foam Bank, Foam Rapids, End Taper, Width Variation, Wave Height / Length / Steepness / Chop), which
  the Paths panel drives LIVE from `bbt_curve` through `_sync_curve_params`. These set the MAGNITUDE
  of the attributes above.
- **Shader knobs** live on the water material's master node (Shallow / Deep Color, Water Roughness,
  IOR, Transmission, Flow Speed, Ripple Strength / Scale, Wave Detail, Surface Texture, Foam Color /
  Amount / Crispness, Shore Foam, Depth Absorption, Depth Opacity, Shoreline Fade, Frozen, Edge Fade),
  edited in the Shaders panel's Water sub-panel.

The rule: **the geometry attribute sets presence; the shader knob sets look.**

## Invariants, each with the number that fixes it

| Invariant | The number |
|---|---|
| **EEVEE must be told to refract.** `use_raytrace_refraction` and `use_screen_refraction` True, `show_transparent_back` False, `surface_render_method` DITHERED, and the scene's `eevee.use_raytracing` on | BLENDED alpha structurally cannot refract; with the flags off, a 0.92 Transmission reads flat opaque grey. This was the single largest cause of "looks basic" |
| **The visible waves are GEOMETRY, not a normal** | one low-frequency flow-advected bump at high strength smears into hair streaks under a grazing view. The shader keeps only a subtle high-frequency detail normal at default strength 0.10; displacement moves **87% of verts between frame 1 and 40**, and amplitude 0 is perfectly flat |
| **Wave components stay near-downstream** | wide cross-wave angles facet on the roughly 1 m width spacing, so the across-width period has to stay large |
| **Pure sine Gerstner needs a domain warp** | three pure sines lock into a mechanical interference lattice; the Wave Chop large-scale noise bending the phase is what breaks it into natural chop |
| **Bed and surface meander in LOCKSTEP** | one shared helper, `blocks.width_multiplier(ng, near, width_var)`, called by BOTH `curve_water` and `curve_overlay`, so the two cannot be hand-matched out of sync. `WIDTH_NOISE_SCALE = 0.05`, floored at 0.15 |
| Width variation is **containment-neutral** | half-width 5.8 to 7.8 in a smooth meander at `width_var` 0.35, dead flat at 6.83 to 6.89 at 0.0; containment after erosion identical to the flat baseline (both about 2% marginal float, max gap about 0.4 m) |
| **The ribbon tapers, it does not clip** | the carve fades its band smoothly over the last End Taper metres; the ribbon used to DELETE verts inside that distance, so the same knob gave a gradual carve and an abrupt water cut. Both now use the same smooth profile, and width goes to about 0 at both tips with **no verts deleted** |
| `bbt_shore` is width-independent | it comes from the profile factor, so the width variation and the end taper cannot distort the shore, foam and depth gradients |
| **A pre-depth ribbon still reads.** The shoreline fade is keyed to `bbt_shore`, never to `bbt_depth` | keying it to depth would make a mis-paired old ribbon (depth 0 everywhere) VANISH instead of degrading. The old shore gradient is kept as a floor under the depth colour for the same reason |
| Depth is read for real | `bbt_depth` 0 at the banks to about 0.7 mid-channel; Beer-Lambert colour by `1 - exp(-Depth Absorption * depth)`, transmission faded by Depth Opacity so the bed hides under a river |
| Freeze works with **no Firmament in the scene** | `frozen = max(Frozen, env-cold)`; the manual path tints icy blue-white itself, the env path is tinted by `S_Weather`'s frost term, so the two never double up. Measured: freezing changes **6.9%** of a ribbon-framed Cycles shot at max pixel delta 0.36 |
| Flow animates | frames 1 to 48 move **3.7%** of pixels |
| Advection never pops | each noise octave is sampled at two phases half a cycle apart and cross-faded by a triangle wave, so the scroll never resets even where `bbt_flow` diverges |

## The versioning rule, which is a trap worth stating twice

**Bump `S_WaterMaster`'s own entry in `_GROUP_VER_OVERRIDE`, not the global `S_GROUP_VER`.**

A global bump rebuilds every cached `S_` group's interface, and an interface rebuild gives sockets new
identifiers, so **every node value drops to 0** — verified. A water-only shader change that bumps the
global version therefore wipes the artist's tuned terrain and surface materials as collateral. The
per-group override exists so the water group alone rebuilds. `S_TexSet` and `S_LeafSeason` carry their
own versions for the same reason.

One consequence to say out loud: **an existing tuned water material keeps its old stored values**
through an in-place group rebuild. Only a freshly built material picks up new defaults, so deleting
the `S_WaterMaster` node group is how you force them.

## Failure modes

| Failure mode | What it looks like | What it actually is |
|---|---|---|
| **the chevron pattern seen through the water** | a regular corduroy lattice across the surface | **not the shader.** It is the carved terrain BED — heightmap grid faceting — seen through clear transmissive water, and it is present with the water surface flat and opaque. Verified by hiding the terrain: the surface alone is smooth. This is a terrain-resolution matter |
| the combing / hair-streak flow | the flow pattern reads as combed hair | a single low-frequency flow-advected bump at high strength under a grazing view. The fix is to make the waves geometry and keep the normal subtle |
| the mechanical wave lattice | a regular interference grid | pure sine Gerstner components. Wave Chop's domain warp is the fix |
| flat opaque grey water in EEVEE | "very basic" | the refraction flags, above |
| water that never freezes | the Frozen knob appears to do nothing | with no Firmament driver the env temperature defaults to 15 C, so the env-cold term is 0; the manual Frozen input is the standalone path |
| a shader flag that silently does not exist | a look change with no effect | Blender 5.2 EEVEE-Next renamed several material and render flags against 4.x. **Probe the names in the console; never guess.** The same class of change bit the curve nodes twice (`GeometryNodeCurveLine` became `GeometryNodeCurvePrimitiveLine`; `ResampleCurve` and `CurveToPoints` `.mode` became menu sockets) |

## Open questions

- **Open.** Caustics, and a true scene-depth read. `bbt_depth` is a geometric column depth, which
  covers absorption, opacity and the shoreline but says nothing about what is BEHIND the surface. A
  real scene-depth read is EEVEE-Next specific; probe before committing.
- **Deferred, and why.** The Flow / Flow Speed and Foam Bank / Foam Amount knob overlap. Both pairs
  work and each has a clear owner in practice (the geometry attribute sets presence, the shader knob
  sets look). Consolidating them is a rename with a migration cost and no measured win.

## Key code

- `core/geonodes/recipes/curve_water.py` — the ribbon, the attributes, the Gerstner displacement
- `core/geonodes/blocks.py` — `width_multiplier`, shared with `curve_overlay`
- `core/materials/water.py` — `water_master_group`, `water_material`, `set_water_render_flags`,
  `enable_eevee_refraction`
- `core/materials/shared.py` — `_GROUP_VER_OVERRIDE`, the wrapper's extra Principled outputs
- `ui/shaders.py` — the Water sub-panel, `_WATER_LOOK` / `_WATER_FLOW` knob lists, the env feed
- `ui/splines.py` — `_sync_curve_params`, `_derived_water`, the Paths panel's Water section

## Verifying a change

Static compile plus a grep for dangling socket names catches typos; it cannot catch a look. Measure
headless with the Blender 5.2 binary, importing `core` directly with no addon register: build terrain,
a river and its ribbon, shade with `materials.water_material`, then assert the ribbon attributes are
non-zero and varied, all seven Principled inputs are LINKED, the refraction flags are set, and — for
freeze — drive the env temperature below 0 and confirm the outputs change.

**The look itself can only be judged rendered**, in EEVEE (ray tracing on, so refraction shows) AND
Cycles, on flat and sloped rivers, with the weather set to winter for the freeze.

Deploying a change in a running Blender: builder code is a **Reload Builders** away; panel code needs
a full addon reload. A look change also needs a freshly built water material, so delete the
`S_WaterMaster` node group or rebuild the river's water.
