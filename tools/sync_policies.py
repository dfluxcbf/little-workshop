#!/usr/bin/env python3
"""Bring Policy & Compliance's live policies in line with tools/policy_scripts/.

Each entry of tools/policy_scripts/policies.json is one policy: its EARS rule, scope, gate
points and the script beside the manifest. Without --apply this only prints what differs
from the live pillar (`lws policy list`). With --apply it replaces each differing policy in
place (`lws policy activate --policy-id`, which keeps the policy's project selection) and
creates a missing one, selecting the projects its entry names (`lws policy projects set`).

Applying is the Gate 4 approval: the person running it has reviewed these files, as in any
change to them. --without-gate-point leaves a gate point out, for a pillar that predates it
(e.g. `commit` before the pillar carrying it is deployed).

    python3 tools/sync_policies.py                 # what would change
    python3 tools/sync_policies.py --apply         # make it so
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent / "policy_scripts"
MANIFEST = SCRIPTS / "policies.json"


def load_manifest(manifest: Path = MANIFEST) -> list[dict]:
    """The manifest's entries, each with its script's text under `test_script`."""
    entries = json.loads(manifest.read_text(encoding="utf-8"))["policies"]
    for entry in entries:
        entry["test_script"] = (manifest.parent / entry["script"]).read_text(encoding="utf-8")
    return entries


def lws(*args: str) -> dict:
    result = subprocess.run(["lws", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"lws {' '.join(args[:3])} failed: {result.stderr.strip() or result.stdout.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def live_policies() -> dict[str, dict]:
    return {p["topic"]: p for p in lws("policy", "list")["policies"]}


def wanted_gate_points(entry: dict, without: set[str]) -> list[str]:
    return [g for g in entry["gate_points"] if g not in without]


def changes(entry: dict, live: dict | None, without: set[str]) -> list[str]:
    """What differs between a manifest entry and its live policy (empty: in sync)."""
    if live is None:
        return ["missing"]
    found = []
    if entry["ears_text"] != live["ears_text"]:
        found.append("ears_text")
    if entry["test_script"] != live["test_script"]:
        found.append("test_script")
    if sorted(wanted_gate_points(entry, without)) != sorted(live["gate_points"]):
        found.append(f"gate_points {sorted(live['gate_points'])} -> {sorted(wanted_gate_points(entry, without))}")
    for key in ("target_pillar", "scope_level", "scope_target", "created_by"):
        if entry[key] != live[key]:
            found.append(f"{key} {live[key]!r} -> {entry[key]!r}")
    return found


def activate(entry: dict, live: dict | None, without: set[str]) -> dict:
    draft = {key: entry[key] for key in (
        "ears_text", "test_script", "topic", "target_pillar", "scope_level", "scope_target", "created_by",
    )}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(draft, fh)
        path = fh.name
    try:
        args = ["policy", "activate", path, "--gate-points", ",".join(wanted_gate_points(entry, without))]
        if live is not None:
            args += ["--policy-id", live["id"]]
        result = lws(*args)
    finally:
        Path(path).unlink(missing_ok=True)
    policy = result.get("result") or {}
    if live is None and entry.get("projects") and policy.get("id"):
        selection: list[str] = []
        for project in entry["projects"]:
            flag = "--project" if project.get("gating_enabled", True) else "--project-no-gating"
            selection += [flag, project["project_id"]]
        lws("policy", "projects", "set", policy["id"], *selection)
    return policy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="Replace what differs in the live pillar")
    parser.add_argument("--topic", action="append", default=[], help="Only these topics (repeatable)")
    parser.add_argument("--without-gate-point", action="append", default=[],
                        help="Leave this gate point out (repeatable), for a pillar that predates it")
    args = parser.parse_args(argv)
    without = set(args.without_gate_point)

    entries = [e for e in load_manifest() if not args.topic or e["topic"] in args.topic]
    live = live_policies()
    pending = [(e, live.get(e["topic"]), changes(e, live.get(e["topic"]), without)) for e in entries]
    pending = [p for p in pending if p[2]]
    for entry, _, found in pending:
        print(f"{entry['topic']}: {', '.join(found)}")
    unmanaged = sorted(set(live) - {e["topic"] for e in load_manifest()})
    if unmanaged:
        print(f"live but not in the manifest (left alone): {', '.join(unmanaged)}")
    if not pending:
        print("every policy is in sync")
        return 0
    if not args.apply:
        print(f"{len(pending)} to change; run with --apply to replace them in the pillar")
        return 0
    failed = 0
    for entry, current, _ in pending:
        try:
            policy = activate(entry, current, without)
            print(f"applied {entry['topic']} ({policy.get('id', '?')})")
        except RuntimeError as exc:
            failed += 1
            print(f"FAILED {entry['topic']}: {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
