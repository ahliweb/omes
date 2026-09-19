"""lib/omes/py/agent - native OMES + Hermes + systemd agent deployment
lifecycle (issue #87). Standard library only (ADR-0012).

Modules:
  jsonschema_lite - minimal stdlib validator for the subset of JSON
                    Schema draft-07 used by contracts/agent/v1.
  manifest        - manifest loading, schema validation, and the extra
                    semantic checks the schema alone cannot express
                    (traversal, unsafe unit identifiers).
  paths           - config/state directory + per-agent path resolution.
  plan            - pure computation of the apply plan (unit name,
                    HERMES_HOME, env references, resource limits) from a
                    validated manifest. No I/O.
  lifecycle       - the declared -> ... -> healthy / degraded|failed|
                    rolled-back state machine (docs/agent-orchestration-
                    roadmap.md section 5).
  state           - per-agent JSON state file read/write (status,
                    history, provenance).
  health          - health aggregation for a deployed agent, reusing
                    lib/omes/py/health's layered model.
  cli             - argparse entry point invoked by lib/omes/cmd/agent.sh.
"""
