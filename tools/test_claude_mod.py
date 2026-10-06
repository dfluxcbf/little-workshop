#!/usr/bin/env python3
"""Check the little-workshop status mod (src/usage/claude_mod/, REQ-000000414) the way Claude Code loads it:
render it into a scratch folder as `usage.configure_claude` would, with its tests beside it, then run
`claude plugin validate --strict` and `claude plugin test` there. Needs the `claude` CLI, so it stays out of
tests/unit (network-free, no outside tools); run it after changing the mod:

    python3 tools/test_claude_mod.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from usage import profile  # noqa: E402

TESTS = ("register.test.tsx",)


def main() -> int:
    if shutil.which("claude") is None:
        print("claude is not on PATH: install Claude Code to check the mod", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="lws-claude-mod-") as scratch:
        folder = Path(scratch) / profile.MOD_NAME
        files = profile.mod_files(profile.StatusMod(Path(scratch) / "dashboard.sock", "http://localhost:8790"))
        for target, text in files.items():
            (folder / target).parent.mkdir(parents=True, exist_ok=True)
            (folder / target).write_text(text, encoding="utf-8")
        for name in TESTS:
            shutil.copy(REPO / "src" / "usage" / "claude_mod" / name, folder / "hooks" / name)
        for command in (["claude", "plugin", "validate", "--strict", "."], ["claude", "plugin", "test", "."]):
            print(f"$ {' '.join(command)}", flush=True)
            if subprocess.run(command, cwd=folder).returncode != 0:
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
