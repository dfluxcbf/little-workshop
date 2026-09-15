#!/usr/bin/env bash
set -euo pipefail

# $1 = the pillar's module name (e.g. "core", "orchestration")
# $2 = location of pyproject.toml, used purely to find the project root
MODULE="$1"
PROJECT_ROOT="$(cd "$(dirname "$2")" && pwd)"
cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT/src"
exec python3 -m "$MODULE"
