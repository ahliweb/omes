"""The one concrete platform worker skeleton implemented in #66.

`worker.py` is invoked as a bare script by the manager
(``python3 workers/generic_browser/worker.py <operation>``); it drives a
pluggable "browser driver" (see `drivers/`) rather than depending on any
specific browser-automation library. OMES itself never bundles a browser
— see docs/content-distribution.md section 6 and this package's
`worker.py` module docstring.
"""
