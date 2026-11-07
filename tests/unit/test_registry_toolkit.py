"""The toolkit (registry/toolkit.py): sources pinned and discovered from fixture trees, installs recorded in the
project's lock and removed exactly, code confirmed before it is installed, plugins through Claude Code's own CLI,
the default sources and their recommendations. Network-free: a fixture fetcher stands in for GitHub."""

from __future__ import annotations

import json
import os
import stat
import subprocess

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from registry import claude_sync, claude_templates, db, toolkit
from registry.app import build_app
from registry.models import Project

SKILL = "---\nname: {name}\ndescription: >\n  Use when {name}\n  is wanted.\n---\n\nSteps.\n"


class FixtureFetcher:
    """GitHub stand-in: {repo: {sha: {path: text or (text, mode)}}}; the newest sha is the last one."""

    def __init__(self, repos: dict, licences: dict | None = None):
        self.repos, self.licences, self.reads = repos, licences or {}, []

    def repo(self, repo):
        if repo not in self.repos:
            raise toolkit.ToolkitError(f"gh api repos/{repo}: Not Found")
        return {"licence": self.licences.get(repo, "MIT"), "default_branch": "main"}

    def head(self, repo, branch):
        return list(self.repos[repo])[-1]

    def _files(self, repo, sha):
        return {path: (v if isinstance(v, tuple) else (v, "100644")) for path, v in self.repos[repo][sha].items()}

    def tree(self, repo, sha):
        return [{"path": path, "mode": mode} for path, (_, mode) in self._files(repo, sha).items()]

    def read(self, repo, sha, path):
        self.reads.append(path)
        return self._files(repo, sha)[path][0].encode()

    def changed(self, repo, old, new):
        a, b = self._files(repo, old), self._files(repo, new)
        return sorted(p for p in {*a, *b} if a.get(p) != b.get(p))


PLUGIN_REPO = {
    ".claude-plugin/marketplace.json": json.dumps({"name": "tails", "plugins": [
        {"name": "tails", "source": "./", "description": "The tails plugin."},
        {"name": "remote", "source": {"source": "github", "repo": "x/y"}}]}),
    ".claude-plugin/plugin.json": json.dumps({"name": "tails", "description": "from plugin.json"}),
    "hooks/hooks.json": "{}",
    "hooks/activate.js": "console.log(1)",
    "skills/tails-review/SKILL.md": SKILL.format(name="tails-review"),
    ".openclaw/skills/tails-review/SKILL.md": SKILL.format(name="other harness"),
    "commands/tails.toml": "not a Claude command",
}
SKILL_REPO = {
    "skills/engineering/tdd/SKILL.md": SKILL.format(name="tdd"),
    "skills/engineering/tdd/reference/loop.md": "the loop",
    "skills/engineering/diagnosing-bugs/SKILL.md": SKILL.format(name="diagnosing-bugs"),
    "skills/engineering/diagnosing-bugs/scripts/hitl.sh": ("#!/bin/sh\necho hi\n", "100755"),
    "skills/productivity/handoff/SKILL.md": SKILL.format(name="handoff"),
    ".agents/skills/tdd/SKILL.md": SKILL.format(name="copy"),
    "tests/fixtures/.claude/skills/audit/SKILL.md": SKILL.format(name="test data"),
    "README.md": "skills",
}
AGENTS_REPO = {
    ".claude/agents/reviewer.md": "---\nname: reviewer\ndescription: Reviews a diff.\n---\nReview.\n",
    ".claude/skills/impeccable/SKILL.md": SKILL.format(name="impeccable"),
    "plugin/skills/impeccable/SKILL.md": SKILL.format(name="plugin copy"),
    "commands/reflect.md": "---\ndescription: Reflect on the session\n---\nReflect.\n",
}


def items_of(fetcher, repo, sha="s1"):
    return {i["id"]: i for i in toolkit.discover(fetcher, repo, sha)}


# --- REQ-000000502: Toolkit sources are pinned GitHub repositories with discovered items ---


def test_a_plugin_repository_lists_its_local_plugins_with_the_code_they_run():
    fetcher = FixtureFetcher({"o/tails": {"s1": PLUGIN_REPO}})
    items = items_of(fetcher, "o/tails")
    assert set(items) == {"plugin:tails", "skill:tails-review"}  # no remote plugin, no .toml command, no other harness
    plugin = items["plugin:tails"]
    assert (plugin["marketplace"], plugin["description"], plugin["path"]) == ("tails", "The tails plugin.", "")
    assert plugin["runs_code"] == ["hooks/activate.js", "hooks/hooks.json"]
    assert items["skill:tails-review"]["path"] == "skills/tails-review" and items["skill:tails-review"]["runs_code"] == []


