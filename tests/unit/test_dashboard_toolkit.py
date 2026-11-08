"""The Dashboard's Toolkit page (System > Toolkit) and the Claude usage page's "Draft as a skill"."""

from __future__ import annotations

import threading
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from dashboard import app as app_module
from dashboard import client

SOURCE = {"repo": "mattpocock/skills", "sha": "4ae2317ddb22b046e26455e4f9c9b3aaaf62212d", "licence": "MIT",
          "updated_at": "2026-11-07T10:00:00+00:00",
          "items": [{"id": "skill:tdd", "kind": "skill", "name": "tdd", "description": "Test first.", "runs_code": []},
                    {"id": "skill:diagnosing-bugs", "kind": "skill", "name": "diagnosing-bugs", "description": "Bugs.",
                     "runs_code": ["skills/engineering/diagnosing-bugs/scripts/hitl.sh"]}]}


@pytest.fixture
def test_client(tmp_path, monkeypatch):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "little-meals", "status": "active"}])
    return TestClient(app_module.build_app(Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")))


# --- REQ-000000505: Default toolkit sources and recommendations, never installed on their own ---


def test_the_toolkit_page_shows_sources_items_code_and_a_projects_toolkit(test_client, monkeypatch):
    status = {"project_id": "little-meals", "kinds": ["code"],
              "installed": [{"repo": "mattpocock/skills", "sha": SOURCE["sha"], "licence": "MIT",
                             "items": [{"id": "skill:tdd", "kind": "skill", "files": [], "missing": []}]}],
              "recommended": [{"repo": "mattpocock/skills", "item": SOURCE["items"][1], "why": "default for code projects"}]}
    monkeypatch.setattr(client, "toolkit_sources", lambda paths: [SOURCE])
    monkeypatch.setattr(client, "toolkit_status", lambda paths, project: status)
    page = test_client.get("/toolkit", params={"project": "little-meals"}).text
    assert "mattpocock/skills" in page and "MIT" in page and "4ae2317ddb22" in page and "Bugs." in page
    assert 'class="tk-code"' in page and "hitl.sh" in page and 'name="confirm_code" value="1"' in page
    assert "Recommended for little-meals" in page and "default for code projects" in page
    assert 'data-panel="installed"' in page and "/toolkit/remove" in page and "/toolkit/update" in page


def test_without_sources_the_page_offers_the_defaults(test_client, monkeypatch):
    monkeypatch.setattr(client, "toolkit_sources", lambda paths: [])
    page = test_client.get("/toolkit").text
    assert "Add the default sources" in page and 'action="/toolkit/seed"' in page


def test_toolkit_actions_start_registry_jobs_in_the_background(test_client, monkeypatch):
    submitted, jobs = threading.Event(), []

    def fake_create_job(paths, timeout=30.0, **fields):
        jobs.append(fields)
        submitted.set()
        return {"status": "succeeded"}

    monkeypatch.setattr(client, "create_job", fake_create_job)
    answer = test_client.post("/toolkit/install", data={"repo": "mattpocock/skills", "project": "little-meals",
                                                        "items": "skill:diagnosing-bugs", "confirm_code": "1"}, follow_redirects=False)
    assert answer.status_code == 303 and "integration branch" in unquote(answer.headers["location"])
    assert submitted.wait(timeout=5)
    assert (jobs[0]["type"], jobs[0]["project_id"], jobs[0]["params"]) == (
        "registry.toolkit_install", "little-meals", {"repo": "mattpocock/skills", "items": ["skill:diagnosing-bugs"], "confirm_code": True})


# --- REQ-000000508: Draft as a skill starts a fixed agent task ---


def test_draft_as_a_skill_starts_the_usage_job_for_the_chosen_project(test_client, monkeypatch):
    jobs = []
    monkeypatch.setattr(client, "create_job", lambda paths, **kw: jobs.append(kw) or {
        "status": "succeeded", "result": {"skill": "deployment-redeploy-pillar", "project_id": "little-meals"}})
    answer = test_client.post("/usage/draft_skill", data={"kind": "candidate", "row_id": "c1", "project": "little-meals"},
                              follow_redirects=False)
    assert (jobs[0]["type"], jobs[0]["target_pillar"], jobs[0]["project_id"], jobs[0]["params"]) == (
        "usage.draft_skill", "usage", "little-meals", {"candidate_id": "c1"})
    assert "Drafting skill deployment-redeploy-pillar in little-meals" in unquote(answer.headers["location"])


# --- REQ-000000533: Toolkit items install together in one commit straight on the integration branch ---


def test_the_page_offers_a_checkbox_per_item_and_one_install_for_the_selection(test_client, monkeypatch):
    status = {"project_id": "little-meals", "kinds": ["code"], "branch": "dev", "installed": [],
              "recommended": [{"repo": "mattpocock/skills", "item": SOURCE["items"][1], "why": "default for code projects"}]}
    monkeypatch.setattr(client, "toolkit_sources", lambda paths: [SOURCE])
    monkeypatch.setattr(client, "toolkit_status", lambda paths, project: status)
    page = test_client.get("/toolkit", params={"project": "little-meals"}).text
    assert page.count('name="pick" value="mattpocock/skills skill:tdd"') == 1
    assert page.count('name="pick" value="mattpocock/skills skill:diagnosing-bugs"') == 1  # once, in its source
    assert 'data-value="mattpocock/skills skill:diagnosing-bugs"' in page  # the recommendation selects that box
    assert 'data-code="skills/engineering/diagnosing-bugs/scripts/hitl.sh"' in page
    assert 'id="tk-install"' in page and "Install selected into little-meals" in page and "Select all recommended" in page
    assert "improvement/toolkit" not in page
    # The selection's script sits once in the page body (not in <title>) and keeps its own sent-flag: base.html's
    # handler cancels a form whose dataset.sent is already set, which stopped every "Install selected" from posting.
    assert "<title>Toolkit — little-workshop</title>" in page and page.count("getElementById('tk-install')") == 1
    script = page[page.index("getElementById('tk-install')"):]
    assert "dataset.sent" not in script[:script.index("</script>")]


def test_the_selected_items_of_every_source_go_in_one_install_job(test_client, monkeypatch):
    submitted, jobs = threading.Event(), []

    def fake_create_job(paths, timeout=30.0, **fields):
        jobs.append(fields)
        submitted.set()
        return {"status": "succeeded"}

    monkeypatch.setattr(client, "create_job", fake_create_job)
    answer = test_client.post("/toolkit/install", data={"project": "little-meals", "confirm_code": "1", "pick": [
        "mattpocock/skills skill:tdd", "pbakaus/impeccable skill:impeccable", "mattpocock/skills skill:handoff",
        "mattpocock/skills skill:tdd"]}, follow_redirects=False)
    assert answer.status_code == 303 and "install 3 items" in unquote(answer.headers["location"])
    assert submitted.wait(timeout=5) and len(jobs) == 1
    assert (jobs[0]["type"], jobs[0]["project_id"], jobs[0]["params"]) == ("registry.toolkit_install", "little-meals", {
        "selections": [{"repo": "mattpocock/skills", "items": ["skill:tdd", "skill:handoff"]},
                       {"repo": "pbakaus/impeccable", "items": ["skill:impeccable"]}],
        "confirm_code": True})


def test_an_install_with_nothing_selected_starts_no_job(test_client, monkeypatch):
    jobs = []
    monkeypatch.setattr(client, "create_job", lambda paths, timeout=30.0, **fields: jobs.append(fields))
    answer = test_client.post("/toolkit/install", data={"project": "little-meals"}, follow_redirects=False)
    assert answer.status_code == 303 and "Select at least one item" in unquote(answer.headers["location"]) and jobs == []


# --- REQ-000000535: The toolkit installs MCP servers into a project's .mcp.json ---


def test_the_page_lists_mcp_servers_and_offers_the_add_form(test_client, monkeypatch):
    lab = {"repo": "projects.blender.org/lab/blender_mcp", "sha": "v1.0.3", "licence": "GPL-3.0", "default_branch": "",
           "updated_at": "2026-11-08T10:00:00+00:00",
           "items": [{"id": "mcp:blender-lab", "kind": "mcp", "name": "blender-lab", "description": "Blender Lab",
                      "runs_code": ["MCP server runs: uvx blender-mcp"]}]}
    monkeypatch.setattr(client, "toolkit_sources", lambda paths: [lab])
    monkeypatch.setattr(client, "toolkit_status", lambda paths, project: {"project_id": "little-meals", "kinds": ["code"],
                                                                          "branch": "dev", "installed": [], "recommended": []})
    page = test_client.get("/toolkit", params={"project": "little-meals"}).text
    assert 'value="projects.blender.org/lab/blender_mcp mcp:blender-lab"' in page and 'class="tk-kind tk-kind-mcp"' in page
    assert 'data-code="MCP server runs: uvx blender-mcp"' in page
    assert "pinned at v1.0.3" in page and "Re-pin to newest" not in page
    assert 'action="/toolkit/add_mcp"' in page and "Add an MCP server" in page


def test_adding_an_mcp_server_starts_one_add_source_job(test_client, monkeypatch):
    submitted, jobs = threading.Event(), []

    def fake_create_job(paths, timeout=30.0, **fields):
        jobs.append(fields)
        submitted.set()
        return {"status": "succeeded"}

    monkeypatch.setattr(client, "create_job", fake_create_job)
    answer = test_client.post("/toolkit/add_mcp", data={
        "repo": "ahujasid/blender-mcp", "mcp_name": "blender", "mcp_command": "uvx mcp-for-blender --flag 'a b'",
        "mcp_env": "DISABLE_TELEMETRY=true\n", "licence": "", "version": ""}, follow_redirects=False)
    assert answer.status_code == 303 and "register MCP server blender" in unquote(answer.headers["location"])
    assert submitted.wait(timeout=5)
    assert (jobs[0]["type"], jobs[0]["params"]) == ("registry.toolkit_add_source", {"repo": "ahujasid/blender-mcp", "mcp_servers": {
        "blender": {"command": "uvx", "args": ["mcp-for-blender", "--flag", "a b"], "env": {"DISABLE_TELEMETRY": "true"}}}})
    refused = test_client.post("/toolkit/add_mcp", data={"repo": "o/r", "mcp_name": "x", "mcp_command": ""}, follow_redirects=False)
    assert "Give the repository" in unquote(refused.headers["location"]) and len(jobs) == 1


# --- REQ-000000537: The Toolkit page lists only what is left to install, folds each source and selects whole sources ---

TWO = {"repo": "o/two", "sha": "b" * 40, "licence": "MIT", "updated_at": "2026-11-07T10:00:00+00:00", "default_branch": "main",
       "items": [{"id": "skill:a", "kind": "skill", "name": "a", "description": "A.", "runs_code": []},
                 {"id": "plugin:two", "kind": "plugin", "name": "two", "description": "Bundles a.", "runs_code": []}]}


def _page(test_client, monkeypatch, installed, recommended=()):
    status = {"project_id": "little-meals", "kinds": ["code"], "branch": "dev", "installed": installed, "recommended": list(recommended)}
    monkeypatch.setattr(client, "toolkit_sources", lambda paths: [SOURCE, TWO])
    monkeypatch.setattr(client, "toolkit_status", lambda paths, project: status)
    return test_client.get("/toolkit", params={"project": "little-meals"}).text


def test_installed_items_leave_the_available_list_and_fully_installed_sources_fold_away(test_client, monkeypatch):
    installed = [{"repo": "mattpocock/skills", "sha": SOURCE["sha"], "licence": "MIT",
                  "items": [{"id": "skill:tdd", "kind": "skill", "files": [], "missing": []},
                            {"id": "skill:diagnosing-bugs", "kind": "skill", "files": [], "missing": []}]}]
    page = _page(test_client, monkeypatch, installed)
    assert 'name="pick" value="mattpocock/skills skill:tdd"' not in page and 'value="o/two skill:a"' in page
    assert "Everything installed from:" in page and "<strong>2</strong> installed" in page and "<strong>2</strong> available" in page
    assert "Recommended for" not in page  # nothing left to recommend: no panel
    available = page[page.index('data-panel="available"'):page.index('data-panel="installed"')]
    assert 'data-repo="mattpocock/skills"' not in available and 'data-repo="o/two"' in available
    installed_panel = page[page.index('data-panel="installed"'):page.index('data-panel="sources"')]
    assert installed_panel.count('class="tk-item tk-item-installed"') == 2 and "Test first." in installed_panel  # the registered description
    assert "Remove all" in installed_panel


def test_each_source_can_be_selected_whole_and_the_page_filters_by_text_and_kind(test_client, monkeypatch):
    page = _page(test_client, monkeypatch, [])
    assert page.count('class="tk-group-pick"') == 2 and 'id="tk-search"' in page
    assert all(f'data-kind="{k}"' in page for k in ("all", "skill", "agent", "command", "plugin", "mcp"))
    assert 'data-tab="available"' in page and 'data-tab="installed"' in page and 'data-tab="sources"' in page
    script = page[page.index("tk-group-pick')"):]
    assert "bundles" in script  # a source's own plugin is left out of a whole-source selection


def test_a_source_behind_its_pin_says_an_update_is_available(test_client, monkeypatch):
    installed = [{"repo": "o/two", "sha": "a" * 40, "licence": "MIT", "items": [{"id": "skill:a", "kind": "skill", "files": [], "missing": []}]}]
    page = _page(test_client, monkeypatch, installed)
    assert "update available" in page and "with updates" in page
