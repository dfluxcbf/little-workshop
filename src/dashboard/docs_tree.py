"""The Docs page as folders: from the project's flat list of Markdown files (`{path, open, done, certified}`) the
entries of one directory — its subfolders, each with the totals of everything beneath it, and its own files — and
the breadcrumb leading to it."""

from __future__ import annotations

COUNTS = ("open", "done", "certified")


def clean_dir(raw: str) -> str:
    """`raw` as a directory path without leading, trailing or repeated slashes; `..` parts are dropped."""
    return "/".join(part for part in (raw or "").split("/") if part not in ("", ".", ".."))


def folder_view(files: list[dict], directory: str) -> dict:
    directory = clean_dir(directory)
    prefix = directory + "/" if directory else ""
    folders: dict[str, dict] = {}
    own: list[dict] = []
    for f in files:
        path = f["path"]
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix):]
        if "/" not in rest:
            own.append({**f, "name": rest})
            continue
        name = rest.split("/", 1)[0]
        entry = folders.setdefault(name, {"name": name, "path": prefix + name, "files": 0, **{k: 0 for k in COUNTS}})
        entry["files"] += 1
        for key in COUNTS:
            entry[key] += f.get(key, 0)
    parts = directory.split("/") if directory else []
    crumbs = [{"name": part, "path": "/".join(parts[: i + 1])} for i, part in enumerate(parts)]
    return {"directory": directory, "crumbs": crumbs, "folders": sorted(folders.values(), key=lambda e: e["name"].lower()),
            "files": sorted(own, key=lambda e: e["name"].lower()), "exists": bool(folders or own or not directory)}