def test_a_bare_skill_repository_lists_each_skill_once_with_its_description_and_scripts():
    items = items_of(FixtureFetcher({"o/skills": {"s1": SKILL_REPO}}), "o/skills")
    assert set(items) == {"skill:tdd", "skill:diagnosing-bugs", "skill:handoff"}  # .agents copies and test data skipped
    assert items["skill:tdd"]["description"] == "Use when tdd is wanted."
    assert items["skill:tdd"]["files"] == ["skills/engineering/tdd/SKILL.md", "skills/engineering/tdd/reference/loop.md"]
    assert items["skill:diagnosing-bugs"]["runs_code"] == ["skills/engineering/diagnosing-bugs/scripts/hitl.sh"]


def test_agents_and_commands_are_found_and_the_claude_copy_of_a_skill_wins():
    items = items_of(FixtureFetcher({"o/agents": {"s1": AGENTS_REPO}}), "o/agents")
    assert set(items) == {"skill:impeccable", "agent:reviewer", "command:reflect"}
    assert items["skill:impeccable"]["path"] == ".claude/skills/impeccable"
    assert items["agent:reviewer"]["description"] == "Reviews a diff."
    assert items["command:reflect"]["description"] == "Reflect on the session"


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "little-widget"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'little-widget'\n")
    _git(["init", "-b", "dev"], root)
    _git(["config", "user.email", "t@example.com"], root)
    _git(["config", "user.name", "T"], root)
    _git(["add", "-A"], root)
    _git(["commit", "-m", "first"], root)
    return root


@pytest.fixture
def registry(tmp_path, checkout):
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    conn = db.connect(tmp_path / "registry.db")
    db.insert_project(conn, Project(name="little-widget", path=str(checkout)))
    fetcher = FixtureFetcher({"o/skills": {"s1": SKILL_REPO}, "o/tails": {"s1": PLUGIN_REPO},
                              **{repo: {"s1": SKILL_REPO} for repo in toolkit.DEFAULT_SOURCES}}, {"pbakaus/impeccable": "Apache-2.0"})
    started = []

    def start_branch(project_id, name):
        started.append(name)
        _git(["checkout", "-b", f"improvement/{name}"], checkout)
        return {"status": "succeeded", "result": {"branch": f"improvement/{name}"}}

    client = TestClient(build_app(conn, paths, start_branch=start_branch, fetcher=fetcher, usage_findings=lambda p: []))
    client.fetcher, client.started = fetcher, started
    yield client
    conn.close()


def job(client, kind, project_id=None, **params):
    return client.post(f"/v1/jobs/registry.toolkit_{kind}", json={"job_id": "j1", "project_id": project_id, "params": params}).json()


def test_adding_a_source_pins_it_with_its_licence_and_items(registry):
    answer = job(registry, "add_source", repo="o/skills")
    assert answer["outcome"] == "succeeded" and answer["result"]["added"] == [{"repo": "o/skills", "sha": "s1", "licence": "MIT", "items": 3}]
    (source,) = registry.get("/v1/toolkit/sources").json()
    assert (source["repo"], source["sha"], source["licence"]) == ("o/skills", "s1", "MIT")
    assert {i["id"] for i in registry.get("/v1/toolkit/items", params={"repo": "o/skills"}).json()} == {
        "skill:tdd", "skill:diagnosing-bugs", "skill:handoff"}
    assert job(registry, "add_source", repo="not a repo")["outcome"] == "failed"


# --- REQ-000000503: Toolkit installs are recorded in the project's lock and removed exactly ---


def test_installing_a_skill_copies_it_records_it_and_removing_it_leaves_nothing(checkout):
    fetcher = FixtureFetcher({"o/skills": {"s1": SKILL_REPO}})
    source = {"repo": "o/skills", "sha": "s1", "licence": "MIT", "items": toolkit.discover(fetcher, "o/skills", "s1")}
    written = toolkit.install(checkout, fetcher, source, toolkit.pick(source, ["tdd"]))
    assert written == [".claude/skills/tdd/SKILL.md", ".claude/skills/tdd/reference/loop.md", toolkit.LOCK_PATH]
    assert (checkout / ".claude/skills/tdd/reference/loop.md").read_text() == "the loop"
    assert json.loads((checkout / toolkit.LOCK_PATH).read_text()) == {"sources": {"o/skills": {
        "sha": "s1", "licence": "MIT", "items": {"skill:tdd": {
            "kind": "skill", "path": "skills/engineering/tdd",
            "files": [".claude/skills/tdd/SKILL.md", ".claude/skills/tdd/reference/loop.md"]}}}}}

    toolkit.remove(checkout, "o/skills", ["tdd"])
    assert not (checkout / ".claude").exists() and _git(["status", "--porcelain"], checkout) == ""


