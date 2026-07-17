# <project-name>

> One-line description of the piece. What's the idea / the math / the mood?

Copy this folder to start a new project:
```sh
cp -r projects/_template projects/<my-piece>
```

## Layout

| Folder      | Contents |
|-------------|----------|
| `src/`      | Working `.blend` files. Main file: `<name>_v###.blend`. |
| `textures/` | Textures used only by this project. |
| `refs/`     | References specific to this piece. |
| `exports/`  | Deliverables (glTF, USD, stills for sharing). |
| `renders/`  | Output frames/images. **Gitignored.** |

## Notes / log

- **Idea:**
- **Techniques:** (geometry nodes / shaders / sim used)
- **Reusable bits to promote to `library/`:**
- **Render settings:** engine, samples, resolution

## Checklist

- [ ] Main .blend saved in `src/`
- [ ] Render output follows the convention (see `docs/CONVENTIONS.md`)
- [ ] Any reusable node group marked as an asset + appended to `library/`
- [ ] Final render in `renders/`, hero shot copied to `exports/`
