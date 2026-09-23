#!/usr/bin/env bash
# Preflight system-dependency check, per little-projects/docs/policies/build_policy.md
# ("Missing system dependencies"): fail fast, report everything checkable in one
# pass, give a copy-pasteable install command per dependency.
#
# TIERS below are checked in order. Each tier is a list of "command|install hint"
# pairs (one per line). If a tier has any missing commands, ALL of them are
# reported together and the script stops there — later tiers are not checked,
# since they may depend on this tier being satisfied first (e.g. don't bother
# checking for a tool installed via npm before checking for node/npm itself).
#
# Add this project's own required-but-unisolatable tools below, e.g.:
#   $'node|Install with: sudo apt install -y nodejs npm'
set -euo pipefail

TIERS=(
  $'pipx|Install with: sudo apt install -y pipx && pipx ensurepath'
  $'systemctl|systemd --user is required to run pillars; install/enable systemd-user-session support for this OS'
  $'docker|Required for Policy & Compliance (M12): every policy check runs sandboxed in a real Docker container. Install with: https://docs.docker.com/engine/install/'
)

for tier in "${TIERS[@]}"; do
  missing=()
  while IFS='|' read -r cmd hint; do
    [[ -z "$cmd" ]] && continue
    if ! command -v "$cmd" >/dev/null 2>&1; then
      missing+=("$cmd|$hint")
    fi
  done <<< "$tier"

  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "Missing required system dependencies:" >&2
    for m in "${missing[@]}"; do
      IFS='|' read -r cmd hint <<< "$m"
      echo "  - $cmd" >&2
      echo "      $hint" >&2
    done
    echo >&2
    echo "Install the above, then re-run. (Checked in dependency order — later" >&2
    echo "tiers of dependencies are not checked until this tier is satisfied.)" >&2
    exit 1
  fi
done

exit 0
