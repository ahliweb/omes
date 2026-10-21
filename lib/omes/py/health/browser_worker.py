#!/usr/bin/env python3
"""lib/omes/py/health/browser_worker.py - read-only host-readiness evidence
for an OPTIONAL, separately installed browser worker (issue #298, parent
epic #294).

Standard library only (ADR-0012). This module is a *producer* of the
sanitized metadata document defined by
contracts/browser-transfer/v1/browser-worker-readiness.schema.json (issue
#296). It ships NO RPA runtime, NO browser executor, NO Playwright
dependency, and it never launches a browser or opens a browser session. It
extends existing OMES host primitives instead of building parallel ones:

- the #71 model capability probe (`ollama.py`) is called as a library for
  model readiness; its CLI/JSON output is not touched;
- the #80 exposure audit (`exposure.py`) supplies the listener table used to
  find CDP / MCP / Ollama listeners that are bound beyond loopback;
- the #216 AI-privacy destination classifier (`ai_privacy.py`) reports
  whether Hermes routes model calls to a cloud destination;
- `provenance/versions.py`'s Hermes version probe is reused for the optional
  `hermes_version` field.

What it reports (and what it deliberately does NOT):

- ONLY metadata: a state, closed-vocabulary enums, a version label, a UTC
  timestamp and stable reason codes. It never reads the contents of any
  browser profile, cookie store, session file or heartbeat file; it only
  `lstat()`s them (presence, type, mode, mtime) and counts directory
  entries. It never reads `$HERMES_HOME/.env`, `.hermes/` or
  `messages.db`.
- A `ready` claim is a producer claim about THIS HOST at `observed_at`. It
  is not authorization to run a browser job (AWCMS owns that), and it is not
  proof that the worker is isolated: per-job isolation evidence belongs to
  #276 and is NOT integrated yet, so `session_isolation` is reported as
  `unknown` with `ISOLATION_EVIDENCE_NOT_INTEGRATED`, which prevents
  `ready`. Not implemented yet (tracked in #298, depends on #276): the merged
  #282 `omes agent isolation-drift` verifies only the declared compose posture
  of an agent container, not per-job browser profile, egress or cgroup
  ceilings.
- Every probe is read-only and bounded. The only network-active probe is the
  reused #71 Ollama check, which the operator opts into by declaring the
  `ai_assisted` capability profile; it sends a short synthetic non-secret
  prompt, exactly as `omes health ollama` does.

State precedence (documented in docs/browser-worker-readiness.md):
  unsupported > blocked > unknown > degraded > ready
`unsupported` is reserved for an unsupported platform and short-circuits
every other probe. Absence of evidence is never `ready`.

Exit codes: 0 ready; 7 any not-ready state; 4 not configured (a tenant,
server or worker id is missing, so no valid contract document can be
produced - no stdout, an explanation on stderr); 2 usage / invalid
configuration value; 1 internal error (including a self-check failure of the
produced document against the schema - nothing is printed in that case).

Usage:
  python3 browser_worker.py [--format json|human] [--worker-id ID]
         [--capability-profile deterministic|ai_assisted] [--model ID]
         [--facts-stdin]
"""

from __future__ import annotations

import argparse
import datetime
import ipaddress
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess  # nosec B404 - only fixed, read-only, argv-safe commands below
import sys
from typing import Callable, Optional
from urllib.parse import urlparse

_HEALTH_DIR = os.path.dirname(os.path.abspath(__file__))
_PY_ROOT = os.path.dirname(_HEALTH_DIR)  # lib/omes/py
sys.path.insert(0, _HEALTH_DIR)
sys.path.insert(0, _PY_ROOT)
import ai_privacy  # noqa: E402
import exposure  # noqa: E402
import ollama  # noqa: E402
from jobs import schema as schema_mod  # noqa: E402
from provenance import versions as versions_mod  # noqa: E402

EXIT_READY = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_CONFIGURED = 4
EXIT_NOT_READY = 7

SCHEMA_VERSION = "1.0.0"
SCHEMA_RELATIVE_PATH = os.path.join("contracts", "browser-transfer", "v1", "browser-worker-readiness.schema.json")

# Identical to the patterns in the contract schema (a unit test asserts they
# have not drifted from the schema file).
ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9_.:+-]{1,64}$")
MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_./:@+-]{1,128}$")

CAPABILITY_PROFILES = ("deterministic", "ai_assisted")

# Defaults (all overridable; see docs/configuration.md section 15a).
DEFAULT_HEARTBEAT_MAX_AGE_SECONDS = 120
DEFAULT_SESSION_MAX_AGE_SECONDS = 3600
DEFAULT_DISK_MIN_MB = 2048
DEFAULT_MEM_MIN_MB = 2048
DEFAULT_CPU_MIN = 2
HEARTBEAT_FUTURE_SKEW_SECONDS = 5
# Chrome DevTools Protocol remote-debugging ports. 9222-9229 is the same
# range lib/omes/py/health/exposure.py already treats as `browser-control`.
DEFAULT_CDP_PORTS = tuple(range(9222, 9230))
# The example port the Playwright MCP README documents for its HTTP transport.
DEFAULT_MCP_PORTS = (8931,)
DEFAULT_OLLAMA_PORT = 11434

_SUBPROCESS_TIMEOUT_SECONDS = 5.0

SEV_BLOCKED = "blocked"
SEV_UNKNOWN = "unknown"
SEV_DEGRADED = "degraded"
SEV_UNSUPPORTED = "unsupported"
_SEVERITY_RANK = {SEV_UNSUPPORTED: 0, SEV_BLOCKED: 1, SEV_UNKNOWN: 2, SEV_DEGRADED: 3}

READY_CODE = "READINESS_CHECKS_PASSED"

