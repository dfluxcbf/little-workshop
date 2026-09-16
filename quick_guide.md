# little-workshop — quick guide

A practical, get-it-running guide. For the full design/architecture story see
`docs/design.md`, `docs/architecture.md`, and `docs/pillars/*.md`; for what's
built vs. planned see `docs/milestones.md` and `docs/requirements.md`.

## What this is

little-workshop is a self-hosted daemon that governs a project's whole
lifecycle — requirements, versioning, deployment, builds, policy compliance —
through ten independent "pillars," each its own process talking over a Unix
domain socket. `lws` is the CLI; a Dashboard and an MCP server sit on top as
thin, stateless proxies for a human's browser or an agent like Claude.

## Install

```bash
bazel run //:install
```

This builds the wheel, runs `tools/check_system_deps.sh`, and `pipx`-installs
it, giving you a global `lws` command. Re-run this any time `src/` changes —
the previous install goes stale silently otherwise (see `docs/open_questions.md`
history: this bit a real self-hosting attempt once already).

## Running it for real (self-hosting)

Nothing auto-launches on install. Core needs a real `manifest.toml` naming
each pillar's launch command — write one once:

```python
from pathlib import Path
from common.manifest import default_manifest, write_manifest
from common.paths import Paths

paths = Paths.from_env()
venv_python = str(Path.home() / ".local/share/pipx/venvs/little-workshop/bin/python3")
manifest = default_manifest()
for pillar in ("registry", "orchestration", "requirements", "policy",
               "agent", "deployment", "version", "build", "dashboard"):
    manifest.pillars[pillar].command = [venv_python, "-m", pillar]
write_manifest(paths.manifest_path, manifest)
```

Then start Core — it launches every enabled pillar itself:

```bash
systemd-run --user --unit=little-workshop-core "$HOME/.local/share/pipx/venvs/little-workshop/bin/python3" -m core
```

Check status:

```bash
lws core status
```

Stop everything:

```bash
systemctl --user stop 'little-workshop-*'
```

State persists in `~/.config/little-workshop` (config) and
`~/.local/state/little-workshop` (databases, sockets, logs) regardless of
whether the daemons are currently running.

## Everyday commands

```bash
lws project bootstrap --name my-project --dest-dir ~/code       # new project
lws project import --name existing-thing --path ~/code/existing-thing
lws project list

lws job list --status failed
lws job status <job-id>

lws version feature-start --project-id my-project --name widgets
lws version release-start --project-id my-project

lws deployment deploy --project-id my-project --branch master
lws deployment status --project-id my-project

lws requirements list --project-id my-project
lws requirements report --project-id my-project

lws build run --project-id my-project --commitish master --command "pytest"
lws build publish-pypi ...

lws policy list
lws policy author --ears-text "..." --topic ... --target-pillar ... \
    --scope-level all_projects --created-by user
lws policy activate <draft.json>          # Gate 4 — requires --approved
lws policy configure <policy-id> --disable
lws policy waiver grant --policy-id <id> --project-id <id> --reason "..."
```

Every command is `lws <noun> <verb> [flags]`; `lws <noun> --help` lists them.
Everything mutating goes through Orchestration as a Job (`lws job list`
shows the audit trail); pure reads fall back to a direct database read when
the daemon is down.

## The Dashboard

A browser-facing panel view of the same state, bound to a Unix socket like
every other pillar (no network port — put a reverse proxy or `tailscale
serve` in front of it for real remote access, same model `little-runner`
already used). Panels today: pillar Overview, Projects, Jobs, and a full
Policy management screen (enable/disable, gate-point multi-select, waiver
grant/revoke, read-only script view). Reach it locally with:

```bash
curl --unix-socket ~/.local/state/little-workshop/sockets/dashboard.sock http://x/
```

## Policies, in one paragraph

A policy is EARS-worded text plus a generated pytest script, authored
through a four-gate pipeline (`policy author` → review → `policy activate
--approved`). Framework policies (`created_by: framework`) always win over
project policies and can't be silently shadowed — but a project that
genuinely can't comply gets an explicit, reasoned, revocable **waiver**
instead of a policy edit. Each policy optionally **gates** one or more
action categories (`build_install`, `server_launch`, `deploy_redeploy`,
`self_redeploy`, `server_stop`, `release`, `package_publish`,
`branch_finish`, `project_removal`, `project_registration`) — a failing
check on a gated action holds the Job at `pending_confirmation` until a
human confirms it or a 10-minute watchdog fails it. See
`docs/pillars/policy_compliance.md` for the full story.

## MCP (for Claude or another agent)

```bash
lws mcp serve
```

registers a stdio MCP server exposing every pillar's mutations/reads as
`workshop_*` tools, namespaced by pillar. Point `claude mcp add` at it per
`docs/policies/mcp_policy.md`.

## Testing

```bash
bazel test //:test        # full suite: unit + real systemd --user integration
bazel test //:coverage    # same, gated at 90% line coverage
```

Run directly for trustworthy numbers (`bazel test`'s own coverage percentage
has a known reporting quirk — see `scripts/bazel/run_pytest_coverage.sh`):

```bash
PYTHONPATH=src python3 -m pytest tests/unit -q
PYTHONPATH=src python3 -m pytest tests/integration -q   # needs systemd --user + docker
```

Integration tests spin up real `systemd --user` units and a real Docker
sandbox for policy checks — they skip cleanly (not fail) when either isn't
available.

## Where to look next

| Question | Document |
|---|---|
| What's the overall design? | `docs/design.md` |
| What does each pillar actually do? | `docs/pillars/<name>.md` |
| What's built vs. still planned? | `docs/milestones.md` |
| What was actually implemented, requirement by requirement? | `docs/requirements.md` |
| What's genuinely unresolved right now? | `docs/open_questions.md` |
| Why does this project not depend on the tools it's absorbing? | `docs/governance_policy.md` |