def test_an_install_never_overwrites_a_skill_of_the_projects_own(checkout):
    (checkout / ".claude/skills/tdd").mkdir(parents=True)
    (checkout / ".claude/skills/tdd/SKILL.md").write_text("ours")
    fetcher = FixtureFetcher({"o/skills": {"s1": SKILL_REPO}})
    source = {"repo": "o/skills", "sha": "s1", "licence": "MIT", "items": toolkit.discover(fetcher, "o/skills", "s1")}
    with pytest.raises(toolkit.ToolkitError, match="would overwrite the project's own"):
        toolkit.install(checkout, fetcher, source, toolkit.pick(source, ["skill:tdd"]))
    assert (checkout / ".claude/skills/tdd/SKILL.md").read_text() == "ours"


def test_the_install_job_commits_on_a_toolkit_branch_and_the_status_reads_the_lock(registry, checkout):
    job(registry, "add_source", repo="o/skills")
    answer = job(registry, "install", "little-widget", repo="o/skills", items=["handoff"])
    assert answer["outcome"] == "succeeded", answer
    assert registry.started == ["toolkit-handoff"] and _git(["branch", "--show-current"], checkout) == "improvement/toolkit-handoff"
    assert _git(["log", "-1", "--format=%s"], checkout) == "impr (-1): toolkit-handoff" and _git(["status", "--porcelain"], checkout) == ""
    (installed,) = registry.get("/v1/toolkit/status/little-widget").json()["installed"]
    assert installed["repo"] == "o/skills" and [i["id"] for i in installed["items"]] == ["skill:handoff"]
    assert installed["items"][0]["missing"] == []

    _git(["checkout", "dev"], checkout)
    _git(["merge", "-q", "improvement/toolkit-handoff"], checkout)
    assert job(registry, "remove", "little-widget", repo="o/skills")["outcome"] == "succeeded"
    assert not (checkout / ".claude").exists() and registry.started[-1] == "toolkit-remove-skills"


def test_a_plugin_is_installed_and_removed_with_claude_codes_own_commands(tmp_path, checkout, monkeypatch):
    bin_dir, log = tmp_path / "bin", tmp_path / "claude.log"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\n"
                    "if [ \"$2\" = install ]; then mkdir -p .claude && "
                    "echo '{\"enabledPlugins\": {\"tails@tails\": true}}' > .claude/settings.json; fi\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    fetcher = FixtureFetcher({"o/tails": {"s1": PLUGIN_REPO}})
    source = {"repo": "o/tails", "sha": "s1", "licence": "MIT", "items": toolkit.discover(fetcher, "o/tails", "s1")}

    written = toolkit.install(checkout, fetcher, source, toolkit.pick(source, ["plugin:tails"]), confirm_code=True)
    assert written == [".claude/settings.json", toolkit.LOCK_PATH]
    assert log.read_text().splitlines() == ["plugin marketplace add o/tails", "plugin install tails@tails --scope project"]
    assert toolkit.lock_plugins(checkout) == ("tails@tails",)

    toolkit.remove(checkout, "o/tails")
    assert log.read_text().splitlines()[-1] == "plugin uninstall tails@tails --scope project"
    assert not (checkout / toolkit.LOCK_PATH).exists()


# --- REQ-000000504: Toolkit items that run code are installed only when confirmed ---


def test_an_item_that_runs_code_needs_confirm_code_and_nothing_is_started_without_it(registry, checkout):
    job(registry, "add_source", repo="o/skills")
    refused = job(registry, "install", "little-widget", repo="o/skills", items=["diagnosing-bugs"])
    assert refused["outcome"] == "failed" and "skills/engineering/diagnosing-bugs/scripts/hitl.sh" in refused["error"]
    assert registry.started == [] and not (checkout / ".claude").exists()

    assert job(registry, "install", "little-widget", repo="o/skills", items=["diagnosing-bugs"], confirm_code=True)["outcome"] == "succeeded"
    script = checkout / ".claude/skills/diagnosing-bugs/scripts/hitl.sh"
    assert script.read_text().startswith("#!/bin/sh") and os.access(script, os.X_OK)


def test_an_update_lists_the_changed_files_and_asks_again_for_changed_code(registry, checkout):
    job(registry, "add_source", repo="o/skills")
    job(registry, "install", "little-widget", repo="o/skills", items=["diagnosing-bugs"], confirm_code=True)
    _git(["checkout", "dev"], checkout)
    _git(["merge", "-q", "improvement/toolkit-diagnosing-bugs"], checkout)
    registry.fetcher.repos["o/skills"]["s2"] = {**SKILL_REPO, "skills/engineering/diagnosing-bugs/scripts/hitl.sh": ("#!/bin/sh\nrm -rf x\n", "100755")}

    refused = job(registry, "update", "little-widget", repo="o/skills")
    assert refused["outcome"] == "failed" and "changes code" in refused["error"]
    answer = job(registry, "update", "little-widget", repo="o/skills", confirm_code=True)
    assert answer["outcome"] == "succeeded", answer
    result = answer["result"]
    assert (result["from"], result["to"], result["changed"]) == ("s1", "s2", ["skills/engineering/diagnosing-bugs/scripts/hitl.sh"])
    assert "rm -rf" in (checkout / ".claude/skills/diagnosing-bugs/scripts/hitl.sh").read_text()
    assert json.loads((checkout / toolkit.LOCK_PATH).read_text())["sources"]["o/skills"]["sha"] == "s2"