# code -> (severity, one-line meaning, remediation). The single source of
# truth for severity: probes only ever emit codes, never a severity, so the
# documented table and the behaviour cannot disagree. docs/browser-worker-
# readiness.md lists every code and a unit test fails if the two drift.
REASONS: dict = {
    "PLATFORM_UNSUPPORTED": (
        SEV_UNSUPPORTED,
        "this OS/architecture is outside the OMES support matrix",
        "run the worker host on a supported platform (docs/compatibility-matrix.md)",
    ),
    "HOST_FACTS_UNAVAILABLE": (
        SEV_UNKNOWN,
        "platform support could not be determined (no host facts supplied)",
        "run through `omes health browser-worker`, which supplies the host facts",
    ),
    "CAPABILITY_PROFILE_UNDECLARED": (
        SEV_UNKNOWN,
        "the worker's capability profile (deterministic or ai_assisted) is not declared, so the model requirement is unknown",
        "set OMES_BROWSER_WORKER_CAPABILITY_PROFILE (or --capability-profile)",
    ),
    "ADAPTER_VERSION_UNDECLARED": (
        SEV_UNKNOWN,
        "the browser adapter version is not declared; OMES cannot observe an adapter it does not own",
        "set OMES_BROWSER_WORKER_ADAPTER_VERSION to the pinned adapter version",
    ),
    "BROWSER_PIN_UNDECLARED": (
        SEV_UNKNOWN,
        "no pinned browser version is declared, so browser runtime provenance cannot be checked",
        "set OMES_BROWSER_WORKER_BROWSER_PIN to the expected browser version label",
    ),
    "BROWSER_BINARY_MISSING": (
        SEV_BLOCKED,
        "no usable Chromium-family browser binary was found",
        "install the browser the worker pins, or set OMES_BROWSER_WORKER_BROWSER_BIN to its absolute path",
    ),
    "BROWSER_VERSION_UNKNOWN": (
        SEV_UNKNOWN,
        "a browser was found but its version could not be determined",
        "check that `<browser> --version` works for the operator account",
    ),
    "BROWSER_PIN_MISMATCH": (
        SEV_BLOCKED,
        "the observed browser version differs from the declared pin",
        "reinstall the pinned browser, or update the pin after reviewing the new version",
    ),
    "CDP_LISTENER_NONLOOPBACK": (
        SEV_BLOCKED,
        "a browser remote-debugging (CDP) listener is bound beyond loopback",
        "bind it to 127.0.0.1 or use --remote-debugging-pipe; OMES_EXPOSURE_ALLOW does not apply to browser control",
    ),
    "MCP_LISTENER_NONLOOPBACK": (
        SEV_BLOCKED,
        "an MCP server listener is bound beyond loopback",
        "bind it to 127.0.0.1 or use stdio; OMES_EXPOSURE_ALLOW does not apply to browser control",
    ),
    "OLLAMA_LISTENER_NONLOOPBACK": (
        SEV_BLOCKED,
        "an Ollama listener is bound beyond loopback",
        "bind Ollama to 127.0.0.1 (OLLAMA_HOST); see docs/ollama.md",
    ),
    "EXPOSURE_AUDIT_UNAVAILABLE": (
        SEV_UNKNOWN,
        "the listener table could not be read completely (ss missing, failed or timed out)",
        "install iproute2 (`ss`) and re-run; see `omes audit exposure`",
    ),
    "MODEL_NOT_CONFIGURED": (
        SEV_UNKNOWN,
        "the ai_assisted profile is declared but no Ollama model id is configured",
        "set OMES_OLLAMA_MODEL (or --model)",
    ),
    "MODEL_UNAVAILABLE": (
        SEV_DEGRADED,
        "the local model is not healthy (the reused `omes health ollama` check is not ready)",
        "run `omes health ollama` for the layer-by-layer cause; see docs/ollama.md",
    ),
    "MODEL_PROBE_ERROR": (
        SEV_UNKNOWN,
        "the model probe failed unexpectedly, so model readiness is unknown",
        "run `omes health ollama --json` and inspect its output",
    ),
    "EGRESS_MODEL_DESTINATION_CLOUD": (
        SEV_BLOCKED,
        "Hermes is configured to route model calls to a cloud destination",
        "use a local-only posture for browser workers (docs/ai-data-privacy-and-model-security.md); OMES never rewrites Hermes routing",
    ),
    "EGRESS_MODEL_DESTINATION_UNKNOWN": (
        SEV_UNKNOWN,
        "the model destination class could not be determined (no hermes, unreadable or unlisted provider)",
        "run `omes health ai-privacy`; set a documented local provider in Hermes",
    ),
    "ISOLATION_EVIDENCE_NOT_INTEGRATED": (
        SEV_UNKNOWN,
        "per-job browser isolation (and per-job egress/resource ceilings) evidence is not integrated yet",
        "Not implemented yet (tracked in #298, depends on #276); #282 is merged, but `omes agent isolation-drift` verifies only the declared compose posture of an agent container, not per-job browser profile, egress or cgroup ceilings",
    ),
    "ISOLATION_NOT_CONFIGURED": (
        SEV_BLOCKED,
        "the isolation evidence source reports that per-job isolation is not configured",
        "configure per-job isolation (#276) before leasing browser jobs",
    ),
    "RESOURCE_DISK_LOW": (
        SEV_DEGRADED,
        "free disk space is below the declared minimum",
        "free disk space or lower OMES_BROWSER_WORKER_DISK_MIN_MB deliberately",
    ),
    "RESOURCE_MEMORY_LOW": (
        SEV_DEGRADED,
        "available memory is below the declared minimum",
        "free memory or lower OMES_BROWSER_WORKER_MEM_MIN_MB deliberately",
    ),
    "RESOURCE_CPU_LOW": (
        SEV_DEGRADED,
        "usable CPU count is below the declared minimum",
        "provision more CPUs or lower OMES_BROWSER_WORKER_CPU_MIN deliberately",
    ),
    "RESOURCE_FACTS_UNAVAILABLE": (
        SEV_UNKNOWN,
        "disk, memory or CPU facts could not be read",
        "check that /proc/meminfo and the profile filesystem are readable",
    ),
    "PROFILE_ROOT_NOT_CONFIGURED": (
        SEV_UNKNOWN,
        "no browser profile root is declared, so leftover profile directories cannot be checked",
        "set OMES_BROWSER_WORKER_PROFILE_ROOT to the worker's per-job profile directory",
    ),
    "PROFILE_ROOT_MISSING": (
        SEV_DEGRADED,
        "the declared browser profile root does not exist",
        "create it with mode 0700 owned by the worker account",
    ),
    "PROFILE_ROOT_UNSAFE": (
        SEV_BLOCKED,
        "the browser profile root (or an entry in it) is a symlink, not a directory, foreign-owned or accessible to group/other",
        "make the root a real directory owned by the worker account with mode 0700 and remove symlink entries",
    ),
    "ORPHAN_BROWSER_PROCESS": (
        SEV_BLOCKED,
        "an automation browser process has outlived the session ceiling",
        "stop the leftover browser process through the worker's own supervisor; OMES never kills processes",
    ),
    "STALE_PROFILE_DIRECTORY": (
        SEV_BLOCKED,
        "a per-job browser profile directory outlived the session ceiling with no live browser using it",
        "have the worker supervisor delete the leftover profile; OMES never deletes browser profiles",
    ),
    "PROCESS_TABLE_UNAVAILABLE": (
        SEV_UNKNOWN,
        "the process table could not be read, so orphan sessions cannot be ruled out",
        "install procps (`ps`) and re-run",
    ),
    "HEARTBEAT_NOT_CONFIGURED": (
        SEV_UNKNOWN,
        "no worker heartbeat file is declared (no producer exists in OMES yet)",
        "set OMES_BROWSER_WORKER_HEARTBEAT_FILE to a file the worker touches periodically",
    ),
    "HEARTBEAT_MISSING": (
        SEV_DEGRADED,
        "the declared heartbeat file does not exist",
        "start the worker, or fix the heartbeat path",
    ),
    "HEARTBEAT_STALE": (
        SEV_DEGRADED,
        "the heartbeat file is older than the declared freshness window",
        "check that the worker is running; see OMES_BROWSER_WORKER_HEARTBEAT_MAX_AGE_SECONDS",
    ),
    "HEARTBEAT_INVALID": (
        SEV_UNKNOWN,
        "the heartbeat file is not a regular file, is world-writable, is dated in the future or is unreadable",
        "make it a regular file writable only by the worker account",
    ),
    "READINESS_INVARIANT_VIOLATED": (
        SEV_UNKNOWN,
        "internal consistency check failed (a ready state with a non-ready field); reported as unknown",
        "report this as an OMES bug with the redacted output",
    ),
}


