"""Headless check: does the sun a build_sky decided survive a world re-apply? (docs/FIRMAMENT.md)

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \
        --python tools/scripts/headless_sun.py

Exit code 0 = every check passed.

Measures the LAMP -- rotation_euler, energy, angular size -- before and after a re-apply, not the
op's own report, because the whole failure mode is a sun that reads correct at build time and is
silently recomputed at the next re-apply. The world applier re-places the sun from bbt_firmament
plus bbt_env whenever anything touches the world (`apply_world`, `set_env`, `apply_biome`,
`apply_season`, a World slider, or a write to any geographic field, each of which carries its own
update callback). At a night `time_of_day` the recomputed sun is below the horizon, the lamp energy
is zeroed and the physical sky renders black, so the frame comes back with nothing in it but the
emissive geometry -- which reads as a fog or an exposure fault and is neither. Hence: build_sky
RECORDS its sun on bbt_firmament, and this measures that it holds.

The one case that cannot hold is also checked, because build_sky reports it rather than hiding it: a
geographic key passed to the op but never written to bbt_env is recomputed from bbt_env at the next
re-apply, and `data.durable` is False with `data.undurable` naming the keys.
"""

import math
import os
import sys

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions"))

from bob_blender_tools.core import scene as _scene, world as W  # noqa: E402
from bob_blender_tools.ui import firmament, world as ui_world  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # for `_gate`
from _gate import Gate  # noqa: E402

# The shared gate harness (`_gate.py`): one `check` / `note` / exit-code implementation for every
# gate, bound to module-level names so the call sites below read as plain assertions. `FAILURES` is
# the Gate's own list, not a copy, so anything already reading it keeps working.
GATE = Gate("sun gate")
check, note, skip = GATE.check, GATE.note, GATE.skip
FAILURES = GATE.failures


def sun_state():
    """The lamp's aim, energy and angular size, rounded. The tuple a re-apply must not change."""
    sun = bpy.data.objects.get(W.SUN_NAME)
    if sun is None:
        return None
    return (tuple(round(math.degrees(a), 3) for a in sun.rotation_euler),
            round(sun.data.energy, 4), round(math.degrees(sun.data.angle), 4))


def main():
    # firmament owns bbt_env + bbt_firmament and subscribes the sun applier; ui_world owns the
    # Live Environment master the applier is gated on. Together they are the re-apply chain.
    firmament.register()
    ui_world.register()

    scene = bpy.context.scene
    print(f"live_env = {scene.bbt_world.live_env}")

    # A night clock, so the solar model would put the sun under the horizon.
    r = _scene.set_env({"op": "set_env", "params": {"time_of_day": 22.0, "frost": 0.0}})
    print("set_env:", r["info"])

    # 1. A manual override at night: the case that used to be lost.
    r = W.build_sky({"op": "build_sky", "params": {
        "use_override": True, "sun_elevation": 35.0, "sun_azimuth": 120.0, "sun_strength": 3.0}})
    print("build_sky:", r["info"], "\n     data:", r["data"])
    built = sun_state()
    print("     lamp:", built)
    check("override sun is above the horizon and lit", built is not None and built[1] > 0.0,
          f"energy {built[1] if built else None}")
    check("override recorded on bbt_firmament", r["data"]["recorded"] is True)
    check("override reported durable", r["data"]["durable"] is True)
    fm = scene.bbt_firmament
    check("bbt_firmament carries the override",
          fm.use_override is True and abs(fm.override_elevation - 35.0) < 1e-4
          and abs(fm.override_azimuth - 120.0) < 1e-4,
          f"use_override={fm.use_override} el={fm.override_elevation} az={fm.override_azimuth}")
    check("sun_strength recorded", abs(fm.sun_strength - 3.0) < 1e-4, f"{fm.sun_strength}")

    # 2. The re-apply that used to eat it.
    r = _scene.apply_world({"op": "apply_world"})
    print("apply_world:", r["info"])
    after = sun_state()
    check("sun survives apply_world unchanged", after == built, f"{built} -> {after}")

    # 3. And a set_env writing a geographic field, which fires the property callback directly
    #    rather than going through the appliers, so `apply` is not what protects it.
    r = _scene.set_env({"op": "set_env", "params": {"time_of_day": 1.0}})
    print("set_env(time_of_day=1.0):", r["info"])
    after = sun_state()
    check("sun survives a geographic set_env", after == built, f"{built} -> {after}")

    # 4. A build with NO override clears the flag, so the solar model wins again. Without this the
    #    fix would be worse than the bug: a stale override would outrank every later clock.
    r = W.build_sky({"op": "build_sky", "params": {}})
    print("build_sky (solar):", r["info"], "\n             data:", r["data"])
    solar_built = sun_state()
    print("             lamp:", solar_built)
    check("no-override build clears the flag", scene.bbt_firmament.use_override is False)
    check("the night solar sun is dark", solar_built is not None and solar_built[1] == 0.0,
          f"energy {solar_built[1] if solar_built else None}")
    check("solar build reports durable (its clock IS bbt_env)", r["data"]["durable"] is True)
    _scene.apply_world({"op": "apply_world"})
    check("solar sun survives apply_world too", sun_state() == solar_built,
          f"{solar_built} -> {sun_state()}")

    # 5. An explicit clock that never reached bbt_env: built correctly, reported as not durable,
    #    and then measurably lost. The report is the deliverable here, not the survival.
    r = W.build_sky({"op": "build_sky", "params": {"time_of_day": 11.0}})
    print("build_sky (explicit clock):", r["info"], "\n                      data:", r["data"])
    explicit = sun_state()
    check("the explicit clock lights the lamp", explicit is not None and explicit[1] > 0.0,
          f"energy {explicit[1] if explicit else None}")
    check("explicit clock reported NOT durable",
          r["data"]["durable"] is False and r["data"]["undurable"] == ["time_of_day"],
          f"{r['data']}")
    _scene.apply_world({"op": "apply_world"})
    check("and it is indeed lost on re-apply, exactly as reported", sun_state() != explicit,
          f"{explicit} -> {sun_state()}")

    # 6. Clamps: one number for the sky node, the lamp and the recorded prop, at the edges too.
    r = W.build_sky({"op": "build_sky", "params": {
        "use_override": True, "sun_elevation": 120.0, "sun_azimuth": -30.0, "sun_angle": 40.0}})
    print("build_sky (out of range):", r["data"])
    check("elevation clamped to 90", abs(r["data"]["elevation"] - 90.0) < 1e-6)
    check("azimuth wrapped to 330", abs(r["data"]["azimuth"] - 330.0) < 1e-6)
    check("sun_angle clamped to 20", abs(r["data"]["sun_angle"] - 20.0) < 1e-6)
    fm = scene.bbt_firmament
    check("the props match the built values",
          abs(fm.override_azimuth - 330.0) < 1e-4 and abs(fm.sun_angle - 20.0) < 1e-4,
          f"az={fm.override_azimuth} angle={fm.sun_angle}")

    print()
    return GATE.exit_code()


if __name__ == "__main__":
    sys.exit(main())
