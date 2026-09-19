"""Adds lib/omes/py and the repo root to sys.path (stdlib-only, no pip
build step) so tests can `from jobs import store, runner, ...`."""
import os
import sys

_THIS = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS, "..", "..", ".."))
_LIB_PY = os.path.join(_REPO_ROOT, "lib", "omes", "py")
for _p in (_LIB_PY, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
