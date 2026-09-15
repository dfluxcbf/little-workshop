#!/usr/bin/env bash
set -euo pipefail

WHEEL="$1"
CHECK_DEPS="$2"
SERVER_NAME="little-workshop"

# Fail fast with copy-pasteable fixes if pipx/systemctl aren't available.
"$CHECK_DEPS"

# pipx installs into its own isolated virtualenv, never the system Python.
# `lws` (and, once M15's shims exist, `lreq`/`lvx`/`lrun`/`little-pypi`/
# `bootstrap-little-project` forwarders) is the console script this
# exposes; Core itself launches pillars from source via systemd-run, not
# through this pipx install — see `:core_server`/`:orchestration_server`.
pipx install --force "$WHEEL"

# Register the MCP Server pillar per docs/policies/mcp_policy.md: user
# scope always (never local/project scope, since this server proxies the
# same daemon regardless of which project's session attaches it), removal
# before re-registration with no forced scope (so a stale entry can't
# shadow the new one), and — if `claude` isn't on PATH — print the
# equivalent manual command instead of failing the whole install.
if command -v claude >/dev/null 2>&1; then
  claude mcp remove "$SERVER_NAME" >/dev/null 2>&1 || true
  claude mcp add "$SERVER_NAME" -s user -- lws mcp serve
else
  echo "NOTE: 'claude' CLI not found on PATH; skipping MCP registration."
  echo "Run this once 'claude' is available:"
  echo "  claude mcp add $SERVER_NAME -s user -- lws mcp serve"
fi
