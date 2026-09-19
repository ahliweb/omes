"""Adds lib/omes/py to sys.path so tests can `from hermesbackup import ...`
without installing the package (stdlib-only, no pip build step)."""
import os
import sys

_THIS = os.path.dirname(os.path.abspath(__file__))
_LIB_PY = os.path.abspath(os.path.join(_THIS, "..", "..", "..", "lib", "omes", "py"))
if _LIB_PY not in sys.path:
    sys.path.insert(0, _LIB_PY)
