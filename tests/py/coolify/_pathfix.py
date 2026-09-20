import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_LIB_PY = _ROOT / "lib" / "omes" / "py"
if str(_LIB_PY) not in sys.path:
    sys.path.insert(0, str(_LIB_PY))

os.environ.setdefault("PYTHONPATH", str(_LIB_PY))
