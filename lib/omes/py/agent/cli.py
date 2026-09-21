#!/usr/bin/env python3
"""omes agent <subcommand> - CLI entry point for the native OMES + Hermes +
systemd agent deployment lifecycle (issue #87). Invoked by
lib/omes/cmd/agent.sh. Stdlib only (ADR-0012).

Exit codes follow docs/cli.md's stable exit-code contract:
  0 ok, 1 general error, 2 usage error, 4 preflight failed,
  5 privilege error, 6 apply failed, 7 verify failed, 9 backup failed,
  10 rollback failed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # nosec B404 - fixed argv, no shell, bounded/short-lived calls only
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from . import compose as compose_mod
from . import compose_health
from . import compose_preflight
from . import health as health_mod
from . import manifest as manifest_mod
from . import migration as migration_mod
from . import paths, plan as plan_mod, provenance, runtime_bridge, state as state_mod, unitfile
import hashlib
import time

EX_OK = 0
EX_ERROR = 1
EX_USAGE = 2
EX_PREFLIGHT = 4
EX_PRIVILEGE = 5
EX_APPLY = 6
EX_VERIFY = 7
EX_BACKUP = 9
EX_ROLLBACK = 10


def _omes_root() -> Path:
    override = os.environ.get("OMES_ROOT")
    if override:
        return Path(override)
    # lib/omes/py/agent/cli.py -> lib/omes/py/agent -> lib/omes/py -> lib/omes -> lib -> root
    return Path(__file__).resolve().parents[4]


def _print(obj: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, sort_keys=True))
    else:
        print(obj)


def _is_root() -> bool:
    if os.environ.get("OMES_TEST") == "1" and os.environ.get("OMES_FAKE_ROOT") == "1":
        return True
    try:
        return os.geteuid() == 0
    except AttributeError:  # pragma: no cover
        return False


def _load_manifest(name: str, omes_root: Path):
    path = paths.manifest_path(name)
    if not path.exists():
        raise manifest_mod.ManifestError([f"no manifest found for agent '{name}' at {path}"])
    return manifest_mod.load_and_validate(path, omes_root, expected_name=name)


def _check_privilege(service_mode: str) -> Optional[str]:
    """Returns an error string, or None if privilege is correct for
    `service_mode` (user-scope must never run as root; system-scope
    requires root, per AGENTS.md section 3 / issue #87)."""
    root = _is_root()
    if service_mode == "user" and root:
        return "serviceMode 'user' must not be applied as root (run as the target non-root user)"
    if service_mode == "system" and not root:
        return "serviceMode 'system' requires root (the existing non-root service-user policy still applies to the running agent itself)"
    return None


def _service_mode(manifest: dict) -> str:
    if manifest.get("apiVersion") == "omes.ahliweb.com/v2":
        return manifest.get("placement", {}).get("serviceScope", "user")
    return manifest["spec"]["serviceMode"]


def _backend(manifest: dict) -> str:
    if manifest.get("apiVersion") == "omes.ahliweb.com/v2":
        b = manifest.get("placement", {}).get("backend", "native")
        return "systemd" if b == "native" else b
    return manifest["spec"].get("backend", "systemd")


def _check_hermes_profile(profile_ref: str) -> Optional[str]:
    """Resolves Hermes profile existence via supported Hermes CLI ('hermes profile list').
    Returns an error string if Hermes CLI fails or the profile is absent, None if valid."""
    if not shutil.which("hermes"):
        return f"Hermes runtime CLI 'hermes' not found on PATH; required to verify profile '{profile_ref}'"
    try:
        proc = subprocess.run(
            ["hermes", "profile", "list"],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"failed to execute 'hermes profile list': {exc}"

    if proc.returncode != 0:
        return f"'hermes profile list' exited {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"

    lines = proc.stdout.splitlines()
    profiles = set()
    for line in lines:
        cleaned = line.strip().lstrip("*-• ").split()
        if cleaned:
            profiles.add(cleaned[0].rstrip(":"))

    if profile_ref not in profiles:
        return f"Hermes profile '{profile_ref}' not found in active Hermes profiles ({sorted(profiles)}) - fail preflight"
    return None


def _sha256_file(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _hermes_gateway_cmd(
    subcmd: str,
    profile_ref: str,
    extra_args: Optional[List[str]] = None,
    timeout: float = 30.0,
    hermes_home: Optional[str] = None,
) -> Any:
    cmd = ["hermes", "gateway", subcmd]
    if profile_ref and profile_ref != "default":
        cmd.extend(["--profile", profile_ref])
    if extra_args:
        cmd.extend(extra_args)
    env = dict(os.environ)
    if hermes_home:
        env["HERMES_HOME"] = str(hermes_home)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        class _ProcRes:
            returncode = -1
            stdout = ""
            stderr = str(exc)

        return _ProcRes()


def _is_multiplexed_mode(profile_ref: str, hermes_home: Optional[str] = None) -> bool:
    if os.environ.get("SHIM_HERMES_MULTIPLEXED") == "1":
        return True
    try:
        proc = _hermes_gateway_cmd("status", profile_ref, hermes_home=hermes_home)
        if proc.returncode == 0 and "multiplexed" in proc.stdout.lower():
            return True
    except Exception:
        pass
    return False


def _build_systemd_plan(manifest: Dict[str, Any], omes_root: Path) -> Dict[str, Any]:
    """Builds the systemd-backend plan with its unit name resolved
    through lib/omes/py/agent/runtime_bridge.py -> lib/omes/runtime.sh's
    `runtime_agent_service_unit` (the one source of truth for
    hermes-gateway[-<profile>].service naming - issues #85, #87, #96, #175).
    Falls back to plan.py's own `unit_name()` if the bridge cannot run."""
    name = manifest["metadata"]["name"]
    if manifest.get("apiVersion") == "omes.ahliweb.com/v2":
        profile_ref = manifest.get("runtime", {}).get("profileRef", name)
    else:
        profile_ref = manifest.get("spec", {}).get("profile", name)
    scope = _service_mode(manifest)
    unit_override = runtime_bridge.agent_service_unit(profile_ref, scope, omes_root)
    return plan_mod.build_plan(manifest, unit_name_override=unit_override)


def _run_docker(args_list: list, timeout: float = 30.0):
    cmd = ["docker"] + args_list
    try:
        return subprocess.run(  # nosec B603 - fixed argv, no shell
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.TimeoutExpired, OSError) as exc:

        class _Fake:
            returncode = -1
            stdout = ""
            stderr = str(exc)

        return _Fake()


def _compose_backups_dir(name: str) -> Path:
    return paths.agent_state_dir(name) / "compose-backups"


def _backup_previous_compose_file(name: str, compose_file: Path) -> Optional[Dict[str, Any]]:
    """Copies the currently-rendered compose file aside before it is
    overwritten (backup-before-mutate). Returns None if there is no
    prior file (first apply)."""
    if not compose_file.is_file():
        return None
    backups_dir = _compose_backups_dir(name)
    backups_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(backups_dir, 0o700)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    session_dir = backups_dir / ts
    suffix = 1
    while session_dir.exists():
        session_dir = backups_dir / f"{ts}-{suffix}"
        suffix += 1
    session_dir.mkdir(parents=True)
    os.chmod(session_dir, 0o700)
    dest = session_dir / "compose.yaml"
    dest.write_bytes(compose_file.read_bytes())
    os.chmod(dest, 0o600)
    return {"path": str(dest), "sha256": _sha256_file(dest)}


def _latest_compose_backup(name: str) -> Optional[Path]:
    backups_dir = _compose_backups_dir(name)
    if not backups_dir.is_dir():
        return None
    sessions = sorted((p for p in backups_dir.iterdir() if p.is_dir()), reverse=True)
    for session in sessions:
        candidate = session / "compose.yaml"
        if candidate.is_file():
            return candidate
    return None


def _compose_health(plan: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    """Delegates to lib/omes/py/agent/compose_health.py (issue #96
    follow-up: reuse lib/omes/py/health/hermes.py's provider/channel
    layers, executed through `docker compose exec -T` where they need
    the container's own view, instead of this module's own ad hoc
    container+health-command-only check). Kept as a thin wrapper here so
    every existing call site (`apply`, `health`) is unaffected."""
    return compose_health.run(plan, timeout)


def cmd_list(args: argparse.Namespace) -> int:
    omes_root = _omes_root()
    manifests_dir = paths.agents_config_dir()
    names = set(state_mod.list_agents())
    if manifests_dir.is_dir():
        names.update(p.stem for p in manifests_dir.glob("*.json"))

    results = []
    for name in sorted(names):
        entry: Dict[str, Any] = {"name": name}
        manifest_file = paths.manifest_path(name)
        entry["manifestPresent"] = manifest_file.exists()
        st = state_mod.load(name)
        entry["state"] = st.get("state", "declared")
        if manifest_file.exists():
            try:
                data = manifest_mod.load_and_validate(manifest_file, omes_root, expected_name=name)
                if data.get("apiVersion") == "omes.ahliweb.com/v2":
                    entry["role"] = None
                    entry["serviceMode"] = data.get("placement", {}).get("serviceScope", "user")
                    entry["profileRef"] = data.get("runtime", {}).get("profileRef")
                else:
                    entry["role"] = data["spec"]["role"]
                    entry["serviceMode"] = data["spec"]["serviceMode"]
                    entry["profileRef"] = data["spec"].get("profile")
            except manifest_mod.ManifestError as exc:
                entry["manifestError"] = str(exc)
        results.append(entry)

    if args.json:
        _print({"agents": results}, True)
    else:
        if not results:
            print("no agents declared")
        for r in results:
            print(f"{r['name']}: state={r['state']} manifest={'ok' if r.get('manifestPresent') and not r.get('manifestError') else 'missing/invalid'}")
    return EX_OK


def _doctor_timeout() -> float:
    raw = os.environ.get("OMES_HEALTH_TIMEOUT", "").strip()
    try:
        return float(raw) if raw else 10.0
    except ValueError:
        return 10.0


def _doctor_one(name: str, omes_root: Path, timeout: float) -> Dict[str, Any]:
    """Read-only, bounded-timeout report for a single deployed agent:
    its lifecycle state (from the agent state dir) plus a health summary,
    reusing lib/omes/py/agent/health.py (systemd) or
    lib/omes/py/agent/compose_health.py (compose) - never a second health
    implementation (issue #87 follow-up: "omes doctor integration")."""
    st = state_mod.load(name)
    entry: Dict[str, Any] = {"name": name, "state": st.get("state", "declared")}

    manifest_path = paths.manifest_path(name)
    if not manifest_path.exists():
        entry["ok"] = False
        entry["error"] = f"no manifest found at {manifest_path}"
        return entry

    try:
        manifest = manifest_mod.load_and_validate(manifest_path, omes_root, expected_name=name)
    except manifest_mod.ManifestError as exc:
        entry["ok"] = False
        entry["error"] = str(exc)
        return entry

    backend = _backend(manifest)
    entry["backend"] = backend
    entry["serviceMode"] = _service_mode(manifest)

    try:
        if backend == "compose":
            plan = compose_mod.build_plan(manifest)
            health_result = compose_health.run(plan, timeout)
        else:
            plan = _build_systemd_plan(manifest, omes_root)
            health_result = health_mod.run(plan["unit"]["name"], plan["serviceMode"], plan["hermesHome"], timeout)
    except Exception as exc:  # pragma: no cover - defensive: doctor must never crash the whole report  # nosec B110 - reported below, not silently swallowed
        entry["ok"] = False
        entry["error"] = f"health check failed: {exc}"
        return entry

    entry["health"] = health_result
    entry["ready"] = bool(health_result.get("ready"))
    entry["connected"] = bool(health_result.get("connected"))
    entry["ok"] = entry["ready"]
    return entry


def cmd_doctor(args: argparse.Namespace) -> int:
    """`omes agent doctor` - reports every deployed agent (from the agent
    state dir, `lib/omes/py/agent/paths.py`'s `agents_state_dir()`) with
    its lifecycle state and a read-only, bounded-timeout health summary.
    Never mutates anything. Meant to be invoked from `bin/omes`'s
    `cmd_doctor` (issue #87/#96 follow-up) - not a second doctor
    mechanism; `lib/omes/cmd/agent.sh` exposes it as
    `omes agent doctor` for direct/manual use too."""
    omes_root = _omes_root()
    timeout = _doctor_timeout()
    names = sorted(state_mod.list_agents())

    results = [_doctor_one(name, omes_root, timeout) for name in names]

    overall_ok = all(r.get("ok") for r in results) if results else True
    if args.json:
        _print({"agents": results, "ok": overall_ok}, True)
    else:
        if not results:
            print("no deployed agents found")
        for r in results:
            status = "OK" if r.get("ok") else ("WARN" if "error" not in r else "FAIL")
            detail = r.get("error") or f"state={r.get('state')} ready={r.get('ready')} connected={r.get('connected')}"
            print(f"{r['name']}: {status} {detail}")
    return EX_OK


def cmd_check(args: argparse.Namespace) -> int:
    omes_root = _omes_root()
    try:
        manifest = _load_manifest(args.name, omes_root)
    except manifest_mod.ManifestError as exc:
        _print({"ok": False, "errors": exc.errors}, args.json) if args.json else print(
            f"error: {exc}", file=sys.stderr
        )
        return EX_PREFLIGHT

    service_mode = _service_mode(manifest)
    priv_error = _check_privilege(service_mode)
    if priv_error:
        _print({"ok": False, "errors": [priv_error]}, args.json) if args.json else print(
            f"error: {priv_error}", file=sys.stderr
        )
        return EX_PRIVILEGE

    if manifest.get("apiVersion") == "omes.ahliweb.com/v2":
        profile_ref = manifest.get("runtime", {}).get("profileRef", "")
        prof_error = _check_hermes_profile(profile_ref)
        if prof_error:
            _print({"ok": False, "errors": [prof_error]}, args.json) if args.json else print(
                f"error: {prof_error}", file=sys.stderr
            )
            return EX_PREFLIGHT

    backend = _backend(manifest)
    if backend == "compose":
        preflight = compose_preflight.check()
        if not preflight["ok"]:
            _print({"ok": False, "errors": preflight["errors"]}, args.json) if args.json else print(
                f"error: {'; '.join(preflight['errors'])}", file=sys.stderr
            )
            return EX_PREFLIGHT
    elif not shutil.which("systemctl"):
        msg = "systemctl not found on PATH"
        _print({"ok": False, "errors": [msg]}, args.json) if args.json else print(f"error: {msg}", file=sys.stderr)
        return EX_PREFLIGHT

    result = {
        "ok": True,
        "name": args.name,
        "serviceMode": service_mode,
        "backend": backend,
        "apiVersion": manifest.get("apiVersion", "omes.ahliweb.com/v1"),
    }
    _print(result, args.json) if args.json else print(
        f"{args.name}: check ok (serviceMode={service_mode}, backend={backend})"
    )
    return EX_OK


def cmd_plan(args: argparse.Namespace) -> int:
    omes_root = _omes_root()
    try:
        manifest = _load_manifest(args.name, omes_root)
    except manifest_mod.ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_PREFLIGHT

    priv_error = _check_privilege(_service_mode(manifest))
    if priv_error:
        print(f"error: {priv_error}", file=sys.stderr)
        return EX_PRIVILEGE

    if _backend(manifest) == "compose":
        plan = compose_mod.build_plan(manifest)
        _print(compose_mod.plan_summary(plan), args.json)
        return EX_OK

    plan = _build_systemd_plan(manifest, omes_root)
    _print(plan, args.json)
    return EX_OK


def _run_systemctl(mode: str, args_list: list, timeout: float = 20.0):
    cmd = ["systemctl"] + (["--user"] if mode == "user" else []) + args_list
    try:
        return subprocess.run(  # nosec B603 - fixed argv, no shell
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        class _Fake:
            returncode = -1
            stdout = ""
            stderr = str(exc)

        return _Fake()


def _maybe_backup(hermes_home: Path, dry_run: bool) -> Dict[str, Any]:
    """Reuses lib/omes/py/hermesbackup (issue #82) for the "backup"
    lifecycle step, scoped to the agent's own isolated HERMES_HOME. A
    fresh agent with no prior HERMES_HOME simply has nothing to back
    up - this is not an error."""
    if not hermes_home.exists():
        return {"skipped": "hermes_home does not exist yet (first apply)"}

    py_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["HERMES_HOME"] = str(hermes_home)
    env["PYTHONPATH"] = str(py_root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    cmd = [sys.executable, "-m", "hermesbackup.cli", "create", "--json"]
    if dry_run:
        cmd.append("--dry-run")
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv, no shell
            cmd, capture_output=True, text=True, timeout=60, env=env, check=False
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeError(f"backup step failed to start: {exc}") from exc

    if proc.returncode != 0:
        raise RuntimeError(f"backup step failed (exit {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"raw": proc.stdout.strip()}


def _write_atomic(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _apply_compose(name: str, manifest: Dict[str, Any], args: argparse.Namespace) -> int:
    """`omes agent apply` for `spec.backend: "compose"` (issue #96):
    check -> plan -> backup -> mutate -> verify, using
    `docker compose -p <project> -f <rendered file> up -d` for the
    mutate step. Never grants the container Docker socket access; refuses
    (before any mutation) if the daemon is not rootless."""
    omes_root = _omes_root()
    preflight = compose_preflight.check()
    if not preflight["ok"]:
        _print({"ok": False, "stage": "preflight", "errors": preflight["errors"]}, args.json) if args.json else print(
            f"error: {'; '.join(preflight['errors'])}", file=sys.stderr
        )
        return EX_PREFLIGHT

    if manifest.get("apiVersion") == "omes.ahliweb.com/v2":
        profile_ref = manifest.get("runtime", {}).get("profileRef", "")
        prof_error = _check_hermes_profile(profile_ref)
        if prof_error:
            _print({"ok": False, "stage": "preflight", "errors": [prof_error]}, args.json) if args.json else print(
                f"error: {prof_error}", file=sys.stderr
            )
            return EX_PREFLIGHT

    if shutil.which("docker") is None:
        print("error: docker not found on PATH", file=sys.stderr)
        return EX_PREFLIGHT

    plan = compose_mod.build_plan(manifest)

    if args.dry_run:
        _print({"dryRun": True, **compose_mod.plan_summary(plan)}, args.json)
        return EX_OK

    if not args.yes and not os.environ.get("OMES_NONINTERACTIVE") == "1":
        if sys.stdin.isatty():
            answer = input(f"Apply compose agent deployment '{name}' (project={plan['project']})? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("aborted (not confirmed)", file=sys.stderr)
                return EX_ERROR
        else:
            print("error: this is a mutating operation; pass --yes to confirm", file=sys.stderr)
            return EX_ERROR

    state_mod.advance(name, "preflighted", "rootless docker preflight ok; manifest validated")
    state_mod.advance(name, "planned", "compose plan computed")

    try:
        backup_result = _maybe_backup(Path(plan["hermesHome"]), dry_run=False)
    except RuntimeError as exc:
        state_mod.advance(name, "failed", f"backup step failed: {exc}")
        print(f"error: {exc}", file=sys.stderr)
        return EX_BACKUP

    compose_file = Path(plan["composeFile"])
    compose_file_backup = _backup_previous_compose_file(name, compose_file)
    state_mod.advance(
        name,
        "backed-up",
        json.dumps({"hermesHome": backup_result, "composeFileBackup": compose_file_backup})[:300],
    )

    rendered = compose_mod.render_compose_yaml(plan)
    unchanged = compose_file.is_file() and compose_file.read_text(encoding="utf-8") == rendered

    try:
        for volume in plan["volumes"]:
            Path(volume["hostPath"]).mkdir(parents=True, exist_ok=True)
        Path(plan["hermesHome"]).mkdir(parents=True, exist_ok=True)
        _write_atomic(compose_file, rendered)
    except OSError as exc:
        state_mod.advance(name, "failed", f"failed to write compose file: {exc}")
        print(f"error: failed to write compose file: {exc}", file=sys.stderr)
        return EX_APPLY

    managed_paths = [str(compose_file)]

    up_proc = _run_docker(["compose", "-p", plan["project"], "-f", str(compose_file), "up", "-d"], timeout=120.0)
    if up_proc.returncode != 0:
        state_mod.advance(name, "failed", "docker compose up failed", managed_paths=managed_paths)
        print(f"error: docker compose up failed: {up_proc.stderr}", file=sys.stderr)
        return EX_APPLY

    prov = provenance.collect(omes_root)
    prov["composeImageDigest"] = plan["image"]
    prov["composeFileSha256"] = _sha256_file(compose_file)
    prov["composeReapplyUnchanged"] = unchanged
    state_mod.advance(name, "applied", "docker compose up -d succeeded", managed_paths=managed_paths, provenance=prov)

    health_result = _compose_health(plan, timeout=30.0)
    if not health_result.get("ready"):
        state_mod.advance(name, "failed", "container did not report running/healthy after apply", managed_paths=managed_paths)
        print("error: container did not become healthy after apply", file=sys.stderr)
        return EX_VERIFY

    state_mod.advance(name, "verified", "container running and healthy")
    state_mod.advance(name, "ready", "verification passed")
    final_state = state_mod.advance(name, "healthy", "post-apply health check passed")

    result = {
        "ok": True,
        "name": name,
        "state": final_state["state"],
        "project": plan["project"],
        "composeFile": str(compose_file),
        "health": health_result,
        "apiVersion": manifest.get("apiVersion", "omes.ahliweb.com/v1"),
    }
    _print(result, args.json) if args.json else print(f"{name}: applied (compose), state={final_state['state']}")
    return EX_OK


def cmd_apply(args: argparse.Namespace) -> int:
    omes_root = _omes_root()
    name = args.name

    try:
        manifest = _load_manifest(name, omes_root)
    except manifest_mod.ManifestError as exc:
        _print({"ok": False, "stage": "preflight", "errors": exc.errors}, args.json) if args.json else print(
            f"error: {exc}", file=sys.stderr
        )
        return EX_PREFLIGHT

    service_mode = _service_mode(manifest)
    priv_error = _check_privilege(service_mode)
    if priv_error:
        _print({"ok": False, "stage": "preflight", "errors": [priv_error]}, args.json) if args.json else print(
            f"error: {priv_error}", file=sys.stderr
        )
        return EX_PRIVILEGE

    if manifest.get("apiVersion") == "omes.ahliweb.com/v2":
        profile_ref = manifest.get("runtime", {}).get("profileRef", "")
        prof_error = _check_hermes_profile(profile_ref)
        if prof_error:
            _print({"ok": False, "stage": "preflight", "errors": [prof_error]}, args.json) if args.json else print(
                f"error: {prof_error}", file=sys.stderr
            )
            return EX_PREFLIGHT

    if _backend(manifest) == "compose":
        return _apply_compose(name, manifest, args)

    if not shutil.which("systemctl"):
        print("error: systemctl not found on PATH", file=sys.stderr)
        return EX_PREFLIGHT

    plan = _build_systemd_plan(manifest, omes_root)

    if args.dry_run:
        _print(
            {
                "dryRun": True,
                "unit": plan["unit"],
                "hermesHome": plan["hermesHome"],
                "secretReferences": plan["secretReferences"],
                "resources": plan["resources"],
                "resourceDropinLines": plan["resourceDropinLines"],
                "restartPolicy": plan["restartPolicy"],
                "health": plan["health"],
            },
            args.json,
        )
        return EX_OK

    if not args.yes and not os.environ.get("OMES_NONINTERACTIVE") == "1":
        if sys.stdin.isatty():
            answer = input(f"Apply agent deployment '{name}' (serviceMode={plan['serviceMode']})? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("aborted (not confirmed)", file=sys.stderr)
                return EX_ERROR
        else:
            print("error: this is a mutating operation; pass --yes to confirm", file=sys.stderr)
            return EX_ERROR

    mode = plan["serviceMode"]

    state_mod.advance(name, "preflighted", "manifest validated; privilege ok")
    state_mod.advance(name, "planned", "plan computed")

    try:
        backup_result = _maybe_backup(Path(plan["hermesHome"]), dry_run=False)
    except RuntimeError as exc:
        state_mod.advance(name, "failed", f"backup step failed: {exc}")
        print(f"error: {exc}", file=sys.stderr)
        return EX_BACKUP
    state_mod.advance(name, "backed-up", json.dumps(backup_result)[:200])

    profile_ref = plan.get("profileRef", name)

    # Step: Detect and migrate legacy omes-agent-<name>.service if present
    legacy_unit = plan["unit"].get("legacy_name")
    unit_dir = Path(plan["unit"]["dir"])
    if legacy_unit:
        legacy_path = unit_dir / legacy_unit
        legacy_dropin = unit_dir / f"{legacy_unit}.d"
        act_res = _run_systemctl(mode, ["is-active", legacy_unit])
        en_res = _run_systemctl(mode, ["is-enabled", legacy_unit])
        if legacy_path.exists() or legacy_dropin.exists() or act_res.returncode == 0 or en_res.returncode == 0:
            if act_res.returncode == 0:
                _run_systemctl(mode, ["stop", legacy_unit])
            if en_res.returncode == 0:
                _run_systemctl(mode, ["disable", legacy_unit])
            if legacy_path.is_file() or legacy_path.is_symlink():
                try:
                    legacy_path.unlink()
                except OSError:
                    pass
            if legacy_dropin.is_dir():
                for c in sorted(legacy_dropin.iterdir(), reverse=True):
                    try:
                        c.unlink()
                    except OSError:
                        pass
                try:
                    legacy_dropin.rmdir()
                except OSError:
                    pass
            _run_systemctl(mode, ["daemon-reload"])

    # Step: Install upstream Hermes gateway service (Hermes owns base unit)
    multiplexed = _is_multiplexed_mode(profile_ref, plan["hermesHome"])
    if not multiplexed:
        install_proc = _hermes_gateway_cmd("install", profile_ref, hermes_home=plan["hermesHome"])
        if install_proc.returncode != 0:
            state_mod.advance(name, "failed", f"hermes gateway install failed: {install_proc.stderr}")
            print(f"error: hermes gateway install failed: {install_proc.stderr}", file=sys.stderr)
            return EX_APPLY

    # Step: Apply OMES-owned resource/hardening drop-in overlay only
    dropin_path = Path(plan["unit"]["dropin_path"])
    try:
        dropin_content = unitfile.render_dropin(plan, omes_root)
        dropin_path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(dropin_path, dropin_content)
        Path(plan["hermesHome"]).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        state_mod.advance(name, "failed", f"failed to write drop-in: {exc}")
        print(f"error: failed to write drop-in: {exc}", file=sys.stderr)
        return EX_APPLY

    managed_paths = [str(dropin_path)]

    reload_proc = _run_systemctl(mode, ["daemon-reload"])
    if reload_proc.returncode != 0:
        state_mod.advance(name, "failed", "daemon-reload failed", managed_paths=managed_paths)
        print(f"error: systemctl daemon-reload failed: {reload_proc.stderr}", file=sys.stderr)
        return EX_APPLY

    if not multiplexed:
        start_proc = _hermes_gateway_cmd("start", profile_ref, hermes_home=plan["hermesHome"])
        enable_proc = _run_systemctl(mode, ["enable", "--now", plan["unit"]["name"]])
        if enable_proc.returncode != 0 and start_proc.returncode != 0:
            state_mod.advance(name, "failed", "enable/start failed", managed_paths=managed_paths)
            print(f"error: gateway start failed: {enable_proc.stderr or start_proc.stderr}", file=sys.stderr)
            return EX_APPLY
    else:
        _run_systemctl(mode, ["daemon-reload"])

    prov = provenance.collect(omes_root)
    state_mod.advance(name, "applied", "upstream gateway configured and overlay applied", managed_paths=managed_paths, provenance=prov)

    active_proc = _run_systemctl(mode, ["is-active", plan["unit"]["name"]])
    status_proc = _hermes_gateway_cmd("status", profile_ref, hermes_home=plan["hermesHome"])
    if active_proc.returncode != 0 and status_proc.returncode != 0:
        state_mod.advance(name, "failed", "unit did not become active", managed_paths=managed_paths)
        print("error: unit did not become active after apply", file=sys.stderr)
        return EX_VERIFY

    state_mod.advance(name, "verified", "unit is active")
    state_mod.advance(name, "ready", "verification passed")

    health_result = health_mod.run(plan["unit"]["name"], mode, plan["hermesHome"])
    if health_result.get("ready"):
        final_state = state_mod.advance(name, "healthy", "post-apply health check passed")
    else:
        final_state = state_mod.advance(name, "degraded", "post-apply health check did not pass")

    result = {
        "ok": True,
        "name": name,
        "state": final_state["state"],
        "unit": plan["unit"]["name"],
        "health": health_result,
        "apiVersion": manifest.get("apiVersion", "omes.ahliweb.com/v1"),
    }
    _print(result, args.json) if args.json else print(f"{name}: applied, state={final_state['state']}")
    return EX_OK


def cmd_status(args: argparse.Namespace) -> int:
    omes_root = _omes_root()
    st = state_mod.load(args.name)
    result: Dict[str, Any] = dict(st)
    try:
        manifest = _load_manifest(args.name, omes_root)
        if _backend(manifest) == "compose":
            plan = compose_mod.build_plan(manifest)
            proc = _run_docker(["compose", "-p", plan["project"], "-f", plan["composeFile"], "ps", "--format", "json"])
            result["containerRunning"] = proc.returncode == 0 and "running" in proc.stdout.lower()
            result["project"] = plan["project"]
            result["backend"] = "compose"
        else:
            plan = _build_systemd_plan(manifest, omes_root)
            name = manifest["metadata"]["name"]
            if manifest.get("apiVersion") == "omes.ahliweb.com/v2":
                profile_ref = manifest.get("runtime", {}).get("profileRef", name)
            else:
                profile_ref = manifest.get("spec", {}).get("profile", name)
            proc = _run_systemctl(plan["serviceMode"], ["is-active", plan["unit"]["name"]])
            gw_status = _hermes_gateway_cmd("status", profile_ref, hermes_home=plan["hermesHome"])
            result["unitActive"] = proc.returncode == 0 or (gw_status.returncode == 0 and "running" in gw_status.stdout.lower())
            result["unit"] = plan["unit"]["name"]
            result["backend"] = "systemd"
            if _is_multiplexed_mode(profile_ref, plan["hermesHome"]):
                result["multiplexed"] = True
    except manifest_mod.ManifestError as exc:
        result["manifestError"] = str(exc)

    _print(result, args.json) if args.json else print(f"{args.name}: state={result.get('state')}")
    return EX_OK


def cmd_health(args: argparse.Namespace) -> int:
    omes_root = _omes_root()
    try:
        manifest = _load_manifest(args.name, omes_root)
    except manifest_mod.ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_PREFLIGHT

    if _backend(manifest) == "compose":
        plan = compose_mod.build_plan(manifest)
        result = _compose_health(plan, timeout=30.0)
        _print(result, args.json)
        return EX_OK if result.get("ready") else EX_VERIFY

    plan = _build_systemd_plan(manifest, omes_root)
    result = health_mod.run(plan["unit"]["name"], plan["serviceMode"], plan["hermesHome"])
    _print(result, args.json)
    return EX_OK if result.get("ready") else EX_VERIFY


def cmd_restart(args: argparse.Namespace) -> int:
    omes_root = _omes_root()
    try:
        manifest = _load_manifest(args.name, omes_root)
    except manifest_mod.ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_PREFLIGHT

    priv_error = _check_privilege(_service_mode(manifest))
    if priv_error:
        print(f"error: {priv_error}", file=sys.stderr)
        return EX_PRIVILEGE

    if _backend(manifest) == "compose":
        plan = compose_mod.build_plan(manifest)
        proc = _run_docker(["compose", "-p", plan["project"], "-f", plan["composeFile"], "restart"], timeout=60.0)
        if proc.returncode != 0:
            print(f"error: restart failed: {proc.stderr}", file=sys.stderr)
            return EX_APPLY
        _print({"ok": True, "project": plan["project"]}, args.json) if args.json else print(f"{args.name}: restarted")
        return EX_OK

    plan = _build_systemd_plan(manifest, omes_root)
    name = manifest["metadata"]["name"]
    if manifest.get("apiVersion") == "omes.ahliweb.com/v2":
        profile_ref = manifest.get("runtime", {}).get("profileRef", name)
    else:
        profile_ref = manifest.get("spec", {}).get("profile", name)

    gw_res = _hermes_gateway_cmd("restart", profile_ref, hermes_home=plan["hermesHome"])
    proc = _run_systemctl(plan["serviceMode"], ["restart", plan["unit"]["name"]])
    if proc.returncode != 0 and gw_res.returncode != 0:
        print(f"error: restart failed: {proc.stderr or gw_res.stderr}", file=sys.stderr)
        return EX_APPLY
    _print({"ok": True, "unit": plan["unit"]["name"]}, args.json) if args.json else print(f"{args.name}: restarted")
    return EX_OK


def _rollback_compose(name: str, manifest: Dict[str, Any], args: argparse.Namespace) -> int:
    plan = compose_mod.build_plan(manifest)
    compose_file = Path(plan["composeFile"])

    down_proc = _run_docker(["compose", "-p", plan["project"], "-f", str(compose_file), "down"], timeout=60.0)
    if down_proc.returncode != 0:
        state_mod.advance(name, "failed", "docker compose down failed during rollback")
        print(f"error: docker compose down failed: {down_proc.stderr}", file=sys.stderr)
        return EX_ROLLBACK

    previous = _latest_compose_backup(name)
    restored = False
    if previous is not None:
        try:
            _write_atomic(compose_file, previous.read_text(encoding="utf-8"))
        except OSError as exc:
            state_mod.advance(name, "failed", f"failed to restore previous compose file: {exc}")
            print(f"error: failed to restore previous compose file: {exc}", file=sys.stderr)
            return EX_ROLLBACK
        up_proc = _run_docker(["compose", "-p", plan["project"], "-f", str(compose_file), "up", "-d"], timeout=120.0)
        if up_proc.returncode != 0:
            state_mod.advance(name, "failed", "docker compose up failed while restoring previous version")
            print(f"error: docker compose up (restore) failed: {up_proc.stderr}", file=sys.stderr)
            return EX_ROLLBACK
        restored = True

    final_state = state_mod.advance(
        name, "rolled-back", "compose down; previous file restored" if restored else "compose down; no previous file", managed_paths=[]
    )
    result = {"ok": True, "name": name, "state": final_state["state"], "restoredPreviousVersion": restored}
    _print(result, args.json) if args.json else print(f"{name}: rolled back (compose)")
    return EX_OK


def cmd_rollback(args: argparse.Namespace) -> int:
    omes_root = _omes_root()
    try:
        manifest = _load_manifest(args.name, omes_root)
    except manifest_mod.ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_PREFLIGHT

    priv_error = _check_privilege(_service_mode(manifest))
    if priv_error:
        print(f"error: {priv_error}", file=sys.stderr)
        return EX_PRIVILEGE

    if not args.yes and not os.environ.get("OMES_NONINTERACTIVE") == "1":
        if sys.stdin.isatty():
            answer = input(f"Roll back agent deployment '{args.name}'? This removes only OMES-managed unit/drop-ins/state, never Hermes data. [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("aborted (not confirmed)", file=sys.stderr)
                return EX_ERROR
        else:
            print("error: this is a mutating operation; pass --yes to confirm", file=sys.stderr)
            return EX_ERROR

    if _backend(manifest) == "compose":
        return _rollback_compose(args.name, manifest, args)

    plan = _build_systemd_plan(manifest, omes_root)
    name = manifest["metadata"]["name"]
    if manifest.get("apiVersion") == "omes.ahliweb.com/v2":
        profile_ref = manifest.get("runtime", {}).get("profileRef", name)
    else:
        profile_ref = manifest.get("spec", {}).get("profile", name)
    mode = plan["serviceMode"]
    st = state_mod.load(args.name)
    managed_paths = st.get("managedPaths", [])

    applied_steps = []
    failed_steps = []

    # Step 1: stop unit (Hermes CLI + systemctl)
    stop_proc = _hermes_gateway_cmd("stop", profile_ref, hermes_home=plan["hermesHome"])
    if stop_proc.returncode != 0:
        stop_proc = _run_systemctl(mode, ["stop", plan["unit"]["name"]])
    if stop_proc.returncode != 0:
        act_proc = _run_systemctl(mode, ["is-active", plan["unit"]["name"]])
        if act_proc.returncode == 0 or stop_proc.returncode == 1:
            failed_steps.append({"step": "systemctl_stop", "error": f"systemctl stop failed: exit {stop_proc.returncode}"})
        else:
            applied_steps.append({"step": "systemctl_stop", "detail": "already inactive"})
    else:
        applied_steps.append({"step": "systemctl_stop", "detail": "stopped"})

    # Step 2: disable unit
    disable_proc = _run_systemctl(mode, ["disable", plan["unit"]["name"]])
    if disable_proc.returncode != 0:
        en_proc = _run_systemctl(mode, ["is-enabled", plan["unit"]["name"]])
        if en_proc.returncode == 0:
            failed_steps.append({"step": "systemctl_disable", "error": f"systemctl disable failed: exit {disable_proc.returncode}"})
        else:
            applied_steps.append({"step": "systemctl_disable", "detail": "already disabled"})
    else:
        applied_steps.append({"step": "systemctl_disable", "detail": "disabled"})

    # Step 3: remove managed paths
    removed = []
    remaining_managed = []
    for p in managed_paths:
        path = Path(p)
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
                removed.append(str(path))
                applied_steps.append({"step": "unlink_file", "path": str(path)})
            elif path.is_dir():
                path.rmdir()
                removed.append(str(path))
                applied_steps.append({"step": "rmdir", "path": str(path)})
        except OSError as exc:
            if path.exists():
                failed_steps.append({"step": "remove_path", "path": str(path), "error": str(exc)})
                remaining_managed.append(str(path))

    try:
        dropin_dir = Path(plan["unit"]["dropin_dir"])
        if dropin_dir.is_dir() and not any(dropin_dir.iterdir()):
            dropin_dir.rmdir()
            applied_steps.append({"step": "rmdir", "path": str(dropin_dir)})
    except OSError as exc:
        if dropin_dir.is_dir():
            failed_steps.append({"step": "rmdir", "path": str(dropin_dir), "error": str(exc)})

    # Step 4: daemon-reload
    reload_proc = _run_systemctl(mode, ["daemon-reload"])
    if reload_proc.returncode != 0:
        failed_steps.append({"step": "daemon_reload", "error": f"daemon-reload failed: exit {reload_proc.returncode}"})
    else:
        applied_steps.append({"step": "daemon_reload", "detail": "reloaded"})

    # Step 5: Post-operation read-back verification
    is_active_proc = _run_systemctl(mode, ["is-active", plan["unit"]["name"]])
    unit_active = (is_active_proc.returncode == 0)
    is_enabled_proc = _run_systemctl(mode, ["is-enabled", plan["unit"]["name"]])
    unit_enabled = (is_enabled_proc.returncode == 0)
    unremoved_files = [p for p in managed_paths if Path(p).exists()]

    observed_state = {
        "unit_active": unit_active,
        "unit_enabled": unit_enabled,
        "unremoved_files": unremoved_files,
    }

    reasons = [f"{s['step']}: {s.get('error', s.get('path', ''))}" for s in failed_steps]
    if unit_active:
        reasons.append(f"unit {plan['unit']['name']} is still active")
    if unit_enabled:
        reasons.append(f"unit {plan['unit']['name']} is still enabled")
    if unremoved_files:
        reasons.append(f"managed files still present: {unremoved_files}")

    verification_passed = (len(reasons) == 0)

    if not verification_passed:
        error_msg = "; ".join(reasons) or "rollback verification failed"
        state_mod.advance(
            args.name,
            "failed",
            f"rollback failed: {error_msg}",
            managed_paths=remaining_managed or managed_paths,
        )
        result = {
            "ok": False,
            "name": args.name,
            "action": "rollback",
            "backend": "systemd",
            "state": "failed",
            "error": error_msg,
            "removed": removed,
            "applied_steps": applied_steps,
            "failed_steps": failed_steps,
            "observed_state": observed_state,
            "verification": {"passed": False, "reasons": reasons},
        }
        _print(result, args.json) if args.json else print(f"error: rollback failed: {error_msg}", file=sys.stderr)
        return EX_ROLLBACK

    final_state = state_mod.advance(args.name, "rolled-back", "rollback removed OMES-managed unit/drop-in", managed_paths=[])
    result = {
        "ok": True,
        "name": args.name,
        "action": "rollback",
        "backend": "systemd",
        "state": final_state["state"],
        "removed": removed,
        "applied_steps": applied_steps,
        "failed_steps": [],
        "observed_state": observed_state,
        "verification": {"passed": True},
    }
    _print(result, args.json) if args.json else print(f"{args.name}: rolled back")
    return EX_OK


def cmd_remove(args: argparse.Namespace) -> int:
    """`omes agent remove <name>` - compose backend only (issue #96).
    `docker compose down --volumes`, scoped to this agent's own project,
    then removes only the OMES-managed compose dir and state directory -
    never HERMES_HOME, never another agent's resources."""
    omes_root = _omes_root()
    try:
        manifest = _load_manifest(args.name, omes_root)
    except manifest_mod.ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_PREFLIGHT

    if _backend(manifest) != "compose":
        print("error: 'omes agent remove' is only implemented for backend=compose (see docs/agent-deployment.md)", file=sys.stderr)
        return EX_USAGE

    priv_error = _check_privilege(_service_mode(manifest))
    if priv_error:
        print(f"error: {priv_error}", file=sys.stderr)
        return EX_PRIVILEGE

    if not args.yes and not os.environ.get("OMES_NONINTERACTIVE") == "1":
        if sys.stdin.isatty():
            answer = input(f"Remove compose agent deployment '{args.name}'? This tears down its containers and OMES-managed volumes (never HERMES_HOME). [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("aborted (not confirmed)", file=sys.stderr)
                return EX_ERROR
        else:
            print("error: this is a mutating operation; pass --yes to confirm", file=sys.stderr)
            return EX_ERROR

    plan = compose_mod.build_plan(manifest)
    compose_file = Path(plan["composeFile"])

    if compose_file.is_file():
        down_proc = _run_docker(
            ["compose", "-p", plan["project"], "-f", str(compose_file), "down", "--volumes"], timeout=60.0
        )
        if down_proc.returncode != 0:
            state_mod.advance(args.name, "failed", "docker compose down --volumes failed during remove")
            print(f"error: docker compose down --volumes failed: {down_proc.stderr}", file=sys.stderr)
            return EX_ROLLBACK

    removed = []
    compose_dir = Path(plan["composeDir"])
    if compose_dir.is_dir():
        for child in sorted(compose_dir.glob("**/*"), reverse=True):
            try:
                if child.is_file() or child.is_symlink():
                    child.unlink()
                else:
                    child.rmdir()
            except OSError:
                pass
        try:
            compose_dir.rmdir()
            removed.append(str(compose_dir))
        except OSError:
            pass

    final_state = state_mod.advance(args.name, "rolled-back", "removed (compose down --volumes; OMES-managed paths deleted)", managed_paths=[])
    state_mod.remove(args.name)
    result = {"ok": True, "name": args.name, "state": final_state["state"], "removed": removed}
    _print(result, args.json) if args.json else print(f"{args.name}: removed")
    return EX_OK


def cmd_migrate(args: argparse.Namespace) -> int:
    omes_root = _omes_root()
    name = args.name
    try:
        manifest = _load_manifest(name, omes_root)
    except manifest_mod.ManifestError as exc:
        _print({"ok": False, "errors": exc.errors}, args.json) if args.json else print(
            f"error: {exc}", file=sys.stderr
        )
        return EX_PREFLIGHT

    if manifest.get("apiVersion") == "omes.ahliweb.com/v2":
        msg = f"manifest for agent '{name}' is already at apiVersion 'omes.ahliweb.com/v2'"
        _print({"ok": True, "message": msg, "manifest": manifest}, args.json) if args.json else print(msg)
        return EX_OK

    try:
        v2_manifest, audit_records = migration_mod.migrate_manifest_v1_to_v2(manifest)
    except migration_mod.MigrationError as exc:
        _print({"ok": False, "error": str(exc)}, args.json) if args.json else print(
            f"error: migration failed: {exc}", file=sys.stderr
        )
        return EX_ERROR

    # Validate generated v2 manifest against schema & semantic rules
    v2_errors = manifest_mod.validate(v2_manifest, omes_root)
    if v2_errors:
        _print({"ok": False, "error": "migrated manifest failed v2 validation", "errors": v2_errors}, args.json) if args.json else print(
            f"error: migrated manifest failed v2 validation: {'; '.join(v2_errors)}", file=sys.stderr
        )
        return EX_ERROR

    written_to = None
    backup_file = None
    if not args.dry_run:
        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(v2_manifest, indent=2) + "\n", encoding="utf-8")
            written_to = str(out_path)
        else:
            manifest_path = paths.manifest_path(name)
            backup_path = manifest_path.with_suffix(".json.bak")
            backup_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            manifest_path.write_text(json.dumps(v2_manifest, indent=2) + "\n", encoding="utf-8")
            written_to = str(manifest_path)
            backup_file = str(backup_path)

    result = {
        "ok": True,
        "name": name,
        "migrated": True,
        "dry_run": args.dry_run,
        "dryRun": args.dry_run,
        "migrated_manifest": v2_manifest,
        "v2Manifest": v2_manifest,
        "audit": audit_records,
    }
    if written_to:
        result["writtenTo"] = written_to
    if backup_file:
        result["backup"] = backup_file

    if args.json:
        _print(result, True)
    else:
        print(f"Migration preview for agent '{name}':")
        print(json.dumps(v2_manifest, indent=2))
        print("\nAudit classification:")
        for r in audit_records:
            print(f"  {r['v1_field']:<25} -> {r['action']:<10} -> {r['target']}")
    return EX_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omes agent")
    sub = parser.add_subparsers(dest="subcommand")

    p_list = sub.add_parser("list")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_doctor = sub.add_parser("doctor")
    p_doctor.add_argument("--json", action="store_true")
    p_doctor.set_defaults(func=cmd_doctor)

    for name, func in (("check", cmd_check), ("plan", cmd_plan), ("status", cmd_status), ("health", cmd_health), ("restart", cmd_restart)):
        p = sub.add_parser(name)
        p.add_argument("agent_name", metavar="name")
        p.add_argument("--json", action="store_true")
        p.set_defaults(func=func, name_attr="agent_name")

    p_apply = sub.add_parser("apply")
    p_apply.add_argument("agent_name", metavar="name")
    p_apply.add_argument("--dry-run", action="store_true")
    p_apply.add_argument("--yes", action="store_true")
    p_apply.add_argument("--json", action="store_true")
    p_apply.set_defaults(func=cmd_apply)

    p_rollback = sub.add_parser("rollback")
    p_rollback.add_argument("agent_name", metavar="name")
    p_rollback.add_argument("--yes", action="store_true")
    p_rollback.add_argument("--json", action="store_true")
    p_rollback.set_defaults(func=cmd_rollback)

    p_remove = sub.add_parser("remove")
    p_remove.add_argument("agent_name", metavar="name")
    p_remove.add_argument("--yes", action="store_true")
    p_remove.add_argument("--json", action="store_true")
    p_remove.set_defaults(func=cmd_remove)

    p_migrate = sub.add_parser("migrate")
    p_migrate.add_argument("agent_name", metavar="name")
    p_migrate.add_argument("--dry-run", action="store_true")
    p_migrate.add_argument("--output", metavar="path", help="Path to write migrated v2 manifest")
    p_migrate.add_argument("--json", action="store_true")
    p_migrate.set_defaults(func=cmd_migrate, name_attr="agent_name")

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "subcommand", None):
        parser.print_usage(sys.stderr)
        return EX_USAGE
    if hasattr(args, "agent_name"):
        args.name = args.agent_name
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