class ConfigError(Exception):
    """kind is 'not_configured' (exit 4) or 'invalid' (exit 2)."""

    def __init__(self, kind: str, message: str, names: Optional[list] = None):
        super().__init__(message)
        self.kind = kind
        self.names = names or []


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class Config:
    """Validated operator configuration. Plain attribute bag."""

    def __init__(self) -> None:
        self.tenant_id: str = ""
        self.server_id: str = ""
        self.worker_id: str = ""
        self.capability_profile: str = "unknown"
        self.model: Optional[str] = None
        self.adapter_version: Optional[str] = None
        self.browser_bin: Optional[str] = None
        self.browser_pin: Optional[str] = None
        self.profile_root: Optional[str] = None
        self.heartbeat_file: Optional[str] = None
        self.heartbeat_max_age: int = DEFAULT_HEARTBEAT_MAX_AGE_SECONDS
        self.session_max_age: int = DEFAULT_SESSION_MAX_AGE_SECONDS
        self.disk_min_mb: int = DEFAULT_DISK_MIN_MB
        self.mem_min_mb: int = DEFAULT_MEM_MIN_MB
        self.cpu_min: int = DEFAULT_CPU_MIN
        self.cdp_ports: frozenset = frozenset(DEFAULT_CDP_PORTS)
        self.mcp_ports: frozenset = frozenset(DEFAULT_MCP_PORTS)
        self.home: str = ""
        self.playwright_browsers_path: Optional[str] = None


def _env_str(env: dict, name: str) -> Optional[str]:
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _env_int(env: dict, name: str, default: int) -> int:
    raw = _env_str(env, name)
    if raw is None:
        return default
    if not re.fullmatch(r"[0-9]{1,9}", raw) or int(raw) < 1:
        raise ConfigError("invalid", f"{name} must be a positive integer (got '{raw}')", [name])
    return int(raw)


def _env_abs_path(env: dict, name: str) -> Optional[str]:
    raw = _env_str(env, name)
    if raw is None:
        return None
    if not raw.startswith("/") or "\x00" in raw:
        raise ConfigError("invalid", f"{name} must be an absolute path", [name])
    return os.path.normpath(raw)


def _env_ports(env: dict, name: str, base: tuple) -> frozenset:
    ports = set(base)
    raw = _env_str(env, name)
    if raw is None:
        return frozenset(ports)
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not re.fullmatch(r"[0-9]{1,5}", part) or not 1 <= int(part) <= 65535:
            raise ConfigError("invalid", f"{name} must be a comma-separated list of TCP/UDP ports (got '{part}')", [name])
        ports.add(int(part))
    return frozenset(ports)


def load_config(args: argparse.Namespace, env: dict) -> Config:
    cfg = Config()

    missing = []
    tenant = _env_str(env, "OMES_JOBS_TENANT_ID")
    server = _env_str(env, "OMES_JOBS_SERVER_ID")
    worker = (args.worker_id or "").strip() or _env_str(env, "OMES_BROWSER_WORKER_ID")
    if not tenant:
        missing.append("OMES_JOBS_TENANT_ID")
    if not server:
        missing.append("OMES_JOBS_SERVER_ID")
    if not worker:
        missing.append("OMES_BROWSER_WORKER_ID (or --worker-id)")
    if missing:
        raise ConfigError(
            "not_configured",
            "browser-worker readiness is not configured; cannot emit a valid contract document without: " + ", ".join(missing),
            missing,
        )
    for label, value in (("OMES_JOBS_TENANT_ID", tenant), ("OMES_JOBS_SERVER_ID", server), ("worker id", worker)):
        if not ID_PATTERN.match(value or ""):
            raise ConfigError("invalid", f"{label} must match {ID_PATTERN.pattern}", [label])
    cfg.tenant_id, cfg.server_id, cfg.worker_id = tenant, server, worker

    profile = (args.capability_profile or "").strip() or _env_str(env, "OMES_BROWSER_WORKER_CAPABILITY_PROFILE")
    if profile is not None:
        if profile not in CAPABILITY_PROFILES:
            raise ConfigError("invalid", f"capability profile must be one of {', '.join(CAPABILITY_PROFILES)} (got '{profile}')", ["OMES_BROWSER_WORKER_CAPABILITY_PROFILE"])
        cfg.capability_profile = profile

    model = (args.model or "").strip() or _env_str(env, "OMES_OLLAMA_MODEL")
    if model is not None:
        if model.startswith("-") or not MODEL_ID_PATTERN.match(model):
            raise ConfigError("invalid", f"model id must match {MODEL_ID_PATTERN.pattern} and not start with '-'", ["OMES_OLLAMA_MODEL"])
        cfg.model = model

    for attr, name in (("adapter_version", "OMES_BROWSER_WORKER_ADAPTER_VERSION"), ("browser_pin", "OMES_BROWSER_WORKER_BROWSER_PIN")):
        value = _env_str(env, name)
        if value is not None:
            if not VERSION_PATTERN.match(value) or value.lower() in ("unknown", "none"):
                raise ConfigError("invalid", f"{name} must match {VERSION_PATTERN.pattern} and name a real version", [name])
            setattr(cfg, attr, value)

    cfg.browser_bin = _env_abs_path(env, "OMES_BROWSER_WORKER_BROWSER_BIN")
    cfg.profile_root = _env_abs_path(env, "OMES_BROWSER_WORKER_PROFILE_ROOT")
    cfg.heartbeat_file = _env_abs_path(env, "OMES_BROWSER_WORKER_HEARTBEAT_FILE")
    cfg.heartbeat_max_age = _env_int(env, "OMES_BROWSER_WORKER_HEARTBEAT_MAX_AGE_SECONDS", DEFAULT_HEARTBEAT_MAX_AGE_SECONDS)
    cfg.session_max_age = _env_int(env, "OMES_BROWSER_WORKER_SESSION_MAX_AGE_SECONDS", DEFAULT_SESSION_MAX_AGE_SECONDS)
    cfg.disk_min_mb = _env_int(env, "OMES_BROWSER_WORKER_DISK_MIN_MB", DEFAULT_DISK_MIN_MB)
    cfg.mem_min_mb = _env_int(env, "OMES_BROWSER_WORKER_MEM_MIN_MB", DEFAULT_MEM_MIN_MB)
    cfg.cpu_min = _env_int(env, "OMES_BROWSER_WORKER_CPU_MIN", DEFAULT_CPU_MIN)
    cfg.cdp_ports = _env_ports(env, "OMES_BROWSER_WORKER_CDP_PORTS", DEFAULT_CDP_PORTS)
    cfg.mcp_ports = _env_ports(env, "OMES_BROWSER_WORKER_MCP_PORTS", DEFAULT_MCP_PORTS)

    cfg.home = env.get("HOME", "") or ""
    # Playwright's own documented override. "0" means "inside node_modules",
    # which OMES does not search (it would be inside a Hermes-owned tree).
    cfg.playwright_browsers_path = _env_str(env, "PLAYWRIGHT_BROWSERS_PATH")
    return cfg


