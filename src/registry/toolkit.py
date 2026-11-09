"""The toolkit: public Claude Code skills, agents, commands and plugins from GitHub repositories, installed per
project (docs/pillars/registry_portfolio.md, "Toolkit").

A **source** is a GitHub repository registered once and pinned to a commit; its **items** are found in its tree
(`.claude-plugin/marketplace.json` plugins, `**/SKILL.md` skills (a `SKILL.md` at the root is the repository's one
skill), `agents/*.md` and `commands/*.md`, at the root or under `.claude/`, and the MCP servers of every `.mcp.json`).
A repository hosted elsewhere (`host/owner/repo`) is registered with the MCP servers it declares and a version label;
nothing is fetched from it. A plugin is installed with Claude Code's own `claude plugin` commands at project scope; a
skill, agent or command is copied at the pinned commit into the project's `.claude/`; an MCP server is an entry of
the project's `.mcp.json` (Claude Code asks each user to approve a project's servers before starting one). Every install is recorded in the
project's `.claude/toolkit.lock.json`, and removal deletes exactly what it records. An item that runs code (hooks,
scripts) is installed only with `confirm_code`. Nothing here installs on its own: the defaults are recommendations.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Protocol

from common.shell import DEFAULT_RUNNER, CommandRunner

LOCK_PATH = ".claude/toolkit.lock.json"
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
# A repository on another forge: its host first (projects.blender.org/lab/blender_mcp). Never fetched.
OTHER_REPO = re.compile(r"(?=[A-Za-z0-9-]+\.)[A-Za-z0-9.-]+\.[A-Za-z]{2,}/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
KINDS = ("plugin", "skill", "agent", "command", "mcp")
MCP_PATH = ".mcp.json"
MCP_NAME = re.compile(r"[A-Za-z0-9_-]+")
MCP_KEYS = ("type", "command", "args", "env", "url", "headers")  # Claude Code's .mcp.json keys; others are dropped
CODE_DIRS = {"hooks", "scripts", "bin"}
CODE_SUFFIXES = {".sh", ".bash", ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".cmd", ".ps1", ".bat", ".rb", ".exe"}
NOT_ITEMS = {"tests", "test", "fixtures", "examples", "benchmarks"}  # a repository's own test data is not its items
DEST = {"skill": ".claude/skills/{name}", "agent": ".claude/agents/{name}.md", "command": ".claude/commands/{name}.md"}

# Registered by `seed` and offered first (docs/toolkit_sources.md); `recommend` reads the items per project kind.
# ponytail: its skills only, never its hooks, statusline or band. claude-reflect: already installed at user scope.
# mattpocock's implement-spec is left out: it runs its own integration branch, worktree branches, merger subagents
# and pull requests, which compete with the branches little-workshop finishes (2026-11-09).
DEFAULT_SOURCES = {
    "mattpocock/skills": {"code": ("grill-me", "grill-with-docs", "to-spec", "diagnosing-bugs", "tdd",
                                   "handoff", "writing-for-agents")},
    "DietrichGebert/ponytail": {"code": ("ponytail-review", "ponytail-audit", "ponytail-debt")},
    "BayramAnnakov/claude-reflect": {},
    "pbakaus/impeccable": {"ui": ("impeccable",)},
}
# Usage findings (detector -> skills that address them): a long session hands over; a file read in every session
# becomes a digest written for agents.
FINDING_ITEMS = {"D1": ("mattpocock/skills", "handoff"), "D8": ("mattpocock/skills", "writing-for-agents")}


class ToolkitError(RuntimeError):
    pass


class Fetcher(Protocol):
    def repo(self, repo: str) -> dict: ...  # {"licence", "default_branch"}
    def head(self, repo: str, branch: str) -> str: ...
    def tree(self, repo: str, sha: str) -> list[dict]: ...  # [{"path", "mode"}], blobs only
    def read(self, repo: str, sha: str, path: str) -> bytes: ...
    def changed(self, repo: str, old: str, new: str) -> list[str]: ...


class GhFetcher:
    """GitHub's REST API through the logged-in `gh` CLI."""

    def _api(self, *args: str) -> bytes:
        done = subprocess.run(["gh", "api", *args], capture_output=True, timeout=120)
        if done.returncode != 0:
            raise ToolkitError(f"gh api {args[-1]}: {done.stderr.decode(errors='replace').strip()[-300:]}")
        return done.stdout

    def repo(self, repo: str) -> dict:
        data = json.loads(self._api(f"repos/{repo}"))
        return {"licence": (data.get("license") or {}).get("spdx_id") or "unknown", "default_branch": data["default_branch"]}

    def head(self, repo: str, branch: str) -> str:
        return json.loads(self._api(f"repos/{repo}/commits/{branch}"))["sha"]

    def tree(self, repo: str, sha: str) -> list[dict]:
        data = json.loads(self._api(f"repos/{repo}/git/trees/{sha}?recursive=1"))
        return [{"path": e["path"], "mode": e["mode"]} for e in data["tree"] if e["type"] == "blob"]

    def read(self, repo: str, sha: str, path: str) -> bytes:
        return self._api("-H", "Accept: application/vnd.github.raw", f"repos/{repo}/contents/{path}?ref={sha}")

    def changed(self, repo: str, old: str, new: str) -> list[str]:
        return [f["filename"] for f in json.loads(self._api(f"repos/{repo}/compare/{old}...{new}")).get("files", [])]


