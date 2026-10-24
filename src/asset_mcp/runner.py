"""Runs Blender headless to build a .blend's bundle (see `common.assets`).

The executable is `$BLENDER_PATH`, else `blender` on PATH, else the newest Windows install under
`/mnt/c/Program Files/Blender Foundation/` (the WSL case: a Windows `blender.exe` takes Windows
paths, so every path handed to it goes through `wslpath -w`). The Blender-side script and its
arguments are written into a work folder inside the bundle, so Blender reads them from the same
drive as the .blend, and the folder is removed afterwards.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from common import assets as conv

SCRIPT = Path(__file__).with_name("blender_export.py")
WINDOWS_INSTALLS = Path("/mnt/c/Program Files/Blender Foundation")
TIMEOUT_SECONDS = 900


class BlenderError(Exception):
    pass


def find_blender() -> Path:
    env = os.environ.get("BLENDER_PATH")
    if env:
        if not Path(env).is_file():
            raise BlenderError(f"BLENDER_PATH points at {env!r}, which is not a file")
        return Path(env)
    on_path = shutil.which("blender")
    if on_path:
        return Path(on_path)
    installs = sorted(WINDOWS_INSTALLS.glob("Blender */blender.exe"), key=lambda p: [int(x) if x.isdigit() else 0 for x in p.parent.name.split()[-1].split(".")])
    if installs:
        return installs[-1]
    raise BlenderError("Blender was not found: set BLENDER_PATH, or put blender on PATH")


def _native(blender: Path, path: Path) -> str:
    if blender.suffix.lower() != ".exe":
        return str(path)
    out = subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, check=False)
    if out.returncode != 0 or not out.stdout.strip():
        raise BlenderError(f"cannot convert {path} for a Windows Blender: {out.stderr.strip()}")
    return out.stdout.strip()


def export_bundle(blend: Path, *, collection: str | None = None, resolution: int = 768, uv_size: int = 1024) -> dict:
    """Build `<blend stem>.view/` beside `blend`; returns {ok, errors, objects, bundle, log_tail}."""
    blender = find_blender()
    bundle = conv.bundle_dir(blend)
    work = bundle / ".work"
    if bundle.is_dir():
        for old in list(bundle.glob("view_*.png")) + list(bundle.glob("uv_*.png")) + [bundle / conv.MODEL_NAME, bundle / conv.MANIFEST_NAME]:
            old.unlink(missing_ok=True)
        shutil.rmtree(bundle / conv.TEXTURE_DIR, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy(SCRIPT, work / "blender_export.py")
        (work / "args.json").write_text(json.dumps({
            "bundle": _native(blender, bundle), "source_name": blend.name, "source_mtime": blend.stat().st_mtime,
            "collection": collection, "resolution": resolution, "uv_size": uv_size,
        }), encoding="utf-8")
        command = [str(blender), "--background", "--factory-startup", _native(blender, blend),
                   "--python", _native(blender, work / "blender_export.py"), "--", _native(blender, work / "args.json")]
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False)
        except subprocess.TimeoutExpired as exc:
            raise BlenderError(f"Blender did not finish within {TIMEOUT_SECONDS} seconds") from exc
        try:
            result = json.loads((work / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            tail = "\n".join((run.stdout + run.stderr).strip().splitlines()[-15:])
            raise BlenderError(f"Blender exited {run.returncode} without a result:\n{tail}") from None
        result["bundle"] = str(bundle)
        result["log_tail"] = "\n".join(run.stdout.strip().splitlines()[-8:])
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)