# ---------------------------------------------------------------------------
# Side-effecting dependencies (replaceable in tests)
# ---------------------------------------------------------------------------


def _timeout() -> float:
    raw = os.environ.get("OMES_HEALTH_TIMEOUT", "").strip()
    try:
        return float(raw) if raw else 10.0
    except ValueError:
        return 10.0


def _run(cmd: list, timeout: float = _SUBPROCESS_TIMEOUT_SECONDS) -> tuple:
    """Fixed argv, no shell, bounded timeout, stdin closed. Returns
    (returncode, stdout, stderr); -1 = not found/OS error, -2 = timed out."""
    if shutil.which(cmd[0]) is None and not os.path.isabs(cmd[0]):
        return -1, "", f"{cmd[0]} not found on PATH"
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv, no shell, bounded timeout, read-only
            cmd, capture_output=True, text=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -2, "", f"{os.path.basename(cmd[0])} timed out after {timeout}s"
    except OSError as exc:
        return -1, "", str(exc)


_BROWSER_NAMES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")
_BROWSER_PROCESS_RE = re.compile(
    r"^(chromium|chromium-browser|google-chrome|google-chrome-stable|chrome|headless_shell|chrome-headless-shell|chromium_headless_shell)$"
)
_PLAYWRIGHT_DIR_RE = re.compile(r"^(chromium|chromium_headless_shell)-([0-9]{1,9})$")
_PLAYWRIGHT_EXECUTABLES = ("chrome", "headless_shell", "chrome-headless-shell")


def default_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def default_listeners() -> dict:
    return exposure.collect_listeners(_timeout())


def default_ps_bin() -> str:
    return os.environ.get("OMES_PS_BIN", "ps")


def default_processes() -> dict:
    """Reads the process table with one fixed read-only `ps` call. Returns
    {"collected": bool, "rows": [{"pid": int, "age": int, "argv": [str]}]}.
    Arguments are parsed locally to find a few Chromium switches and are
    NEVER copied into any output (they can carry URLs and profile paths)."""
    rc, out, _err = _run([default_ps_bin(), "-ww", "-eo", "pid=,etimes=,args="], _timeout())
    if rc != 0:
        return {"collected": False, "rows": []}
    rows = []
    for line in out.splitlines():
        m = re.match(r"^\s*(\d+)\s+(\d+)\s+(.*)$", line)
        if not m:
            continue
        rows.append({"pid": int(m.group(1)), "age": int(m.group(2)), "argv": m.group(3).split()})
    return {"collected": True, "rows": rows}


def default_resources(cfg: Config) -> dict:
    disk_path = "/"
    if cfg.profile_root and os.path.isdir(cfg.profile_root):
        disk_path = cfg.profile_root
    elif cfg.home and os.path.isdir(cfg.home):
        disk_path = cfg.home
    disk_free_mb: Optional[int] = None
    try:
        disk_free_mb = shutil.disk_usage(disk_path).free // (1024 * 1024)
    except OSError:
        disk_free_mb = None

    mem_mb: Optional[int] = None
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            meminfo = {}
            for line in fh:
                key, _, rest = line.partition(":")
                parts = rest.split()
                if parts and parts[0].isdigit():
                    meminfo[key] = int(parts[0])
        kb = meminfo.get("MemAvailable", meminfo.get("MemFree"))
        mem_mb = kb // 1024 if kb is not None else None
    except OSError:
        mem_mb = None

    cpus: Optional[int] = None
    try:
        cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        cpus = os.cpu_count()
    return {"disk_free_mb": disk_free_mb, "mem_available_mb": mem_mb, "cpus": cpus}


def default_browser_version(binary: str) -> Optional[str]:
    """`<browser> --version`, the same fixed-argv probe `omes health
    versions` uses. Returns the first output line (kept local) or None. It
    never starts a browser session."""
    rc, out, err = _run([binary, "--version"])
    if rc != 0:
        return None
    for text in (out, err):
        for line in text.splitlines():
            line = line.strip()
            if line:
                return line
    return None


def default_ollama_probe(model: str) -> dict:
    # `--model=` form so a value can never be mistaken for another flag.
    return ollama.run([f"--model={model}"])


def default_destination_class() -> str:
    return ai_privacy.collect_destination_class(_timeout())


def default_hermes_version() -> Optional[str]:
    fact = versions_mod.collect_hermes()
    value = fact.get("value") if isinstance(fact, dict) else None
    return value if isinstance(value, str) else None


