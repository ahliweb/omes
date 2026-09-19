"""lib/omes/py/agent/unitfile.py - renders the OMES-managed systemd unit
file and resource/hardening drop-in content for one agent deployment
(issue #87). Pure string rendering, no I/O - lib/omes/py/agent/cli.py
writes the files.
"""
from __future__ import annotations

from typing import Any, Dict

from . import hardening_bridge


def render_unit(plan: Dict[str, Any], hermes_binary: str = "hermes") -> str:
    """Renders the `[Unit]`/`[Service]`/`[Install]` unit file. The
    ExecStart runs Hermes's own gateway entrypoint (OMES never
    reimplements the agent runtime - AGENTS.md section 2) with
    HERMES_HOME pointed at this agent's isolated directory. Secret
    material is referenced only via `EnvironmentFile=-<path>` (the
    leading `-` makes a missing file non-fatal rather than assuming a
    default), never inlined."""
    lines = [
        "[Unit]",
        f"Description=OMES-managed agent deployment: {plan['agent']} ({plan['role']}, profile={plan['profile']})",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"Environment=HERMES_HOME={plan['hermesHome']}",
    ]
    if plan.get("environmentFileReference"):
        lines.append(f"EnvironmentFile=-{plan['environmentFileReference']}")
    lines += [
        f"ExecStart={hermes_binary} gateway start --home {plan['hermesHome']}",
        f"WorkingDirectory={plan['hermesHome']}",
    ]
    lines += [
        "",
        "[Install]",
        "WantedBy=default.target" if plan["serviceMode"] == "user" else "WantedBy=multi-user.target",
        "",
    ]
    return "\n".join(lines)


def render_dropin(plan: Dict[str, Any], omes_root, profile: str = None) -> str:
    """Renders the resource-limit + (reused) hardening drop-in body: a
    single `[Service]` section combining modules/hermes-gateway/
    hardening.sh's reused security directives (via hardening_bridge, for
    any profile other than "off") with this agent's own numeric resource
    limits computed in plan.py."""
    resolved_profile = profile if profile is not None else hardening_bridge.agent_hardening_profile()
    hardening_body = hardening_bridge.render(resolved_profile, plan["hermesHome"], omes_root)

    # This agent's own numeric resource/restart lines (from the manifest)
    # take precedence over hardening_render's generic defaults for the
    # same directive key - systemd itself would apply "last wins" for a
    # duplicate key in one section, but de-duplicating explicitly here
    # keeps the rendered file readable and avoids relying on that systemd
    # behavior.
    resource_lines = list(plan["resourceDropinLines"])
    resource_keys = {line.split("=", 1)[0] for line in resource_lines}

    body_lines = []
    for line in hardening_body.splitlines():
        stripped = line.strip()
        if not stripped or stripped == "[Service]":
            continue
        key = stripped.split("=", 1)[0]
        if key in resource_keys:
            continue
        body_lines.append(line)

    content_lines = ["[Service]"] + body_lines + resource_lines
    return "\n".join(content_lines) + "\n"
