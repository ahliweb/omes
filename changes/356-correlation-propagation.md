---
issue: 356
type: added
---
Correlation and trace IDs now propagate from the Control Center job through the OMES job store, audit log and pull-worker result. The job and worker contracts gain optional `causation_id`, `trace_id` and `span_id` fields (and `generated_ids` on results) as additive, backward-compatible properties; malformed IDs are rejected, never echoed, and a missing `trace_id` or `span_id` is generated locally and marked as such. Job audit lines carry the IDs, and `jobs.correlation.envelopes_for_job()` projects a job's history into valid correlation envelopes that converge through the existing reducer. IDs are evidence, not authority: they change no allowlist, approval or tenant decision. OpenTelemetry export is deferred, and envelope persistence, a CLI emitter and the AWCMS side remain Not implemented yet (tracked in #356); AWCMS must re-vendor the changed `contracts/control-center/v1/` schemas.