def default_isolation(_cfg: Config) -> dict:
    """The isolation evidence source. Per-job browser isolation (profile,
    user, filesystem, network egress and resource ceilings per job) is owned
    by #276. The merged #282 `omes agent isolation-drift` verifies only the
    declared compose posture of an agent container (not per-job browser
    profile, egress or cgroup ceilings), so it is deliberately NOT wired in
    here. Until it is integrated this is `unknown`,
    which prevents `ready`. A future integration replaces this one function
    (or injects another provider) to return {"session_isolation": "per_job"
    | "not_configured", "codes": [...]}."""
    return {"session_isolation": "unknown", "codes": ["ISOLATION_EVIDENCE_NOT_INTEGRATED"]}


def default_uid() -> int:
    return os.geteuid()


class Deps:
    """Bundle of every side effect, so each probe is a pure function of its
    inputs under test. Attribute assignment replaces any dependency."""

    def __init__(self) -> None:
        self.now: Callable[[], datetime.datetime] = default_now
        self.listeners: Callable[[], dict] = default_listeners
        self.processes: Callable[[], dict] = default_processes
        self.resources: Callable[[Config], dict] = default_resources
        self.browser_version: Callable[[str], Optional[str]] = default_browser_version
        self.which: Callable[[str], Optional[str]] = shutil.which
        self.ollama_probe: Callable[[str], dict] = default_ollama_probe
        self.destination_class: Callable[[], str] = default_destination_class
        self.hermes_version: Callable[[], Optional[str]] = default_hermes_version
        self.isolation: Callable[[Config], dict] = default_isolation
        self.uid: Callable[[], int] = default_uid


# ---------------------------------------------------------------------------
# Probes. Each returns {"probe": str, "codes": [str], "fields": {...},
# "detail": {...}}. `detail` is bounded counts/labels for human output only;
# it is never part of the contract document.
# ---------------------------------------------------------------------------


def _result(probe: str, codes: Optional[list] = None, fields: Optional[dict] = None, detail: Optional[dict] = None) -> dict:
    return {"probe": probe, "codes": list(codes or []), "fields": dict(fields or {}), "detail": dict(detail or {})}


def probe_platform(facts: dict) -> dict:
    platform_facts = facts.get("platform") if isinstance(facts, dict) else None
    supported = platform_facts.get("supported") if isinstance(platform_facts, dict) else None
    if supported is True:
        return _result("platform")
    if supported is False:
        return _result("platform", ["PLATFORM_UNSUPPORTED"])
    return _result("platform", ["HOST_FACTS_UNAVAILABLE"])


def probe_declarations(cfg: Config) -> dict:
    codes = []
    if cfg.capability_profile == "unknown":
        codes.append("CAPABILITY_PROFILE_UNDECLARED")
    fields = {"capability_profile": cfg.capability_profile}
    if cfg.adapter_version:
        fields["adapter_version"] = cfg.adapter_version
    else:
        codes.append("ADAPTER_VERSION_UNDECLARED")
    return _result("declarations", codes, fields)


def _browser_label(first_line: Optional[str]) -> tuple:
    """(engine, label) from `--version` output, e.g. 'Chromium 122.0.6261.94
    unofficial' -> ('chromium', 'chromium-122.0.6261.94'). Anything that does
    not look like Chromium/Chrome with a dotted version is ('unknown', None);
    the raw line is never returned."""
    if not first_line:
        return "unknown", None
    lowered = first_line.lower()
    m = re.search(r"([0-9]+(?:\.[0-9]+){1,4})", first_line)
    if "chrom" not in lowered or not m:
        return "unknown", None
    product = "chrome" if "google chrome" in lowered else "chromium"
    return "chromium", f"{product}-{m.group(1)}"


def find_playwright_chromium(cfg: Config) -> Optional[dict]:
    """Read-only discovery of a Playwright-installed Chromium in the
    operator's Playwright browser cache - where Hermes' installer places its
    'bundled Playwright Chromium' (`npx playwright install chromium`). It
    inspects directory names and marker/executable presence only: it never
    executes the browser, and it never looks inside Hermes' own install tree
    or `$HERMES_HOME`. The version evidence is the Playwright revision."""
    base = cfg.playwright_browsers_path
    if base == "0":
        return None
    if not base:
        if not cfg.home:
            return None
        base = os.path.join(cfg.home, ".cache", "ms-playwright")
    best = None
    try:
        entries = list(os.scandir(base))
    except OSError:
        return None
    for entry in entries:
        m = _PLAYWRIGHT_DIR_RE.match(entry.name)
        if not m or entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            continue
        if not os.path.isfile(os.path.join(entry.path, "INSTALLATION_COMPLETE")):
            continue
        if not _has_executable(entry.path):
            continue
        revision = int(m.group(2))
        rank = (revision, 1 if m.group(1) == "chromium" else 0)
        if best is None or rank > best[0]:
            best = (rank, revision)
    if best is None:
        return None
    return {"revision": best[1], "label": f"playwright-chromium-{best[1]}"}


def _has_executable(root: str, depth: int = 2) -> bool:
    try:
        with os.scandir(root) as it:
            children = list(it)
    except OSError:
        return False
    for child in children:
        try:
            if child.is_file(follow_symlinks=False) and child.name in _PLAYWRIGHT_EXECUTABLES and os.access(child.path, os.X_OK):
                return True
            if depth > 1 and child.is_dir(follow_symlinks=False) and _has_executable(child.path, depth - 1):
                return True
        except OSError:
            continue
    return False


def probe_browser(cfg: Config, deps: Deps) -> dict:
    engine = "not_installed"
    label: Optional[str] = None
    source = "none"

    if cfg.browser_bin:
        # An explicit declaration is authoritative: if it is unusable we do
        # NOT fall back to another browser (that would report a different
        # runtime than the one the operator pinned).
        real = os.path.realpath(cfg.browser_bin)
        if os.path.isfile(real) and os.access(real, os.X_OK):
            source = "explicit"
            engine, label = _browser_label(deps.browser_version(cfg.browser_bin))
        else:
            source = "explicit_missing"
    else:
        bundled = find_playwright_chromium(cfg)
        if bundled:
            source, engine, label = "playwright_cache", "chromium", bundled["label"]
        else:
            for name in _BROWSER_NAMES:
                found = deps.which(name)
                if found:
                    source = "path"
                    engine, label = _browser_label(deps.browser_version(found))
                    break

    codes = []
    fields = {"browser_engine": engine}
    if engine == "not_installed":
        codes.append("BROWSER_BINARY_MISSING")
        fields["browser_version"] = "none"
    elif label is None:
        codes.append("BROWSER_VERSION_UNKNOWN")
        fields["browser_version"] = "unknown"
        if not cfg.browser_pin:
            codes.append("BROWSER_PIN_UNDECLARED")
    else:
        fields["browser_version"] = label
        if not cfg.browser_pin:
            codes.append("BROWSER_PIN_UNDECLARED")
        elif cfg.browser_pin != label:
            codes.append("BROWSER_PIN_MISMATCH")
    return _result("browser", codes, fields, {"browser_source": source})


