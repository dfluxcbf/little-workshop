"""Asset review: what a project's visible asset folder holds and where the
files derived from a Blender source live.

A project opts in by naming one folder of its checkout (`assets_dir`, kept by
Registry & Portfolio). Only that folder and its subfolders are ever listed or
served. Three conventions, shared by the pillar that serves the folder
(Agentic Development), the Dashboard pages and the per-project asset MCP
server that generates the derived files:

- **Bundle**: for `<dir>/<name>.blend` the derived files live in the sibling
  folder `<dir>/<name>.view/` — `model.glb` (what the Dashboard loads in 3D),
  the ten preview renders `view_<name>.png`, `uv_<mesh>.png` UV layouts,
  `textures/<image>.png` and `manifest.json`. A bundle folder is never listed
  as a folder of its own: it belongs to its Blender file.
- **Comment**: the reviewer's comment on an asset is the Markdown file with the
  asset's name and the `.md` suffix, beside it (`<dir>/<name>.md`).
- **Containment**: every path is relative to the asset folder and resolved
  inside it; `..`, absolute paths and links leading out are refused.
"""

from __future__ import annotations

import json
from pathlib import Path

BUNDLE_SUFFIX = ".view"
MODEL_NAME = "model.glb"
MANIFEST_NAME = "manifest.json"
TEXTURE_DIR = "textures"

BLEND_SUFFIX = ".blend"
MODEL_SUFFIXES = (".glb", ".gltf")
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
               ".gif": "image/gif", ".bmp": "image/bmp"}

# The ten preview renders, in the order the pages show them: the four sides,
# top and bottom, then two orthographic corner views from above and two from below.
# name -> (azimuth degrees, elevation degrees); azimuth 0 looks at the front (-Y) face.
VIEWS: dict[str, tuple[int, int]] = {
    "front": (0, 0), "right": (90, 0), "back": (180, 0), "left": (270, 0),
    "top": (0, 90), "bottom": (0, -90),
    "ortho_top_1": (45, 35), "ortho_top_2": (225, 35),
    "ortho_bottom_1": (45, -35), "ortho_bottom_2": (225, -35),
}


class AssetError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def view_file(name: str) -> str:
    return f"view_{name}.png"


def asset_root(checkout: Path, assets_dir: str | None) -> Path:
    """The visible folder, resolved; `AssetError` when none is configured or it is gone."""
    if not assets_dir:
        raise AssetError("no asset folder is configured for this project", 404)
    root = (checkout / assets_dir).resolve()
    if not root.is_relative_to(checkout.resolve()):
        raise AssetError("the asset folder lies outside the project")
    if not root.is_dir():
        raise AssetError(f"the asset folder {assets_dir!r} does not exist in the checkout", 404)
    return root


def resolve_inside(root: Path, rel: str) -> Path:
    """`rel` (slash-separated, relative to the asset folder) as a path inside it."""
    if not rel or rel.startswith(("/", "\\")) or "\x00" in rel:
        raise AssetError(f"not a path inside the asset folder: {rel!r}")
    target = (root / rel).resolve()
    if target != root and not target.is_relative_to(root):
        raise AssetError(f"not a path inside the asset folder: {rel!r}")
    return target


def rel_path(root: Path, target: Path) -> str:
    return target.relative_to(root).as_posix()


def bundle_dir(blend: Path) -> Path:
    return blend.with_name(blend.stem + BUNDLE_SUFFIX)


def comment_file(asset: Path) -> Path:
    return asset.with_suffix(".md")


def kind_of(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == BLEND_SUFFIX:
        return "blend"
    if suffix in MODEL_SUFFIXES:
        return "model"
    if suffix in IMAGE_TYPES:
        return "image"
    if suffix == ".md":
        return "doc"
    return None


def is_comment(path: Path) -> bool:
    """A Markdown file that has a sibling asset of the same name is that asset's comment."""
    if path.suffix.lower() != ".md":
        return False
    return any(sibling.stem == path.stem and kind_of(sibling) in ("blend", "model", "image")
               for sibling in path.parent.iterdir() if sibling != path)


def read_manifest(blend: Path) -> dict | None:
    try:
        data = json.loads((bundle_dir(blend) / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def bundle_state(blend: Path) -> str:
    """`none` (never exported), `stale` (the .blend changed since) or `ready`."""
    manifest = read_manifest(blend)
    if manifest is None:
        return "none"
    try:
        return "ready" if blend.stat().st_mtime <= float(manifest.get("source_mtime", 0)) + 1 else "stale"
    except (OSError, ValueError, TypeError):
        return "stale"
