"""Asset review, served from a project's checkout: the tree of the visible asset
folder, one asset's details (its generated bundle, its comment) and raw files.
The conventions live in `common.assets`; nothing outside the folder is read.
Comments are plain Markdown files beside the asset, written in place and never
committed by little-workshop.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from common import assets as conv
from common.assets import AssetError

MAX_COMMENT_BYTES = 200_000


def _entries(directory: Path):
    try:
        return sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return []


def _tree(root: Path, directory: Path) -> list[dict]:
    nodes: list[dict] = []
    for entry in _entries(directory):
        if entry.name.startswith(".") or entry.is_symlink() and not entry.resolve().is_relative_to(root):
            continue
        if entry.is_dir():
            if entry.name.endswith(conv.BUNDLE_SUFFIX):
                continue
            children = _tree(root, entry)
            if children:
                nodes.append({"type": "dir", "name": entry.name, "path": conv.rel_path(root, entry), "children": children})
            continue
        kind = conv.kind_of(entry)
        if kind is None or kind == "doc" and conv.is_comment(entry):
            continue
        node = {"type": "file", "name": entry.name, "path": conv.rel_path(root, entry), "kind": kind,
                "commented": kind != "doc" and conv.comment_file(entry).is_file()}
        if kind == "blend":
            node["bundle"] = conv.bundle_state(entry)
        nodes.append(node)
    return nodes


def tree(checkout: Path, assets_dir: str | None) -> dict:
    root = conv.asset_root(checkout, assets_dir)
    return {"assets_dir": assets_dir, "tree": _tree(root, root)}


def _bundle(root: Path, blend: Path) -> dict:
    state = conv.bundle_state(blend)
    out: dict = {"state": state, "model": None, "views": [], "uv": [], "textures": [], "generated_at": None, "errors": []}
    manifest = conv.read_manifest(blend)
    if manifest is None:
        return out
    folder = conv.bundle_dir(blend)
    out["generated_at"] = manifest.get("generated_at")
    out["errors"] = manifest.get("errors") or []
    if (folder / conv.MODEL_NAME).is_file():
        out["model"] = conv.rel_path(root, folder / conv.MODEL_NAME)
    for name in conv.VIEWS:
        image = folder / conv.view_file(name)
        if image.is_file():
            out["views"].append({"name": name, "path": conv.rel_path(root, image)})
    out["uv"] = [{"name": p.stem, "path": conv.rel_path(root, p)} for p in sorted(folder.glob("uv_*.png"))]
    out["textures"] = [{"name": p.stem, "path": conv.rel_path(root, p)}
                       for p in sorted((folder / conv.TEXTURE_DIR).glob("*")) if p.suffix.lower() in conv.IMAGE_TYPES]
    return out


def details(checkout: Path, assets_dir: str | None, rel: str) -> dict:
    root = conv.asset_root(checkout, assets_dir)
    target = conv.resolve_inside(root, rel)
    if not target.is_file():
        raise AssetError(f"no such file: {rel!r}", 404)
    kind = conv.kind_of(target)
    if kind is None:
        raise AssetError(f"not an asset file: {rel!r}")
    result = {"path": rel, "name": target.name, "kind": kind, "size": target.stat().st_size, "modified": target.stat().st_mtime,
              "bundle": _bundle(root, target) if kind == "blend" else None, "comment": None,
              "comment_path": None, "doc": None}
    if kind == "doc":
        result["doc"] = target.read_text(encoding="utf-8", errors="replace")
    else:
        comment = conv.comment_file(target)
        result["comment_path"] = conv.rel_path(root, comment)
        result["comment"] = comment.read_text(encoding="utf-8", errors="replace") if comment.is_file() else ""
    return result


def read_file(checkout: Path, assets_dir: str | None, rel: str) -> tuple[bytes, str]:
    root = conv.asset_root(checkout, assets_dir)
    target = conv.resolve_inside(root, rel)
    if not target.is_file():
        raise AssetError(f"no such file: {rel!r}", 404)
    if conv.kind_of(target) is None and target.suffix.lower() != ".json":
        raise AssetError(f"not a servable asset file: {rel!r}")
    media_type = {".glb": "model/gltf-binary", ".gltf": "model/gltf+json", ".md": "text/markdown"}.get(target.suffix.lower())
    return target.read_bytes(), media_type or conv.IMAGE_TYPES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"


def write_comment(checkout: Path, assets_dir: str | None, rel: str, body: str) -> dict:
    """Save the comment on an asset beside it as `<asset file name>.md`; an empty body deletes the file."""
    root = conv.asset_root(checkout, assets_dir)
    target = conv.resolve_inside(root, rel)
    kind = conv.kind_of(target)
    if not target.is_file() or kind in (None, "doc"):
        raise AssetError(f"cannot comment on {rel!r}", 404 if not target.is_file() else 400)
    if len(body.encode("utf-8")) > MAX_COMMENT_BYTES:
        raise AssetError("the comment is too long")
    comment = conv.comment_file(target)
    if body.strip():
        comment.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
    elif comment.is_file():
        comment.unlink()
    return {"path": rel, "comment_path": conv.rel_path(root, comment), "saved": bool(body.strip())}