def check_repo(repo: str) -> str:
    if not (REPO.fullmatch(repo or "") or OTHER_REPO.fullmatch(repo or "")):
        raise ToolkitError(f"not a repository: {repo!r} (expected owner/repo on GitHub, or host/owner/repo elsewhere)")
    return repo


def on_github(repo: str) -> bool:
    return bool(REPO.fullmatch(repo or ""))


def mcp_server(name: str, entry: dict) -> dict:
    """One MCP server as a project's `.mcp.json` holds it: Claude Code's keys only (a command and its arguments and
    environment, or a URL), refused when it names neither."""
    if not MCP_NAME.fullmatch(name or ""):
        raise ToolkitError(f"not an MCP server name: {name!r} (letters, digits, - and _)")
    if not isinstance(entry, dict) or not (entry.get("command") or entry.get("url")):
        raise ToolkitError(f"MCP server {name!r} names neither a command nor a url")
    server = {k: entry[k] for k in MCP_KEYS if entry.get(k) not in (None, "", [], {})}
    if not all(isinstance(a, str) for a in server.get("args", [])) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in server.get("env", {}).items()):
        raise ToolkitError(f"MCP server {name!r}: args must be strings and env a map of strings")
    return server


def mcp_runs(server: dict) -> list[str]:
    """What an MCP server runs, for the confirmation an install asks: every MCP server runs code."""
    if server.get("command"):
        return [" ".join(["MCP server runs:", server["command"], *server.get("args", [])])]
    return [f"MCP server at {server['url']}"]


def mcp_item(name: str, entry: dict, description: str = "", path: str = "", declared: bool = False) -> dict:
    server = mcp_server(name, entry)
    item = {"id": f"mcp:{name}", "kind": "mcp", "name": name, "path": path, "description": description,
            "files": [], "runs_code": mcp_runs(server), "server": server}
    return {**item, "declared": True} if declared else item


