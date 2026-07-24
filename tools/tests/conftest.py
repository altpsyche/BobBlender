"""Pytest bootstrap: make the single-source heightfields compute importable.

The compute now lives inside the extension (`core/heightfields`) as the sole committed copy
(P4). Importing `_hfpath` puts that dir on sys.path so tests can `import heightfields`
(and `from heightfields import ...`) exactly as the shipped code does.
"""

import bobtools._hfpath  # noqa: F401  (side effect: adds core/ to sys.path)
