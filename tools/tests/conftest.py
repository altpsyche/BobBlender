"""Pytest bootstrap: make the single-source heightfields compute importable.

The compute now lives inside the extension (`core/heightfields`) as the sole committed copy
the single-compute rule: the compute lives in the extension, never copied into a venv. Importing
`_hfpath` puts that dir on sys.path so tests can `import heightfields`
(and `from heightfields import ...`) exactly as the shipped code does.
"""

import bobtools._hfpath  # noqa: F401  (side effect: adds core/ to sys.path)