def _address_scope(address: str) -> str:
    """'private' for a private/link-local non-loopback address, else
    'public' (wildcard, global and anything unparseable fail toward public)."""
    host = address.split("%", 1)[0].strip("[]")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "public"
    if ip.is_unspecified:
        return "public"
    if ip.is_private or ip.is_link_local:
        return "private"
    return "public"


def _ollama_port() -> int:
    try:
        port = urlparse(ollama.resolve_endpoint()).port
    except ValueError:
        port = None
    return port or DEFAULT_OLLAMA_PORT


def _listener_category(listener: dict, cfg: Config, ollama_port: int) -> Optional[str]:
    port = listener.get("port")
    owner = listener.get("owner")
    if owner == "mcp-server" or port in cfg.mcp_ports:
        return "MCP"
    if owner == "browser-control" or port in cfg.cdp_ports:
        return "CDP"
    if owner == "ollama" or port == ollama_port:
        return "OLLAMA"
    return None


def probe_exposure(cfg: Config, deps: Deps) -> dict:
    collected = deps.listeners()
    codes = []
    scopes = set()
    ollama_port = _ollama_port()
    flagged = 0
    for listener in collected.get("listeners", []):
        if listener.get("bind_class") == "loopback":
            continue
        # CDP, MCP (HTTP/SSE) and the Ollama API are all TCP services. A
        # desktop Chrome's wildcard UDP mDNS socket is not browser control
        # and must not read as an exposed CDP listener.
        if listener.get("proto") != "tcp":
            continue
        category = _listener_category(listener, cfg, ollama_port)
        if category is None:
            continue
        flagged += 1
        codes.append({"CDP": "CDP_LISTENER_NONLOOPBACK", "MCP": "MCP_LISTENER_NONLOOPBACK", "OLLAMA": "OLLAMA_LISTENER_NONLOOPBACK"}[category])
        scopes.add("public" if listener.get("bind_class") == "wildcard" else _address_scope(str(listener.get("address", ""))))

    # Whatever WAS parsed is still evaluated even when the table is
    # incomplete (a timeout can leave a partial table), so a visible exposed
    # listener is reported as blocked rather than hidden behind "unknown".
    if not collected.get("collected"):
        codes.append("EXPOSURE_AUDIT_UNAVAILABLE")
    if "public" in scopes:
        value = "public_blocked"
    elif "private" in scopes:
        value = "private_boundary"
    elif not collected.get("collected"):
        value = "unknown"
    else:
        value = "loopback"
    return _result("exposure", codes, {"exposure": value}, {"flagged_listeners": flagged})


def probe_model(cfg: Config, deps: Deps) -> dict:
    if cfg.capability_profile == "deterministic":
        return _result("model", fields={"model_provider": "none", "model_ready": "not_required"})
    if cfg.capability_profile != "ai_assisted":
        # Profile undeclared: reported by probe_declarations; the model
        # requirement is simply unknown, not "not required".
        return _result("model", fields={"model_provider": "unknown", "model_ready": "unknown"})

    codes = []
    fields: dict = {"model_provider": "ollama", "model_ready": "unknown"}

    try:
        destination = deps.destination_class()
    except Exception:  # noqa: BLE001 - an unreadable destination is "unknown", never a traceback or a pass
        destination = "unknown"
    if destination == "cloud":
        # Hermes routes to a cloud destination: OMES cannot attest a local
        # Ollama provider for this worker. Blocked, and the provider is
        # reported as unknown rather than as a healthy-looking ollama.
        return _result(
            "model",
            ["EGRESS_MODEL_DESTINATION_CLOUD"],
            {"model_provider": "unknown", "model_ready": "unknown"},
            {"destination_class": destination},
        )
    if destination != "local":
        codes.append("EGRESS_MODEL_DESTINATION_UNKNOWN")

    if not cfg.model:
        codes.append("MODEL_NOT_CONFIGURED")
        return _result("model", codes, fields, {"destination_class": destination})

    fields["model_id"] = cfg.model
    try:
        probe = deps.ollama_probe(cfg.model)
    except (Exception, SystemExit):  # noqa: BLE001 - a probe crash must be "unknown", never a traceback or a pass
        codes.append("MODEL_PROBE_ERROR")
        return _result("model", codes, fields, {"destination_class": destination})
    if isinstance(probe, dict) and probe.get("ready") is True:
        fields["model_ready"] = "ready"
    else:
        fields["model_ready"] = "unavailable"
        codes.append("MODEL_UNAVAILABLE")
    return _result("model", codes, fields, {"destination_class": destination})


def probe_isolation(cfg: Config, deps: Deps) -> dict:
    evidence = deps.isolation(cfg) or {}
    value = evidence.get("session_isolation")
    # A provider may only contribute codes from this module's registry; an
    # unregistered code is dropped rather than trusted or allowed to crash.
    codes = [c for c in (evidence.get("codes") or []) if c in REASONS]
    if value == "per_job":
        return _result("isolation", codes, {"session_isolation": "per_job"})
    if value == "not_configured":
        if "ISOLATION_NOT_CONFIGURED" not in codes:
            codes.append("ISOLATION_NOT_CONFIGURED")
        return _result("isolation", codes, {"session_isolation": "not_configured"})
    # Anything else is `unknown`, and an unknown isolation must always weigh
    # at least `unknown` in the overall state, whatever else the provider said.
    if not any(REASONS[c][0] in (SEV_BLOCKED, SEV_UNKNOWN) for c in codes):
        codes.append("ISOLATION_EVIDENCE_NOT_INTEGRATED")
    return _result("isolation", codes, {"session_isolation": "unknown"})


def probe_resources(cfg: Config, deps: Deps) -> dict:
    facts = deps.resources(cfg) or {}
    codes = []
    disk, mem, cpus = facts.get("disk_free_mb"), facts.get("mem_available_mb"), facts.get("cpus")
    if disk is None or mem is None or cpus is None:
        codes.append("RESOURCE_FACTS_UNAVAILABLE")
    if disk is not None and disk < cfg.disk_min_mb:
        codes.append("RESOURCE_DISK_LOW")
    if mem is not None and mem < cfg.mem_min_mb:
        codes.append("RESOURCE_MEMORY_LOW")
    if cpus is not None and cpus < cfg.cpu_min:
        codes.append("RESOURCE_CPU_LOW")
    return _result("resources", codes, detail={"disk_free_mb": disk, "mem_available_mb": mem, "cpus": cpus})


