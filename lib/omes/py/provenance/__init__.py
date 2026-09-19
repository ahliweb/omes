"""lib/omes/py/provenance - runtime compatibility evidence and supply-chain
provenance (issues #83, #84).

Standard library only (ADR-0012). Nothing here executes untrusted code,
reads secret files (`.env`), or dumps the process environment. Every
public entry point is invoked as a script (`python3 -m` is not used by
the bash callers; they call the file directly with an absolute path),
so this package intentionally keeps `__init__.py` empty of behavior.
"""