def frontmatter(text: str) -> dict:
    """The `key: value` lines of a Markdown file's leading `---` block (folded `>`/`|` values joined)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out, key = {}, None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        match = re.match(r"([A-Za-z_-]+):\s*(.*)$", line)
        if match and not line.startswith((" ", "\t")):
            key, value = match.group(1), match.group(2).strip()
            out[key] = "" if value in (">", "|", ">-", "|-") else value.strip("'\"")
        elif key and line.strip():
            out[key] = f"{out[key]} {line.strip()}".strip()
    return out


def runs_code(path: str, mode: str = "100644") -> bool:
    p = PurePosixPath(path)
    return mode == "100755" or p.suffix in CODE_SUFFIXES or bool(CODE_DIRS & set(p.parts[:-1]))


def _under(files: dict[str, str], prefix: str) -> list[str]:
    return sorted(p for p in files if not prefix or p.startswith(prefix + "/"))


def discover(fetcher: Fetcher, repo: str, sha: str) -> list[dict]:
    """Every item of the repository at `sha`: {id, kind, name, path, description, files, runs_code[, marketplace]}."""
    files = {e["path"]: e["mode"] for e in fetcher.tree(repo, sha)}
    text = lambda path: fetcher.read(repo, sha, path).decode("utf-8", errors="replace")
    items: dict[str, dict] = {}

    def add(kind: str, name: str, path: str, item_files: list[str], description: str, **extra) -> None:
        rel = lambda f: PurePosixPath(f).name if f == path else f[len(path) + 1:] if path else f
        code = [f for f in item_files if runs_code(rel(f), files[f])]
        items.setdefault(f"{kind}:{name}", {"id": f"{kind}:{name}", "kind": kind, "name": name, "path": path,
                                            "description": description, "files": item_files, "runs_code": code, **extra})

    if ".claude-plugin/marketplace.json" in files:
        market = json.loads(text(".claude-plugin/marketplace.json"))
        for plugin in market.get("plugins") or []:
            source = plugin.get("source")
            if not isinstance(source, str) or not source.startswith("./"):
                continue  # a plugin hosted elsewhere is that repository's source
            prefix = source[2:].strip("/")
            manifest = f"{prefix}/.claude-plugin/plugin.json" if prefix else ".claude-plugin/plugin.json"
            description = plugin.get("description") or (json.loads(text(manifest)).get("description", "") if manifest in files else "")
            add("plugin", plugin["name"], prefix, _under(files, prefix), description, marketplace=market["name"])
    # Skills: other harnesses' copies (.agents/, .openclaw/, ...) and test data are skipped; `.claude/skills/` wins over a duplicate.
    skipped = lambda p: any(part.startswith(".") and part != ".claude" or part in NOT_ITEMS for part in PurePosixPath(p).parts[:-1])
    skills = [p for p in files if PurePosixPath(p).name == "SKILL.md" and "/" in p and not skipped(p)]
    for path in sorted(skills, key=lambda p: (not p.startswith(".claude/"), p.count("/"), p)):
        folder = str(PurePosixPath(path).parent)
        name = PurePosixPath(folder).name
        if f"skill:{name}" not in items:
            meta = frontmatter(text(path))
            add("skill", name, folder, _under(files, folder), meta.get("description", ""))
    if "SKILL.md" in files and not skills:  # the repository is one skill: its files, minus the repository's own
        meta = frontmatter(text("SKILL.md"))
        own = [p for p in files if not (p.startswith(".") or PurePosixPath(p).parts[0] in NOT_ITEMS | {"LICENSE", "README.md"})]
        add("skill", meta.get("name") or repo.split("/")[-1], "", sorted(own), meta.get("description", ""))
    for path in sorted(p for p in files if PurePosixPath(p).name == MCP_PATH and not any(part in NOT_ITEMS for part in PurePosixPath(p).parts)):
        try:
            servers = json.loads(text(path)).get("mcpServers") or {}
        except (ValueError, AttributeError):
            continue
        for name, entry in sorted(servers.items()):
            if f"mcp:{name}" not in items:
                try:
                    items[f"mcp:{name}"] = mcp_item(name, entry, f"MCP server declared in {path}", path)
                except ToolkitError:
                    continue
    for kind, folder in (("agent", "agents"), ("command", "commands")):
        for path in sorted(files):
            p = PurePosixPath(path)
            if p.suffix == ".md" and str(p.parent) in (folder, f".claude/{folder}") and f"{kind}:{p.stem}" not in items:
                add(kind, p.stem, path, [path], frontmatter(text(path)).get("description", ""))
    return sorted(items.values(), key=lambda i: (KINDS.index(i["kind"]), i["name"]))


# -- the lock file ---------------------------------------------------------------------------------


def parse_lock(text: str | None) -> dict:
    try:
        lock = json.loads(text or "")
    except ValueError:
        return {"sources": {}}
    return lock if isinstance(lock, dict) and isinstance(lock.get("sources"), dict) else {"sources": {}}


def read_lock(checkout: Path) -> dict:
    try:
        return parse_lock((checkout / LOCK_PATH).read_text(encoding="utf-8"))
    except OSError:
        return {"sources": {}}


def write_lock(checkout: Path, lock: dict) -> None:
    path = checkout / LOCK_PATH
    if not lock["sources"]:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_mcp(checkout: Path) -> dict:
    try:
        data = json.loads((checkout / MCP_PATH).read_text(encoding="utf-8"))
    except OSError:
        return {"mcpServers": {}}
    except ValueError as exc:
        raise ToolkitError(f"{MCP_PATH} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.setdefault("mcpServers", {}), dict):
        raise ToolkitError(f"{MCP_PATH} has no mcpServers map")
    return data


def write_mcp(checkout: Path, data: dict) -> None:
    """The project's `.mcp.json`, removed when the toolkit took out its last server and nothing else is in it."""
    path = checkout / MCP_PATH
    if not data["mcpServers"] and set(data) == {"mcpServers"}:
        path.unlink(missing_ok=True)
        return
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def lock_plugins(checkout: Path) -> tuple[str, ...]:
    """The `plugin@marketplace` keys the project's lock enables (the generated settings keep them)."""
    return tuple(sorted(item["plugin"] for source in read_lock(checkout)["sources"].values()
                        for item in source.get("items", {}).values() if item.get("plugin")))