def _user_data_dir(argv: list) -> Optional[str]:
    for token in argv:
        if token.startswith("--user-data-dir="):
            return os.path.normpath(token.split("=", 1)[1]) if token.split("=", 1)[1] else None
    return None


def _is_browser_main(argv: list) -> bool:
    if not argv or not _BROWSER_PROCESS_RE.match(os.path.basename(argv[0])):
        return False
    return not any(token.startswith("--type=") for token in argv[1:])


def _has_automation_flag(argv: list) -> bool:
    for token in argv[1:]:
        if token.startswith(("--headless", "--remote-debugging-port", "--enable-automation")) or token == "--remote-debugging-pipe":
            return True
    return False


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def probe_sessions(cfg: Config, deps: Deps) -> dict:
    codes = []
    detail: dict = {"orphan_processes": 0, "stale_profile_dirs": 0, "unsafe_entries": 0}

    table = deps.processes()
    in_use: set = set()
    if not table.get("collected"):
        codes.append("PROCESS_TABLE_UNAVAILABLE")
    else:
        for row in table.get("rows", []):
            argv = row.get("argv") or []
            browser_like = bool(argv) and bool(_BROWSER_PROCESS_RE.match(os.path.basename(argv[0])))
            if not browser_like:
                continue
            udd = _user_data_dir(argv)
            if udd:
                in_use.add(udd)
            if not _is_browser_main(argv):
                continue
            scoped = _has_automation_flag(argv) or bool(udd and cfg.profile_root and _under(udd, cfg.profile_root))
            if scoped and int(row.get("age", 0)) > cfg.session_max_age:
                detail["orphan_processes"] += 1
        if detail["orphan_processes"]:
            codes.append("ORPHAN_BROWSER_PROCESS")

    if not cfg.profile_root:
        codes.append("PROFILE_ROOT_NOT_CONFIGURED")
        return _result("sessions", codes, detail=detail)

    try:
        root_stat = os.lstat(cfg.profile_root)
    except FileNotFoundError:
        codes.append("PROFILE_ROOT_MISSING")
        return _result("sessions", codes, detail=detail)
    except OSError:
        codes.append("PROFILE_ROOT_UNSAFE")
        return _result("sessions", codes, detail=detail)

    if (
        stat.S_ISLNK(root_stat.st_mode)
        or not stat.S_ISDIR(root_stat.st_mode)
        or root_stat.st_uid not in (deps.uid(), 0)
        or root_stat.st_mode & 0o077
    ):
        codes.append("PROFILE_ROOT_UNSAFE")
        return _result("sessions", codes, detail=detail)

    # Only names, types and timestamps of the root's immediate children are
    # read. Nothing inside a profile directory is ever opened or listed.
    now_ts = deps.now().timestamp()
    try:
        children = list(os.scandir(cfg.profile_root))
    except OSError:
        codes.append("PROFILE_ROOT_UNSAFE")
        return _result("sessions", codes, detail=detail)
    for child in children:
        try:
            if child.is_symlink():
                detail["unsafe_entries"] += 1
                continue
            if not child.is_dir(follow_symlinks=False):
                continue
            if not table.get("collected"):
                continue  # cannot tell whether a profile is in use
            age = now_ts - child.stat(follow_symlinks=False).st_mtime
            child_path = os.path.normpath(child.path)
            referenced = any(_under(used, child_path) for used in in_use)
            if age > cfg.session_max_age and not referenced:
                detail["stale_profile_dirs"] += 1
        except OSError:
            detail["unsafe_entries"] += 1
    if detail["unsafe_entries"]:
        codes.append("PROFILE_ROOT_UNSAFE")
    if detail["stale_profile_dirs"]:
        codes.append("STALE_PROFILE_DIRECTORY")
    return _result("sessions", codes, detail=detail)


def probe_heartbeat(cfg: Config, deps: Deps) -> dict:
    """Freshness is the heartbeat file's mtime and nothing else: the file's
    content is never read (so no format needs to be invented and nothing
    sensitive can leak through it)."""
    if not cfg.heartbeat_file:
        return _result("heartbeat", ["HEARTBEAT_NOT_CONFIGURED"])
    try:
        st = os.lstat(cfg.heartbeat_file)
    except FileNotFoundError:
        return _result("heartbeat", ["HEARTBEAT_MISSING"])
    except OSError:
        return _result("heartbeat", ["HEARTBEAT_INVALID"])
    if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o002:
        return _result("heartbeat", ["HEARTBEAT_INVALID"])
    age = deps.now().timestamp() - st.st_mtime
    if age < -HEARTBEAT_FUTURE_SKEW_SECONDS:
        return _result("heartbeat", ["HEARTBEAT_INVALID"])
    if age > cfg.heartbeat_max_age:
        return _result("heartbeat", ["HEARTBEAT_STALE"], detail={"age_seconds": int(age)})
    return _result("heartbeat", detail={"age_seconds": max(0, int(age))})


def probe_hermes_version(deps: Deps) -> dict:
    raw = deps.hermes_version()
    if not raw:
        return _result("hermes")
    m = re.search(r"([0-9]+(?:\.[0-9]+){1,3})", raw)
    if m and VERSION_PATTERN.match(m.group(1)):
        return _result("hermes", fields={"hermes_version": m.group(1)})
    return _result("hermes")


# ---------------------------------------------------------------------------
# State decision and document assembly (pure)
# ---------------------------------------------------------------------------

FIELD_DEFAULTS = {
    "browser_engine": "unknown",
    "adapter_version": "unknown",
    "browser_version": "unknown",
    "model_provider": "unknown",
    "model_ready": "unknown",
    "session_isolation": "unknown",
    "exposure": "unknown",
}


def decide_state(codes: list) -> str:
    """unsupported > blocked > unknown > degraded > ready. An unregistered
    code is a programming error, not a pass."""
    severities = set()
    for code in codes:
        if code not in REASONS:
            raise ValueError(f"unregistered reason code: {code}")
        severities.add(REASONS[code][0])
    for severity in (SEV_UNSUPPORTED, SEV_BLOCKED, SEV_UNKNOWN, SEV_DEGRADED):
        if severity in severities:
            return severity
    return "ready"


def _ordered_codes(codes: list) -> list:
    unique = sorted(set(codes), key=lambda c: (_SEVERITY_RANK[REASONS[c][0]], c))
    return unique[:20]  # schema maxItems; the most severe codes sort first


