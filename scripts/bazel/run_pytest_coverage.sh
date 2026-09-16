#!/usr/bin/env bash
set -euo pipefail

# $1 is the location of pyproject.toml, used purely to find the project root.
# `pwd -P` (not plain `pwd`): Bazel's runfiles tree reaches the real source
# files through a symlink, and coverage.py records the path Python actually
# imported through — resolving symlinks here up front keeps that path and
# `--cov=src`'s path the same physical location, instead of two spellings
# of one directory that coverage.py then can't reconcile (it silently
# reports 0% measured coverage rather than erroring, so this is easy to
# miss without comparing to a real, non-zero run).
# KNOWN LIMITATION, unresolved: under `bazel test` specifically (this
# script run directly is unaffected — verified: 99% coverage, matching
# a plain `pytest --cov` run byte-for-byte), coverage.py's `.coverage`
# database ends up with every source file registered but zero recorded
# line hits (its `file` table is populated; `line_bits`/`context` are
# empty), so this target's own pass/fail line is not trustworthy right
# now — neither `relative_files`, an absolute `--cov` path, nor forcing
# the legacy `ctrace` core over the sys.monitoring-based default changed
# the outcome. `bazel test //:coverage` still runs the full real suite
# (that part is meaningful — every test still has to pass), but for the
# actual coverage percentage, run this script directly instead:
#   bash scripts/bazel/run_pytest_coverage.sh pyproject.toml
PROJECT_ROOT="$(cd "$(dirname "$1")" && pwd -P)"
cd "$PROJECT_ROOT"
exec python3 -m pytest -q --cov=src --cov-report=term-missing --cov-fail-under=90