# -- install, remove, update ------------------------------------------------------------------------


def _claude(runner: CommandRunner, checkout: Path, *args: str) -> None:
    done = runner.run(["claude", "plugin", *args], cwd=checkout, timeout=300)
    if not done.ok:
        raise ToolkitError(f"claude plugin {' '.join(args)}: {(done.stderr or done.stdout).strip()[-300:]}")


def pick(source: dict, names: list[str]) -> list[dict]:
    """The source's items named by id (`skill:tdd`) or bare name; an unknown name is an error."""
    found, missing = [], []
    for name in names:
        match = [i for i in source["items"] if name in (i["id"], i["name"])]
        (found.extend(match[:1]) if match else missing.append(name))
    if missing:
        raise ToolkitError(f"{source['repo']} has no item {', '.join(missing)}")
    return found


def code_files(items: list[dict]) -> list[str]:
    return [f"{item['id']}: {path}" for item in items for path in item["runs_code"]]


def install(checkout: Path, fetcher: Fetcher, source: dict, items: list[dict], *, confirm_code: bool = False,
            runner: CommandRunner = DEFAULT_RUNNER) -> list[str]:
    """Install `items` of `source` ({repo, sha, licence}) into the checkout and record them in the lock; returns the
    paths written. Refused when an item runs code and `confirm_code` is not given, or when it would overwrite a file
    the lock does not record (a skill of the project's own)."""
    code = code_files(items)
    if code and not confirm_code:
        raise ToolkitError("these items run code; install them with confirm_code after reading: " + "; ".join(code))
    lock = read_lock(checkout)
    entry = lock["sources"].setdefault(source["repo"], {"items": {}})
    owner = {f: repo for repo, s in lock["sources"].items() for i in s.get("items", {}).values() for f in i.get("files", [])}
    owned = set(owner)
    servers_owner = {i["mcp"]: repo for repo, s in lock["sources"].items() for i in s.get("items", {}).values() if i.get("mcp")}
    written: list[str] = []
    for item in items:
        if item["kind"] == "mcp":
            data = read_mcp(checkout)
            name = item["name"]
            if name in data["mcpServers"] and servers_owner.get(name) != source["repo"]:
                whose = f"{servers_owner[name]}'s" if name in servers_owner else "the project's own"
                raise ToolkitError(f"{item['id']} would replace {whose} MCP server {name!r} in {MCP_PATH}")
            data["mcpServers"][name] = item["server"]
            write_mcp(checkout, data)
            entry["items"][item["id"]] = {"kind": "mcp", "mcp": name, "server": item["server"], "files": []}
            written.append(MCP_PATH)
            continue
        if item["kind"] == "plugin":
            key = f"{item['name']}@{item['marketplace']}"
            _claude(runner, checkout, "marketplace", "add", source["repo"])
            _claude(runner, checkout, "install", key, "--scope", "project")
            entry["items"][item["id"]] = {"kind": "plugin", "plugin": key, "files": []}
            written.append(".claude/settings.json")
            continue
        dest = DEST[item["kind"]].format(name=item["name"])
        inside = lambda path: path[len(item["path"]) + 1:] if item["path"] else path
        targets = {path: (f"{dest}/{inside(path)}" if item["kind"] == "skill" else dest) for path in item["files"]}
        clash = [t for t in targets.values() if (checkout / t).exists() and t not in owned]
        if clash:
            raise ToolkitError(f"{item['id']} would overwrite the project's own {', '.join(clash)}")
        taken = sorted({owner[t] for t in targets.values() if owner.get(t) not in (None, source["repo"])})
        if taken:
            raise ToolkitError(f"{item['id']} from {source['repo']} would overwrite files {', '.join(taken)} installed")
        for path, target in targets.items():
            out = checkout / target
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(fetcher.read(source["repo"], source["sha"], path))
            if path in item["runs_code"]:
                out.chmod(0o755)
        entry["items"][item["id"]] = {"kind": item["kind"], "path": item["path"], "files": sorted(targets.values())}
        written += targets.values()
    entry.update(sha=source["sha"], licence=source["licence"])
    write_lock(checkout, lock)
    return sorted({*written, LOCK_PATH})


