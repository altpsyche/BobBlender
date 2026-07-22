"""Shared UX helpers for the BobBlenderTools panels.

One implementation of the suite's recurring UI patterns (docs/UX-REDESIGN.md, the P1-P7
principles), so every panel speaks the same visual language instead of each inventing its
own idioms:

- context_header: the compact "what am I acting on" line a panel opens with, read from the
  active thing (not panel-local state), with a consistent one-line empty-state hint (P1, P7).
- structural_action: a button for a STRUCTURAL action (one that builds or rebuilds a
  datablock) marked with a shared icon and a short "rebuilds: ..." note, so a structural
  press always reads differently from an instant live knob (P3).
- preset_row: the one preset control (operator_menu_enum with per-item label + description),
  used for every preset in the suite (P4).

Pure draw helpers: no properties, operators, or registration. Panels import and call these;
nothing here holds state, so a Reload Builders or addon re-enable needs no special handling.
"""

# The shared marker for a STRUCTURAL action (builds or rebuilds a datablock), distinct from
# an instant live knob. One icon everywhere so "this control rebuilds" becomes learnable (P3).
# FILE_REFRESH already reads as "rebuild" where the suite uses it today.
STRUCTURAL_ICON = "FILE_REFRESH"

# Reshuffle-a-seed marker. Kept distinct from STRUCTURAL_ICON so a seed reshuffle never
# reads as "this rebuilds": FILE_REFRESH means rebuild, RNDCURVE means new random value.
SEED_ICON = "RNDCURVE"


def context_header(layout, label, value, icon="NONE", empty=None):
    """Draw the "what am I acting on" header (P1), or the empty-state hint (P7).

    label: the role being acted on ("Active mesh", "Emitter", "Terrain").
    value: the active thing's name, or a falsy value when nothing is in scope.
    empty: the one-line "what to do next" hint shown when value is falsy.

    Returns True when something is in scope (caller draws the rest), False on the empty
    state (caller can return after this).
    """
    if value:
        layout.label(text=f"{label}: {value}", icon=icon)
        return True
    if empty:
        layout.label(text=empty, icon="INFO")
    return False


def structural_action(layout, op_idname, text=None, note=None, icon=STRUCTURAL_ICON,
                      enabled=True):
    """Draw a STRUCTURAL action button (P3): the shared marker icon, and below it a small
    de-emphasised note ("rebuilds: the terrain", "builds: falling snow + coverage") so the
    press reads as structural, not a live edit.

    Returns the operator's properties object, so the caller can set fields on it (op.index,
    op.preset, ...). text=None uses the operator's own label.
    """
    col = layout.column(align=True)
    col.enabled = enabled
    op = col.operator(op_idname, text=text, icon=icon) if text is not None \
        else col.operator(op_idname, icon=icon)
    if note:
        cap = col.row()
        cap.enabled = False  # muted caption, so the note reads as a hint not a control
        cap.label(text=note)
    return op


def preset_row(layout, op_idname, prop="preset", text="Preset", icon="PRESET"):
    """The one preset idiom (P4): an operator_menu_enum showing the operator enum's per-item
    label + description. Same control and wording for every preset in the suite (terrain,
    scatter layer, cloud, fog, surface, stack, scene)."""
    return layout.operator_menu_enum(op_idname, prop, text=text, icon=icon)


def seed_row(layout, data, prop, op_idname, text="Seed", op_props=None):
    """The one seed idiom: the seed value and a reshuffle button on one row, marked with
    SEED_ICON so a reshuffle reads distinctly from a structural rebuild.

    data / prop: the thing holding the seed and the property name to draw. A modifier input
    socket passes (socket, "value"); a scene/PropertyGroup passes (props, "seed"). Generalised
    from a hardcoded `.value` socket so both a scene IntProperty and a socket route through here.
    op_props: fields to set on the reshuffle operator (e.g. {"object_name": name}).
    """
    row = layout.row(align=True)
    row.prop(data, prop, text=text)
    op = row.operator(op_idname, text="", icon=SEED_ICON)
    if op_props:
        for key, val in op_props.items():
            setattr(op, key, val)
    return op
