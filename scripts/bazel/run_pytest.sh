#!/usr/bin/env bash
set -euo pipefail

# $1 is the location of pyproject.toml, used purely to find the project root.
PROJECT_ROOT="$(cd "$(dirname "$1")" && pwd)"
cd "$PROJECT_ROOT"
exec python3 -m pytest -q