def _ready_invariants_hold(fields: dict) -> bool:
    return (
        fields.get("browser_engine") == "chromium"
        and fields.get("session_isolation") == "per_job"
        and fields.get("exposure") == "loopback"
        and fields.get("model_ready") in ("ready", "not_required")
        and fields.get("model_provider") in ("ollama", "none")
        and fields.get("browser_version") not in (None, "unknown", "none")
        and fields.get("adapter_version") not in (None, "unknown")
    )


def assemble(cfg: Config, results: list, now_iso: str) -> dict:
    fields = dict(FIELD_DEFAULTS)
    codes: list = []
    for result in results:
        fields.update(result["fields"])
        codes.extend(result["codes"])

    state = decide_state(codes)
    if state == "ready" and not _ready_invariants_hold(fields):
        codes.append("READINESS_INVARIANT_VIOLATED")
        state = decide_state(codes)
    reason_codes = [READY_CODE] if state == "ready" else _ordered_codes(codes)

    document: dict = {
        "schema_version": SCHEMA_VERSION,
        "tenant_id": cfg.tenant_id,
        "server_id": cfg.server_id,
        "worker_id": cfg.worker_id,
        "state": state,
        "browser_engine": fields["browser_engine"],
        "adapter_version": fields["adapter_version"],
        "browser_version": fields["browser_version"],
        "model_provider": fields["model_provider"],
    }
    if fields.get("model_id"):
        document["model_id"] = fields["model_id"]
    document["model_ready"] = fields["model_ready"]
    document["session_isolation"] = fields["session_isolation"]
    document["exposure"] = fields["exposure"]
    document["capability_profile"] = fields.get("capability_profile", cfg.capability_profile)
    document["observed_at"] = now_iso
    document["reason_codes"] = reason_codes
    if fields.get("hermes_version"):
        document["hermes_version"] = fields["hermes_version"]
    return document


def collect(cfg: Config, deps: Deps, facts: dict) -> tuple:
    """Runs every probe and returns (document, results). An unsupported
    platform short-circuits: nothing else is probed."""
    now_iso = deps.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    platform = probe_platform(facts)
    results = [platform]
    if "PLATFORM_UNSUPPORTED" not in platform["codes"]:
        results.append(probe_declarations(cfg))
        results.append(probe_browser(cfg, deps))
        results.append(probe_exposure(cfg, deps))
        results.append(probe_model(cfg, deps))
        results.append(probe_isolation(cfg, deps))
        results.append(probe_resources(cfg, deps))
        results.append(probe_sessions(cfg, deps))
        results.append(probe_heartbeat(cfg, deps))
        results.append(probe_hermes_version(deps))
    return assemble(cfg, results, now_iso), results


# ---------------------------------------------------------------------------
# Contract self-check and rendering
# ---------------------------------------------------------------------------


def schema_path() -> str:
    root = os.environ.get("OMES_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(_PY_ROOT)))
    return os.path.join(root, SCHEMA_RELATIVE_PATH)


def validate_document(document: dict, path: Optional[str] = None) -> list:
    """Validates against the published contract with the repository's own
    stdlib validator. Returns a list of errors (empty = valid)."""
    schema = schema_mod.load_json(pathlib.Path(path or schema_path()))
    return list(schema_mod.validate(document, schema))


def render_human(document: dict, results: list) -> str:
    lines = [
        "[omes] health browser-worker: state=%s worker=%s tenant=%s server=%s"
        % (document["state"], document["worker_id"], document["tenant_id"], document["server_id"]),
        "[omes]   browser:   engine=%s version=%s adapter=%s"
        % (document["browser_engine"], document["browser_version"], document["adapter_version"]),
        "[omes]   model:     provider=%s ready=%s profile=%s"
        % (document["model_provider"], document["model_ready"], document.get("capability_profile", "unknown")),
        "[omes]   isolation: %s   exposure: %s" % (document["session_isolation"], document["exposure"]),
        "[omes]   observed_at: %s" % document["observed_at"],
    ]
    evidence = []
    for result in results:
        for key, value in sorted(result["detail"].items()):
            if isinstance(value, (int, str)) and value not in ("", None):
                evidence.append(f"{key}={value}")
    if evidence:
        lines.append("[omes]   evidence:  %s" % " ".join(evidence))
    for code in document["reason_codes"]:
        if code == READY_CODE:
            lines.append("[omes]   reason: %s" % code)
            continue
        severity, meaning, remediation = REASONS[code]
        lines.append("[omes]   %s %s: %s" % (severity.upper(), code, meaning))
        lines.append("[omes]     remediation: %s" % remediation)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_facts() -> dict:
    try:
        raw = sys.stdin.read()
        parsed = json.loads(raw) if raw.strip() else {}
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, OSError):
        return {}


def run(argv: Optional[list] = None, env: Optional[dict] = None, deps: Optional[Deps] = None, facts: Optional[dict] = None) -> tuple:
    """Returns (exit_code, stdout_text, stderr_text). No printing, so tests
    (and a future library caller) can exercise the whole CLI contract."""
    parser = argparse.ArgumentParser(prog="browser-worker-health", add_help=True)
    parser.add_argument("--format", choices=("json", "human"), default="json")
    parser.add_argument("--json", action="store_true", help="alias for --format json")
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--capability-profile", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--facts-stdin", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return (EXIT_USAGE if exc.code not in (0, None) else 0), "", ""

    try:
        cfg = load_config(args, dict(os.environ) if env is None else env)
    except ConfigError as exc:
        code = EXIT_NOT_CONFIGURED if exc.kind == "not_configured" else EXIT_USAGE
        return code, "", f"[omes] health browser-worker: {exc}\n"

    host_facts = facts if facts is not None else (_read_facts() if args.facts_stdin else {})
    deps = deps or Deps()
    document, results = collect(cfg, deps, host_facts)

    errors = validate_document(document)
    if errors:
        # A bug in this producer must never leak an off-contract document.
        return EXIT_ERROR, "", "[omes] health browser-worker: internal error: produced document failed contract self-check: " + "; ".join(errors) + "\n"

    exit_code = EXIT_READY if document["state"] == "ready" else EXIT_NOT_READY
    if args.format == "human" and not args.json:
        return exit_code, render_human(document, results) + "\n", ""
    return exit_code, json.dumps(document) + "\n", ""


def main(argv: Optional[list] = None) -> int:
    code, out, err = run(argv)
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    return code


if __name__ == "__main__":
    sys.exit(main())
