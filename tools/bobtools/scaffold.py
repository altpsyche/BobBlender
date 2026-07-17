"""Scaffold a new project from projects/_template/.

Pure stdlib, no dependencies. Usable as a CLI (bob-new-project <name>) or
imported; the MCP server reuses create_project.
"""

import argparse
import shutil
import sys

from . import config
from .naming import slugify


def create_project(name: str, *, force: bool = False) -> str:
    """Create projects/<slug> from the template. Returns the created path."""
    slug = slugify(name)
    if not slug:
        raise ValueError(f"Could not derive a project name from {name!r}.")

    template = config.template_dir()
    if not template.is_dir():
        raise FileNotFoundError(f"Template not found: {template}")

    dest = config.projects_dir() / slug
    if dest.exists():
        if not force:
            raise FileExistsError(f"Project already exists: {dest}")
        shutil.rmtree(dest)

    shutil.copytree(template, dest)

    # Fill the README placeholders so a new project does not ship the raw
    # template text.
    readme = dest / "README.md"
    if readme.is_file():
        text = readme.read_text()
        text = text.replace("<project-name>", name).replace("<project>", slug)
        readme.write_text(text)

    return str(dest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scaffold a new BobBlender project.")
    parser.add_argument("name", help="Project name (will be slugified to kebab-case).")
    parser.add_argument(
        "--force", action="store_true", help="Overwrite if the project exists."
    )
    args = parser.parse_args(argv)

    try:
        path = create_project(args.name, force=args.force)
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Created {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
