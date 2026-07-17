# 0001 — Repo structure & tooling

**Date:** 2026-07-17
**Status:** Accepted

## Context

A single centralised, long-lived home for procedural Blender work (geometry
nodes, materials, tools, rendering). Author is a technical artist (graphics +
tools background); modeling is out of scope. Add-ons/extensions are maintained
in separate repos.

## Decisions

1. **Structure = library + projects + tools.** Reusable procedural building
   blocks flow out of projects into `library/` and back into new projects via
   Blender's Asset Browser. This flow is the point of the repo.

2. **Version control: Git + Git-LFS.** `.blend` and texture binaries are
   tracked via LFS (`.gitattributes`); renders are gitignored as regenerable.

3. **Blender target: 5.2 LTS**, extensions era. `tools/bob/` uses
   `blender_manifest.toml` so it can `git subtree split` into its own
   extension repo with no rewrite when it's worth publishing.

4. **Asset sharing: Asset Browser library.** `library/` is registered as a
   Blender Asset Library; node groups are marked as assets and organised with
   catalogs (`library/blender_assets.cats.txt`).

## Consequences

- Contributors must run `git lfs install` once.
- The Asset Library path must be registered per machine (Preferences are not
  in the repo). Documented in the top-level README.
- Tools stay dependency-light so the extension graduation path stays clean.

## Future decision records

Add new files here as `NNNN-title.md` when a structural choice is made
(e.g. render farm layout, shot/sequence support, USD pipeline).
