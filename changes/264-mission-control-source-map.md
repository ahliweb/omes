---
issue: 264
type: added
---
Added the OMES-side contract surface for the planned AWCMS 3D Mission Control workspace (epic #263, ADR-0031): the `mission-control-scene-view` and `mission-control-replay-window` Control Center v1 schemas with fixtures, the `mission-control-source-map.json` anti-redundancy map that ties every scene object kind to exactly one existing authority, canonical 2D screen, existing action, and replay basis, and the MC1-MC9 consistency guard run by `scripts/check-architecture.py`; documented the derived-composition decision, a deterministic stale-never-shown-as-healthy state rule, and threats CC11-CC19, and corrected stale documentation so the shipped enrollment-token screen (#233, `ahliweb/awcms` PR #825) and the 14-screen AWCMS baseline are recorded accurately. The AWCMS workspace, replay mode, and contextual actions are not implemented yet (tracked in #265, #266, #267).
