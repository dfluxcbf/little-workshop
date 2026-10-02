"""Policy `design-index-and-schema-current` (build pillar; gates branch_finish; project
ethereal-lands): the generated parts of the project match their generators -- the design index
blocks and docs/design/README.md (tools/update_design_index.py) and the ELM JSON schema
Assets/Machination/Schema/machination.schema.json (tools/elm-schema.py).

These files are written by scripts and never by hand; a hand edit is overwritten the next time
the script runs, and a stale file misleads the C# loader, the viewer and readers. Both scripts
are standard-library Python and compare without writing (`--check`). Current repository state
only."""

import os
import subprocess
import sys

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
CHECKS = (
    ("tools/update_design_index.py", "the design index blocks in docs/design.md and docs/design/*.md, and docs/design/README.md"),
    ("tools/elm-schema.py", "Assets/Machination/Schema/machination.schema.json"),
)
FIX = (
    "Fix: never edit these by hand; change the source (the design documents' headings and status lines, or "
    "the definitions in tools/elm-schema.py), run the script without --check from the repository root, and "
    "commit what it writes."
)


def test_generated_files_match_their_generators():
    present = [(script, what) for script, what in CHECKS if os.path.isfile(os.path.join(PROJECT, script))]
    if not present:
        pytest.skip("neither generator exists in this project")
    stale = []
    for script, what in present:
        run = subprocess.run(
            [sys.executable, script, "--check"], cwd=PROJECT, capture_output=True, text=True, timeout=300,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if run.returncode != 0:
            output = (run.stderr.strip() or run.stdout.strip())[-1500:]
            stale.append(f"{what} differs from what `python3 {script}` writes:\n{output}")
    assert not stale, "\n".join(stale) + f"\n{FIX}"