def remove(checkout: Path, repo: str, names: list[str] | None = None, *, runner: CommandRunner = DEFAULT_RUNNER) -> list[str]:
    """Remove the named items of `repo` (all of them when `names` is None) exactly as the lock records them; returns
    the paths changed."""
    lock = read_lock(checkout)
    entry = lock["sources"].get(repo)
    if entry is None:
        raise ToolkitError(f"nothing from {repo} is installed")
    ids = list(entry["items"]) if names is None else [i for i in entry["items"] if any(n in (i, i.split(":", 1)[1]) for n in names)]
    if names is not None and not ids:
        raise ToolkitError(f"none of {', '.join(names)} is installed from {repo}")
    changed: list[str] = []
    for item_id in ids:
        item = entry["items"].pop(item_id)
        if item.get("mcp"):
            data = read_mcp(checkout)
            data["mcpServers"].pop(item["mcp"], None)
            write_mcp(checkout, data)
            changed.append(MCP_PATH)
        if item.get("plugin"):
            _claude(runner, checkout, "uninstall", item["plugin"], "--scope", "project")
            changed.append(".claude/settings.json")
        for rel in item.get("files", []):
            path = checkout / rel
            path.unlink(missing_ok=True)
            changed.append(rel)
            for parent in path.parents:  # the skill's emptied folders
                if parent == checkout or not parent.is_dir() or any(parent.iterdir()):
                    break
                parent.rmdir()
    if not entry["items"]:
        del lock["sources"][repo]
    write_lock(checkout, lock)
    claude_dir = checkout / ".claude"
    if claude_dir.is_dir() and not any(claude_dir.iterdir()):
        claude_dir.rmdir()
    return sorted({*changed, LOCK_PATH})


def update_plan(checkout: Path, fetcher: Fetcher, source: dict) -> dict:
    """What bringing the project's items of `source` to its pin changes: {"ids", "items", "changed", "code"} (the
    project's item files that differ, and those of them that run code). Empty when the lock is at the pin."""
    entry = read_lock(checkout)["sources"].get(source["repo"])
    if entry is None:
        raise ToolkitError(f"nothing from {source['repo']} is installed")
    if entry.get("sha") == source["sha"]:
        return {"ids": [], "items": [], "changed": [], "code": []}
    ids = [i for i in entry["items"] if entry["items"][i]["kind"] != "plugin"]
    items = pick(source, ids)
    diff = set(fetcher.changed(source["repo"], entry["sha"], source["sha"])) if on_github(source["repo"]) else set()
    # An MCP server whose definition changed runs something else: asked again like changed code.
    servers = [item for item in items if item["kind"] == "mcp" and item.get("server") != entry["items"][item["id"]].get("server")]
    return {"ids": ids, "items": items,
            "changed": sorted({*(f for item in items for f in item["files"] if f in diff), *(i["path"] for i in servers if i["path"])}),
            "code": [f for item in items for f in item["runs_code"] if f in diff] + [line for i in servers for line in i["runs_code"]]}


