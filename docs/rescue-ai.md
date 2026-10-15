# Optional USB Rescue AI companion

> Status: staged in issue [#261](https://github.com/ahliweb/omes/issues/261). This
> repository currently contains only the `contracts/rescue-ai/v1/` schema and
> fixtures plus this document. The external collector, Ventoy media builder, and
> `omes rescue` CLI are not implemented here (Not implemented yet, tracked in #261);
> the rescue companion itself is an external operator-run system.

## Boundary

The companion is an operator-run external system, not an OMES ISO or a second
agent runtime:

- **Ventoy/Linux live media** boots the affected computer or provides tools.
- **Raspberry Pi 5 8 GB or equivalent ARM64 SBC** can act as an independent
  evidence workstation when the target disk is connected through a suitable
  USB-SATA/NVMe adapter.
- **OpenCode Go** is the explicit AI provider route. Hermes/OpenCode own model
  and provider routing; OMES does not implement another LLM router.
- **OMES** owns deterministic validation, provenance, bounded evidence, and
  reconciliation. It does not build an ISO, partition disks, or silently repair
  a target system.

A USB flash drive alone cannot replace the target computer's CPU/RAM. A Pi/SBC
is a separate computer and needs its own power, storage, network, and usually a
display or SSH path.

## Evidence contract

`contracts/rescue-ai/v1/rescue-evidence.schema.json` accepts only bounded metadata:

- source live platform, boot mode, timestamps, opaque target identifier;
- tool and release identifiers;
- allowlisted check IDs and pass/fail/warn status;
- evidence manifest count, storage class, and SHA-256;
- explicit OpenCode Go provider/model identity without credentials;
- analysis, mutation, and verification status;
- data classification and closed source references.

It rejects prompts, model responses, raw logs, credentials, private keys,
arbitrary command fields, and extra properties. Raw evidence belongs in an
operator-controlled store and must not be sent to OpenCode Go when classified
`restricted`.

### Cloud egress is fail-closed in the contract

OpenCode Go is a cloud destination, so the contract applies the classification x
destination rules of
[docs/ai-data-privacy-and-model-security.md](ai-data-privacy-and-model-security.md)
(section 4, [ADR-0029](adr/0029-ai-data-boundary-and-private-inference.md)).
`classification` uses the lowercase egress classes `public`, `internal`,
`confidential`, `restricted`. The schema (a `oneOf` over mutually exclusive
branches, because the repository's stdlib validator has no `if/then`) accepts
`ai_analysis_status: completed` only when all of the following hold:

- `ai_provider.destination_class` is `cloud` (an `unknown` destination is
  denied, matching the evaluator's fail-closed unknown-destination rule);
- `classification` is `public`, `internal`, or `confidential`. **`restricted`
  can never be recorded as a completed cloud analysis**, with or without a
  decision reference, matching `AI_EGRESS_DENY_RESTRICTED_CLOUD_SANITIZED`;
- `egress_decision_ref` is present and names the recorded `allow` outcome from
  the ai-egress policy evaluator (`policy_version`, `classification`,
  `destination: cloud_sanitized`, `decision: allow`, `reason_code`), with a
  `classification` and `reason_code` matching the branch. For `confidential`
  data (cloud is deny-by-default, `approval_required` in the evaluator)
  `egress_decision_ref.approval_id` must additionally reference the owner
  approval recorded through the Control Center approval contract.

`not_run`, `manual_intervention`, and `blocked` statuses record that nothing was
sent to a model and are accepted for any classification. The reference is
metadata only: this contract does not evaluate egress policy, verify the
referenced decision exists, or verify sanitization. Those remain the
evaluator's and the owner's responsibility (Not implemented yet: the
`omes rescue validate` cross-check, tracked in #261).

## OpenCode Go procedure

1. Authenticate interactively with OpenCode using `/connect` and select
   **OpenCode Go**. Credentials are stored by OpenCode; never place them in a
   Ventoy partition, report, command argument, or log.
2. Run `/models` and choose an available exact model identifier. Do not assume
   that a model name remains available; record the selected `provider/model`
   identifier only.
3. Verify the network path and perform a minimal non-sensitive probe.
4. Send only a sanitized, bounded summary. Treat all logs as untrusted data and
   require the model to separate facts, hypotheses, missing evidence, and
   read-only next checks.
5. If OpenCode Go is unavailable, produce the evidence report and mark AI status
   `manual_intervention`; do not silently switch providers.

## Read-only collection contract

The future external collector should use a fixed allowlist such as:

- `lsblk -f`, `blkid`, `findmnt`;
- `dmesg`, `journalctl -b` and offline journal queries;
- `efibootmgr -v` where UEFI access is available;
- `smartctl -a` or the corresponding NVMe health query;
- non-modifying LVM/RAID/encryption discovery;
- filesystem checks in non-repair mode;
- IP, route, DNS, and HTTPS connectivity checks.

The collector must record command identity, exit status, timestamp, target
opaque ID, and manifest hash. It must never accept a command string from AI or
from a remote request. Filesystem repair, `grub-install`, NVRAM changes,
partitioning, formatting, and disk writes require a human approval gate,
backup/image reference, rollback plan, and post-action read-back verification.

## Recovery media workflow

1. Verify Ventoy and Linux Mint ISO provenance/checksums.
2. Install Ventoy only to the confirmed USB whole disk; this erases that USB.
3. Copy ISO files to the Ventoy data partition; do not write the ISO with `dd`.
4. Boot the live environment and record UEFI/Legacy and Secure Boot state.
5. Connect the affected disk read-only first. For formal forensic work, prefer a
   suitable hardware write blocker; software read-only controls have limitations.
6. If the disk has I/O errors, image to a separate destination with GNU
   ddrescue and a mapfile before attempting filesystem repair.
7. Produce bounded metadata and a separate evidence manifest.
8. Use OpenCode Go only on sanitized evidence and preserve the operator's final
   decision separately from model output.

## OMES implementation stages

| Stage | Deliverable | Status |
|---|---|---|
| 1 | `rescue-ai/v1` bounded schema and valid/invalid fixtures | Contract and fixtures present in this repository; no runtime consumer |
| 2 | Read-only validator and typed `omes rescue validate` command | Planned in #261 |
| 3 | Provenance recording for media/tools/provider identity | Planned in #261 |
| 4 | External ARM64 collector and report writer | Separate companion workstream |
| 5 | Optional Hermes skill/runbook integration | Delegated to Hermes; no OMES router |
| 6 | Hardware field validation with Pi 5 and USB-SATA/NVMe adapters | Operator/QA lab work |

## Verification requirements

Before calling the integration ready:

- contract fixtures pass through `scripts/check-contracts.py`;
- raw prompt/response/credential/arbitrary-command fixtures fail for the
  intended reason;
- restricted + cloud + completed, confidential + cloud without an egress
  decision/approval reference, and completed with an unknown destination fixtures
  fail the schema;
- validator never executes discovered files or commands;
- checksum mismatch fails closed;
- network failure still permits evidence collection and produces
  `manual_intervention` rather than a false AI success;
- OpenCode Go provider/model identity is recorded without secrets;
- all destructive actions remain approval-required;
- architecture registry, security model, and documentation distinguish the
  staged external companion from implemented OMES code.

## References

- [OpenCode Go](https://opencode.ai/docs/go)
- [OpenCode providers](https://opencode.ai/docs/providers)
- [OpenCode CLI](https://opencode.ai/docs/cli)
- [Ventoy](https://github.com/ventoy/Ventoy)
- [Linux Mint ISO verification](https://linuxmint.com/verify.php)
- [NIST SP 800-86](https://csrc.nist.gov/pubs/sp/800/86/final)
- [GNU ddrescue manual](https://www.gnu.org/software/ddrescue/manual/ddrescue_manual.html)
