"""Adds lib/omes/py to sys.path so tests can `from agent import ...`
without installing the package (stdlib-only, no pip build step). Also
resolves OMES_ROOT (used by agent.manifest to locate
contracts/agent/v1/agent-deployment.schema.json) if not already set."""
import os
import sys

_THIS = os.path.dirname(os.path.abspath(__file__))
_LIB_PY = os.path.abspath(os.path.join(_THIS, "..", "..", "..", "lib", "omes", "py"))
if _LIB_PY not in sys.path:
    sys.path.insert(0, _LIB_PY)

OMES_ROOT = os.path.abspath(os.path.join(_THIS, "..", "..", ".."))
os.environ.setdefault("OMES_ROOT", OMES_ROOT)
