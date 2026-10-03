"""Control plane tests.

This test package is itself named `control_plane`, which would shadow the real
top-level `control_plane` package when pytest imports it from `tests/` (rootdir
"prepend" import mode). Extending `__path__` makes `control_plane.tokens` etc. resolve
to the real modules either way, so `python -m pytest tests/control_plane` works.
"""
from pathlib import Path

_REAL_PACKAGE = Path(__file__).resolve().parents[2] / "control_plane"
if _REAL_PACKAGE.is_dir() and str(_REAL_PACKAGE) not in __path__:
    __path__.append(str(_REAL_PACKAGE))
