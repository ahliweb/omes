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

from . import health as health_mod
from . import manifest as manifest_mod
from . import paths, plan as plan_mod, provenance, state as state_mod, unitfile

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
                entry["role"] = data["spec"]["role"]
                entry["serviceMode"] = data["spec"]["serviceMode"]
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


def cmd_check(args: argparse.Namespace) -> int:
    omes_root = _omes_root()
    try:
        manifest = _load_manifest(args.name, omes_root)
    except manifest_mod.ManifestError as exc:
        _print({"ok": False, "errors": exc.errors}, args.json) if args.json else print(
            f"error: {exc}", file=sys.stderr
        )
        return EX_PREFLIGHT

    priv_error = _check_privilege(manifest["spec"]["serviceMode"])
    if priv_error:
        _print({"ok": False, "errors": [priv_error]}, args.json) if args.json else print(
            f"error: {priv_error}", file=sys.stderr
        )
        return EX_PRIVILEGE

    if not shutil.which("systemctl"):
        msg = "systemctl not found on PATH"
        _print({"ok": False, "errors": [msg]}, args.json) if args.json else print(f"error: {msg}", file=sys.stderr)
        return EX_PREFLIGHT

    result = {"ok": True, "name": args.name, "serviceMode": manifest["spec"]["serviceMode"]}
    _print(result, args.json) if args.json else print(f"{args.name}: check ok (serviceMode={manifest['spec']['serviceMode']})")
    return EX_OK


def cmd_plan(args: argparse.Namespace) -> int:
    omes_root = _omes_root()
    try:
        manifest = _load_manifest(args.name, omes_root)
    except manifest_mod.ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_PREFLIGHT

    priv_error = _check_privilege(manifest["spec"]["serviceMode"])
    if priv_error:
        print(f"error: {priv_error}", file=sys.stderr)
        return EX_PRIVILEGE

    plan = plan_mod.build_plan(manifest)
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

    priv_error = _check_privilege(manifest["spec"]["serviceMode"])
    if priv_error:
        _print({"ok": False, "stage": "preflight", "errors": [priv_error]}, args.json) if args.json else print(
            f"error: {priv_error}", file=sys.stderr
        )
        return EX_PRIVILEGE

    if not shutil.which("systemctl"):
        print("error: systemctl not found on PATH", file=sys.stderr)
        return EX_PREFLIGHT

    plan = plan_mod.build_plan(manifest)

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

    unit_path = Path(plan["unit"]["path"])
    dropin_path = Path(plan["unit"]["dropin_path"])

    try:
        unit_content = unitfile.render_unit(plan)
        dropin_content = unitfile.render_dropin(plan, omes_root)
        _write_atomic(unit_path, unit_content)
        _write_atomic(dropin_path, dropin_content)
        Path(plan["hermesHome"]).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        state_mod.advance(name, "failed", f"failed to write unit/drop-in: {exc}")
        print(f"error: failed to write unit/drop-in: {exc}", file=sys.stderr)
        return EX_APPLY

    managed_paths = [str(unit_path), str(dropin_path)]

    reload_proc = _run_systemctl(mode, ["daemon-reload"])
    if reload_proc.returncode != 0:
        state_mod.advance(name, "failed", "daemon-reload failed", managed_paths=managed_paths)
        print(f"error: systemctl daemon-reload failed: {reload_proc.stderr}", file=sys.stderr)
        return EX_APPLY

    enable_proc = _run_systemctl(mode, ["enable", "--now", plan["unit"]["name"]])
    if enable_proc.returncode != 0:
        state_mod.advance(name, "failed", "enable failed", managed_paths=managed_paths)
        print(f"error: systemctl enable --now failed: {enable_proc.stderr}", file=sys.stderr)
        return EX_APPLY

    prov = provenance.collect(omes_root)
    state_mod.advance(name, "applied", "unit enabled and started", managed_paths=managed_paths, provenance=prov)

    active_proc = _run_systemctl(mode, ["is-active", plan["unit"]["name"]])
    if active_proc.returncode != 0:
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

    result = {"ok": True, "name": name, "state": final_state["state"], "unit": plan["unit"]["name"], "health": health_result}
    _print(result, args.json) if args.json else print(f"{name}: applied, state={final_state['state']}")
    return EX_OK


def cmd_status(args: argparse.Namespace) -> int:
    omes_root = _omes_root()
    st = state_mod.load(args.name)
    result: Dict[str, Any] = dict(st)
    try:
        manifest = _load_manifest(args.name, omes_root)
        plan = plan_mod.build_plan(manifest)
        proc = _run_systemctl(plan["serviceMode"], ["is-active", plan["unit"]["name"]])
        result["unitActive"] = proc.returncode == 0
        result["unit"] = plan["unit"]["name"]
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

    plan = plan_mod.build_plan(manifest)
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

    priv_error = _check_privilege(manifest["spec"]["serviceMode"])
    if priv_error:
        print(f"error: {priv_error}", file=sys.stderr)
        return EX_PRIVILEGE

    plan = plan_mod.build_plan(manifest)
    proc = _run_systemctl(plan["serviceMode"], ["restart", plan["unit"]["name"]])
    if proc.returncode != 0:
        print(f"error: restart failed: {proc.stderr}", file=sys.stderr)
        return EX_APPLY
    _print({"ok": True, "unit": plan["unit"]["name"]}, args.json) if args.json else print(f"{args.name}: restarted")
    return EX_OK


def cmd_rollback(args: argparse.Namespace) -> int:
    omes_root = _omes_root()
    try:
        manifest = _load_manifest(args.name, omes_root)
    except manifest_mod.ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_PREFLIGHT

    priv_error = _check_privilege(manifest["spec"]["serviceMode"])
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

    plan = plan_mod.build_plan(manifest)
    mode = plan["serviceMode"]
    st = state_mod.load(args.name)
    managed_paths = st.get("managedPaths", [])

    _run_systemctl(mode, ["stop", plan["unit"]["name"]])
    _run_systemctl(mode, ["disable", plan["unit"]["name"]])

    removed = []
    for p in managed_paths:
        path = Path(p)
        try:
            if path.is_file():
                path.unlink()
                removed.append(str(path))
        except OSError:
            pass
    try:
        dropin_dir = Path(plan["unit"]["dropin_dir"])
        if dropin_dir.is_dir() and not any(dropin_dir.iterdir()):
            dropin_dir.rmdir()
    except OSError:
        pass

    _run_systemctl(mode, ["daemon-reload"])

    final_state = state_mod.advance(args.name, "rolled-back", "rollback removed OMES-managed unit/drop-in", managed_paths=[])
    result = {"ok": True, "name": args.name, "state": final_state["state"], "removed": removed}
    _print(result, args.json) if args.json else print(f"{args.name}: rolled back")
    return EX_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omes agent")
    sub = parser.add_subparsers(dest="subcommand")

    p_list = sub.add_parser("list")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

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
