#!/usr/bin/env bash
set -euo pipefail

WHEEL="$1"
CHECK_DEPS="$2"

# Fail fast with copy-pasteable fixes if pipx/systemctl aren't available.
"$CHECK_DEPS"

# pipx installs into its own isolated virtualenv, never the system Python.
# `lws` (and, once M15's shims exist, `lreq`/`lvx`/`lrun`/`little-pypi`/
# `bootstrap-little-project` forwarders) is the console script this
# exposes; Core itself launches pillars from source via systemd-run, not
# through this pipx install — see `:core_server`/`:orchestration_server`.
pipx install --force "$WHEEL"
