"""Adds the repo root to sys.path (stdlib-only, no pip build step) so tests
can import scripts/generate-control-center-data.py by file path."""
import os
import sys

_THIS = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS, "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