def refresh(checkout: Path, fetcher: Fetcher, source: dict, *, confirm_code: bool = False,
            runner: CommandRunner = DEFAULT_RUNNER) -> dict:
    """Bring the project's items of `source` to its pinned commit: {"changed": files of theirs that differ, "paths"}."""
    plan = update_plan(checkout, fetcher, source)
    if plan["code"] and not confirm_code:
        raise ToolkitError("the update changes code; run it with confirm_code after reading: " + "; ".join(plan["code"]))
    if read_lock(checkout)["sources"][source["repo"]].get("sha") == source["sha"]:
        return {"changed": [], "paths": []}
    ids, items = plan["ids"], plan["items"]
    paths = remove(checkout, source["repo"], ids, runner=runner) if ids else []
    paths += install(checkout, fetcher, source, items, confirm_code=True, runner=runner) if items else []
    if not items:  # plugins only: Claude Code updates them; the lock follows the pin
        lock = read_lock(checkout)
        lock["sources"][source["repo"]]["sha"] = source["sha"]
        write_lock(checkout, lock)
        paths.append(LOCK_PATH)
    return {"changed": plan["changed"], "paths": sorted(set(paths))}


def status(checkout: Path, lock: dict | None = None) -> list[dict]:
    """What the lock records (the checkout's own, or `lock`: the integration branch's when the checkout is on
    another branch), each item with the recorded files missing from the checkout (none checked for `lock`)."""
    missing = (lambda item: []) if lock is not None else (lambda item: [f for f in item.get("files", []) if not (checkout / f).exists()])
    return [{"repo": repo, "sha": entry.get("sha"), "licence": entry.get("licence"),
             "items": [{"id": item_id, **item, "missing": missing(item)}
                       for item_id, item in sorted(entry.get("items", {}).items())]}
            for repo, entry in sorted((lock if lock is not None else read_lock(checkout))["sources"].items())]


# -- recommendations --------------------------------------------------------------------------------


def project_kind(checkout: Path, has_ui: bool) -> set[str]:
    """`unity`, or `code` plus `ui` when the project has a UI (the agent's signal: a server manifest or tests/ui)."""
    if (checkout / "ProjectSettings" / "ProjectVersion.txt").is_file():
        return {"unity"}
    return {"code", "ui"} if has_ui else {"code"}


def recommend(kinds: set[str], findings: list[dict], installed: list[dict], sources: list[dict]) -> list[dict]:
    """The registered sources' items suggested for a project and why, minus what it has: [{repo, item, why}]."""
    have = {(s["repo"], i["id"]) for s in installed for i in s["items"]}
    known = {s["repo"]: {i["name"]: i for i in s["items"]} for s in sources}
    wanted: list[tuple[str, str, str]] = []
    for repo, per_kind in DEFAULT_SOURCES.items():
        for kind in sorted(kinds & set(per_kind)):
            wanted += [(repo, name, f"default for {kind} projects") for name in per_kind[kind]]
    for finding in findings:
        if finding.get("detector") in FINDING_ITEMS:
            repo, name = FINDING_ITEMS[finding["detector"]]
            wanted.append((repo, name, f"usage finding: {finding.get('title') or finding['detector']}"))
    out, seen = [], set()
    for repo, name, why in wanted:
        item = known.get(repo, {}).get(name)
        if item and (repo, item["id"]) not in have and (repo, name) not in seen:
            seen.add((repo, name))
            out.append({"repo": repo, "item": item, "why": why})
    return out


def commit_label(action: str, names: list[str], repos: list[str]) -> str:
    """The commit subject's label: `toolkit-<item>` for one item, `toolkit-<repo>` for one source, else
    `toolkit-<n>-items`, with `-remove`/`-update` after `toolkit` for those actions."""
    if len(names) == 1:
        label = names[0].split(":", 1)[-1]
    elif len(repos) == 1:
        label = repos[0].split("/", 1)[1]
    else:
        label = f"{len(names)}-items"
    prefix = "toolkit-" if action == "install" else f"toolkit-{action}-"
    return prefix + re.sub(r"[^a-z0-9-]+", "-", label.lower()).strip("-")