# --- REQ-000000505: Default toolkit sources and recommendations, never installed on their own ---


def test_seeding_registers_the_four_default_sources_and_installs_nothing(registry, checkout):
    answer = job(registry, "add_source", seed=True)
    assert [a["repo"] for a in answer["result"]["added"]] == list(toolkit.DEFAULT_SOURCES)
    assert {s["repo"]: s["licence"] for s in registry.get("/v1/toolkit/sources").json()}["pbakaus/impeccable"] == "Apache-2.0"
    assert job(registry, "add_source", seed=True)["result"]["added"] == []  # already there
    assert registry.started == [] and not (checkout / ".claude").exists()


def test_recommendations_follow_the_project_kind_and_its_findings(tmp_path):
    sources = [{"repo": repo, "items": [{"id": f"skill:{n}", "name": n} for n in
                                       ("tdd", "handoff", "writing-for-agents", "ponytail-review", "impeccable")]}
               for repo in toolkit.DEFAULT_SOURCES]
    unity = tmp_path / "game"
    (unity / "ProjectSettings").mkdir(parents=True)
    (unity / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.0")
    assert toolkit.project_kind(unity, has_ui=True) == {"unity"}
    assert toolkit.recommend({"unity"}, [], [], sources) == []

    code = toolkit.recommend(toolkit.project_kind(tmp_path, has_ui=False), [], [], sources)
    assert {(r["repo"], r["item"]["name"]) for r in code} == {
        ("mattpocock/skills", "tdd"), ("mattpocock/skills", "handoff"), ("mattpocock/skills", "writing-for-agents"),
        ("DietrichGebert/ponytail", "ponytail-review")}
    ui = toolkit.recommend({"code", "ui"}, [], [{"repo": "mattpocock/skills", "items": [{"id": "skill:tdd"}]}], sources)
    assert ("pbakaus/impeccable", "impeccable") in {(r["repo"], r["item"]["name"]) for r in ui}
    assert ("mattpocock/skills", "tdd") not in {(r["repo"], r["item"]["name"]) for r in ui}  # installed already
    by_finding = toolkit.recommend(set(), [{"detector": "D1", "title": "Long sessions"}], [], sources)
    assert [(r["item"]["name"], r["why"]) for r in by_finding] == [("handoff", "usage finding: Long sessions")]


def test_the_status_route_recommends_for_a_project_without_installing(registry, checkout):
    job(registry, "add_source", seed=True)
    status = registry.get("/v1/toolkit/status/little-widget").json()
    assert status["kinds"] == ["code"] and status["installed"] == []
    assert {r["item"]["name"] for r in status["recommended"]} == {"tdd", "handoff", "diagnosing-bugs"}  # the fixture's among the defaults
    assert registry.get("/v1/toolkit/status/nope").status_code == 404


# --- REQ-000000506: Generated settings enable the plugins the toolkit lock records ---


def test_the_generated_settings_keep_the_plugins_the_lock_enables(checkout):
    (checkout / ".claude").mkdir()
    (checkout / toolkit.LOCK_PATH).write_text(json.dumps({"sources": {"o/tails": {
        "sha": "s1", "licence": "MIT", "items": {"plugin:tails": {"kind": "plugin", "plugin": "tails@tails", "files": []}}}}}))
    settings = json.loads(claude_sync.wanted_files(checkout, "little-widget", "python")[".claude/settings.json"])
    assert settings["enabledPlugins"] == {"pyright-lsp@claude-plugins-official": True, "tails@tails": True}
    with pytest.raises(ValueError, match="unknown plugin"):
        claude_templates.settings("little-widget", plugins=("not-an-lsp",))


def test_the_toolkit_tools_are_classified_reads_free_and_writes_asked():
    from common import tool_access

    assert {"workshop_toolkit_sources", "workshop_toolkit_items", "workshop_toolkit_status"} <= set(tool_access.MCP_READ_ONLY)
    assert {"workshop_toolkit_install", "workshop_toolkit_remove", "workshop_toolkit_update", "workshop_toolkit_add_source",
            "workshop_usage_draft_skill"} <= set(tool_access.MCP_ASK)
