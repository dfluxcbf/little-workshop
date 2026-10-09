"""Asset review: the conventions of `common.assets`, the agent pillar's asset endpoints, the Registry's
`project.configure_assets` Job, the Dashboard's Assets pages and the per-project asset-review MCP server."""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from agent import db as agent_db
from agent.app import build_app as build_agent_app
from asset_mcp import runner
from asset_mcp.server import AssetServer
from common import assets as conv
from common.paths import Paths
from dashboard import app as dashboard_module
from dashboard import client as dashboard_client
from registry import db as registry_db
from registry.app import build_app as build_registry_app


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "game"
    folder = root / "art"
    (folder / "house" / "previews").mkdir(parents=True)
    (folder / "house" / "house.blend").write_bytes(b"BLEND")
    (folder / "house" / "previews" / "front.png").write_bytes(b"\x89PNG")
    (folder / "house" / "NOTES.md").write_text("# House\n\nNotes.\n", encoding="utf-8")
    (folder / "props").mkdir()
    (folder / "props" / "barrel.glb").write_bytes(b"glTF")
    (folder / "cache").mkdir()
    (root / "secret.txt").write_text("outside", encoding="utf-8")
    return root


def _bundle(blend: Path, *, mtime_offset: float = 0.0, views=("front", "top"), model=True) -> Path:
    folder = conv.bundle_dir(blend)
    (folder / "textures").mkdir(parents=True)
    if model:
        (folder / conv.MODEL_NAME).write_bytes(b"glTF")
    for name in views:
        (folder / conv.view_file(name)).write_bytes(b"\x89PNG")
    (folder / "uv_layout.png").write_bytes(b"\x89PNG")
    (folder / "textures" / "Base.png").write_bytes(b"\x89PNG")
    (folder / conv.MANIFEST_NAME).write_text(json.dumps({"source_mtime": blend.stat().st_mtime + mtime_offset, "generated_at": "now", "errors": []}))
    return folder


# --- REQ-000000427: Only the visible asset folder and its subfolders are listed and served ---


def test_the_folder_is_resolved_inside_the_checkout_and_must_exist(checkout):
    assert conv.asset_root(checkout, "art") == (checkout / "art").resolve()
    for bad, status in ((None, 404), ("missing", 404), ("../elsewhere", 400)):
        with pytest.raises(conv.AssetError) as err:
            conv.asset_root(checkout, bad)
        assert err.value.status == status


def test_paths_outside_the_folder_are_refused(checkout):
    root = conv.asset_root(checkout, "art")
    (root / "link").symlink_to(checkout / "secret.txt")
    for bad in ("../secret.txt", "/etc/passwd", "house/../../secret.txt", "link", "", "\\windows"):
        with pytest.raises(conv.AssetError):
            conv.resolve_inside(root, bad)
    assert conv.resolve_inside(root, "house/house.blend") == root / "house" / "house.blend"


def test_a_comment_file_is_recognised_by_a_sibling_asset_of_the_same_name(checkout):
    house = checkout / "art" / "house"
    (house / "house.md").write_text("comment", encoding="utf-8")
    assert conv.is_comment(house / "house.md") is True
    assert conv.is_comment(house / "NOTES.md") is False
    assert conv.comment_file(house / "house.blend") == house / "house.md"


def test_bundle_state_follows_the_blend_file(checkout):
    blend = checkout / "art" / "house" / "house.blend"
    assert conv.bundle_state(blend) == "none"
    _bundle(blend)
    assert conv.bundle_state(blend) == "ready"
    os.utime(blend, (time.time() + 100, time.time() + 100))
    assert conv.bundle_state(blend) == "stale"


@pytest.fixture
def agent(tmp_path, checkout, monkeypatch):
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    row = {"name": "proj", "path": str(checkout), "repo_url": None, "assets_dir": "art"}
    monkeypatch.setattr("agent.app.resolve_project", lambda p, pid, **kw: row)
    conn = agent_db.connect(paths.pillar_db_path("agent"))
    return TestClient(build_agent_app(paths, conn)), checkout, row


def _names(nodes):
    return [(n["name"], _names(n["children"]) if n["type"] == "dir" else n["kind"]) for n in nodes]


def test_the_tree_mirrors_the_folder_and_hides_bundles_and_empty_folders(agent):
    client, checkout, _ = agent
    blend = checkout / "art" / "house" / "house.blend"
    _bundle(blend)
    (checkout / "art" / "house" / "house.md").write_text("looks good", encoding="utf-8")
    tree = client.get("/v1/assets/proj").json()
    assert tree["assets_dir"] == "art"
    assert _names(tree["tree"]) == [
        ("house", [("previews", [("front.png", "image")]), ("house.blend", "blend"), ("NOTES.md", "doc")]),
        ("props", [("barrel.glb", "model")]),
    ]
    house = tree["tree"][0]["children"][1]
    assert house["bundle"] == "ready" and house["commented"] is True


def test_a_project_without_an_asset_folder_has_no_assets(agent):
    client, _, row = agent
    row["assets_dir"] = None
    assert client.get("/v1/assets/proj").status_code == 404
    assert client.get("/v1/docs/proj").json()["assets_dir"] is None


def test_details_of_a_blend_list_its_bundle_files(agent):
    client, checkout, _ = agent
    _bundle(checkout / "art" / "house" / "house.blend")
    body = client.get("/v1/assets/proj/asset", params={"path": "house/house.blend"}).json()
    bundle = body["bundle"]
    assert bundle["state"] == "ready" and bundle["model"] == "house/house.view/model.glb"
    assert [v["name"] for v in bundle["views"]] == ["front", "top"]
    assert bundle["uv"][0]["path"] == "house/house.view/uv_layout.png"
    assert bundle["textures"][0]["path"] == "house/house.view/textures/Base.png"
    assert body["comment"] == "" and body["comment_path"] == "house/house.md"


def test_files_are_served_with_their_type_and_nothing_else(agent):
    client, checkout, _ = agent
    _bundle(checkout / "art" / "house" / "house.blend")
    glb = client.get("/v1/assets/proj/file", params={"path": "house/house.view/model.glb"})
    assert glb.content == b"glTF" and glb.headers["content-type"] == "model/gltf-binary"
    assert client.get("/v1/assets/proj/file", params={"path": "house/previews/front.png"}).headers["content-type"] == "image/png"
    for bad in ("../secret.txt", "/etc/passwd", "house/missing.png"):
        assert client.get("/v1/assets/proj/file", params={"path": bad}).status_code in (400, 404)


# --- REQ-000000429: A comment on an asset is a Markdown file beside it ---


def test_a_comment_is_written_beside_the_asset_and_read_back(agent):
    client, checkout, _ = agent
    saved = client.put("/v1/assets/proj/comment", json={"path": "house/house.blend", "body": "Roof too flat"}).json()
    assert saved == {"path": "house/house.blend", "comment_path": "house/house.md", "saved": True}
    assert (checkout / "art" / "house" / "house.md").read_text(encoding="utf-8") == "Roof too flat\n"
    assert client.get("/v1/assets/proj/asset", params={"path": "house/house.blend"}).json()["comment"] == "Roof too flat\n"
    # the comment file is not listed as a document of its own
    names = [c["name"] for c in client.get("/v1/assets/proj").json()["tree"][0]["children"]]
    assert "house.md" not in names
    assert client.put("/v1/assets/proj/comment", json={"path": "house/house.blend", "body": "  \n"}).json()["saved"] is False
    assert not (checkout / "art" / "house" / "house.md").exists()


def test_a_comment_is_refused_outside_the_folder_and_on_a_document(agent):
    client, *_ = agent
    for path in ("../secret.txt", "/etc/hosts", "house/NOTES.md", "house/missing.blend"):
        assert client.put("/v1/assets/proj/comment", json={"path": path, "body": "x"}).status_code in (400, 404)


# --- REQ-000000426: A project chooses its visible asset folder and gets the asset-review MCP server ---


@pytest.fixture
def registry(tmp_path, checkout):
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    conn = registry_db.connect(tmp_path / "registry.db")
    from registry.models import Project

    registry_db.insert_project(conn, Project(name="proj", path=str(checkout)))
    return TestClient(build_registry_app(conn, paths)), checkout


def _configure(client, **params):
    return client.post("/v1/jobs/project.configure_assets", json={"job_id": "j", "project_id": "proj", "params": params}).json()


def test_configuring_a_folder_records_it_and_declares_the_mcp_server(registry):
    client, checkout = registry
    (checkout / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    body = _configure(client, assets_dir="art/")
    assert body["outcome"] == "succeeded" and body["result"]["assets_dir"] == "art"
    assert client.get("/v1/projects/proj").json()["assets_dir"] == "art"
    servers = json.loads((checkout / ".mcp.json").read_text())["mcpServers"]
    assert servers["other"] == {"command": "x"}
    assert servers["asset-review"] == {"command": "lws", "args": ["asset-mcp", "serve", "--project-id", "proj"]}


def test_a_folder_outside_or_missing_is_refused_and_nothing_changes(registry):
    client, checkout = registry
    for bad in ("nope", "../game2", ".."):
        assert _configure(client, assets_dir=bad)["outcome"] == "failed"
    assert client.get("/v1/projects/proj").json()["assets_dir"] is None
    assert not (checkout / ".mcp.json").exists()


def test_emptying_the_folder_turns_asset_review_off_and_removes_only_its_server(registry):
    client, checkout = registry
    (checkout / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    _configure(client, assets_dir="art")
    assert _configure(client, assets_dir="")["result"]["assets_dir"] is None
    assert client.get("/v1/projects/proj").json()["assets_dir"] is None
    assert json.loads((checkout / ".mcp.json").read_text()) == {"mcpServers": {"other": {"command": "x"}}}
    (checkout / ".mcp.json").unlink()
    _configure(client, assets_dir="art")
    _configure(client, assets_dir="")
    assert not (checkout / ".mcp.json").exists()


def test_the_mcp_server_can_be_left_out(registry):
    client, checkout = registry
    assert _configure(client, assets_dir="art", mcp=False)["result"]["mcp_server"] is None
    assert not (checkout / ".mcp.json").exists()


def test_the_registry_database_gains_the_column_on_an_old_database(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("CREATE TABLE projects (name TEXT PRIMARY KEY, path TEXT NOT NULL, repo_url TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);"
                      "INSERT INTO projects VALUES ('p', '/x', NULL, 'active', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');")
    old.commit()
    old.close()
    conn = registry_db.connect(path)
    assert registry_db.get_project(conn, "p").assets_dir is None


# --- REQ-000000428: The Dashboard has an Assets page for projects with a visible asset folder ---


@pytest.fixture
def dashboard(tmp_path, monkeypatch, agent):
    _agent_client, checkout, _row = agent
    monkeypatch.setattr(dashboard_client, "list_projects", lambda paths, status=None: [{"name": "proj", "status": "active"}])
    monkeypatch.setattr(dashboard_client, "policy_check_results", lambda paths, **kw: [])
    monkeypatch.setattr(dashboard_client, "policy_list", lambda paths, topic=None, **kw: [])
    monkeypatch.setattr(dashboard_client, "list_incidents", lambda paths, status=None: [])
    backing = _agent_client

    def call(paths, pillar, method, path, **kw):
        response = backing.request(method, path, **kw)
        if response.status_code >= 400:
            raise dashboard_client.Refused(response.status_code, response.json().get("detail", ""))
        return response

    monkeypatch.setattr(dashboard_client, "_call", call)
    return TestClient(dashboard_module.build_app(Paths(config_home=tmp_path / "c", state_home=tmp_path / "s"))), checkout


def test_the_docs_page_shows_an_assets_button_only_with_an_asset_folder(dashboard, monkeypatch):
    client, _ = dashboard
    overview = {"project_id": "proj", "branch": "dev", "branches": ["dev"], "dirty": False, "files": [], "assets_dir": "art"}
    for name in ("scheduled_tasks", "doc_updates"):
        monkeypatch.setattr(dashboard_client, name, lambda *a, **k: [])
    monkeypatch.setattr(dashboard_client, "list_jobs", lambda *a, **k: [])
    monkeypatch.setattr(dashboard_client, "docs_overview", lambda paths, project: overview)
    text = client.get("/docs/proj").text
    assert 'href="/assets/proj"' in text and ">Assets<" in text
    assert text.index(">Assets<") < text.index("Documents</h2>")
    monkeypatch.setattr(dashboard_client, "docs_overview", lambda paths, project: {**overview, "assets_dir": None})
    assert "/assets/proj" not in client.get("/docs/proj").text


def test_the_assets_page_shows_the_tree_and_the_selected_blend_with_model_views_uv_and_textures(dashboard):
    client, checkout = dashboard
    _bundle(checkout / "art" / "house" / "house.blend")
    text = client.get("/assets/proj", params={"path": "house/house.blend"}).text
    assert "/assets/proj?path=props/barrel.glb" in text and "NOTES.md" in text and "house.view" not in text.split("as-main")[0]
    assert "<model-viewer" in text and "/assets/proj/file?path=house/house.view/model.glb" in text
    assert "view_front.png" in text and "view_top.png" in text
    assert "uv_layout.png" in text and "textures/Base.png" in text


def test_a_blend_without_a_bundle_says_how_to_export_it(dashboard):
    client, _ = dashboard
    text = client.get("/assets/proj", params={"path": "house/house.blend"}).text
    assert "No 3D model yet" in text and "asset_export" in text and "<model-viewer" not in text


def test_models_images_and_documents_open_as_they_are(dashboard):
    client, _ = dashboard
    assert "<model-viewer" in client.get("/assets/proj", params={"path": "props/barrel.glb"}).text
    assert 'class="as-stage as-stage-img"' in client.get("/assets/proj", params={"path": "house/previews/front.png"}).text.replace("panel ", "")
    assert "<h1" in client.get("/assets/proj", params={"path": "house/NOTES.md"}).text and "Notes." in client.get("/assets/proj", params={"path": "house/NOTES.md"}).text


def test_asset_files_are_served_without_scripts_and_outside_paths_refused(dashboard):
    client, _ = dashboard
    ok = client.get("/assets/proj/file", params={"path": "house/previews/front.png"})
    assert ok.status_code == 200 and ok.headers["content-security-policy"] == "script-src 'none'"
    assert client.get("/assets/proj/file", params={"path": "../secret.txt"}).status_code == 400


def test_an_unconfigured_project_shows_the_reason_instead_of_a_tree(dashboard, agent):
    client, _ = dashboard
    agent[2]["assets_dir"] = None
    assert "no asset folder is configured" in client.get("/assets/proj").text


# --- REQ-000000429: A comment on an asset is a Markdown file beside it (the page) ---


def test_the_page_saves_a_comment_and_shows_it_again(dashboard):
    client, checkout = dashboard
    response = client.post("/assets/proj/comment", data={"path": "house/house.blend", "body": "More windows\r\nPlease"}, follow_redirects=False)
    assert response.status_code == 303 and unquote(response.headers["location"]).endswith("path=house/house.blend&saved=1")
    assert (checkout / "art" / "house" / "house.md").read_text(encoding="utf-8") == "More windows\nPlease\n"
    text = client.get("/assets/proj", params={"path": "house/house.blend", "saved": "1"}).text
    assert "More windows" in text and "house/house.md" in text and "Saved." in text


def test_the_page_refuses_a_comment_outside_the_folder(dashboard):
    client, checkout = dashboard
    response = client.post("/assets/proj/comment", data={"path": "../secret.txt", "body": "x"}, follow_redirects=False)
    assert "Could not save the comment" in unquote(response.headers["location"])
    assert (checkout / "secret.txt").read_text(encoding="utf-8") == "outside"


# --- REQ-000000430: The asset-review MCP server exports a Blender file's model and previews ---


def _call(server, name, **arguments):
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
    if "error" in response:
        return response["error"]
    return {**json.loads(response["result"]["content"][0]["text"]), "_error": response["result"]["isError"]}


@pytest.fixture
def mcp(tmp_path, checkout, monkeypatch):
    monkeypatch.setattr("asset_mcp.server.resolve_project", lambda paths, pid: {"name": pid, "path": str(checkout), "assets_dir": "art"})
    return AssetServer(Paths(config_home=tmp_path / "c", state_home=tmp_path / "s"), "proj"), checkout


def test_the_server_declares_its_three_tools(mcp):
    server, _ = mcp
    tools = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in tools] == ["asset_list", "asset_export", "asset_status"]
    assert server.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize"})["result"]["serverInfo"]["name"] == "asset-review"


def test_asset_list_gives_each_blend_file_and_its_bundle_state(mcp):
    server, checkout = mcp
    blend = checkout / "art" / "house" / "house.blend"
    assert _call(server, "asset_list")["blend_files"] == [{"path": "house/house.blend", "bundle": "none"}]
    _bundle(blend)
    assert _call(server, "asset_list")["blend_files"] == [{"path": "house/house.blend", "bundle": "ready"}]


def test_asset_export_runs_blender_on_the_file_and_reports_the_bundle(mcp, monkeypatch):
    server, checkout = mcp
    seen = {}

    def fake(blend, **kw):
        seen.update(blend=blend, **kw)
        _bundle(blend)
        return {"ok": True, "errors": [], "objects": 3}

    monkeypatch.setattr(runner, "export_bundle", fake)
    result = _call(server, "asset_export", path="house/house.blend", collection="Export", resolution=512)
    assert seen == {"blend": (checkout / "art" / "house" / "house.blend").resolve(), "collection": "Export", "resolution": 512}
    assert result["ok"] is True and result["bundle"] == "house/house.view" and result["state"] == "ready" and result["_error"] is False
    status = _call(server, "asset_status", path="house/house.blend")
    assert "model.glb" in status["files"] and "view_front.png" in status["files"] and "uv_layout.png" in status["files"]


def test_asset_export_refuses_paths_outside_the_folder_and_other_files(mcp):
    server, checkout = mcp
    (checkout / "outside.blend").write_bytes(b"x")
    for path in ("../outside.blend", "/etc/passwd", "props/barrel.glb", "house/missing.blend"):
        result = _call(server, "asset_export", path=path)
        assert result["_error"] is True and result["success"] is False


def test_the_server_rejects_unknown_arguments_and_tools(mcp):
    server, _ = mcp
    assert "unknown" in _call(server, "asset_export", path="a.blend", frames=3)["message"]
    assert "Unknown tool" in _call(server, "nope")["message"]


def test_a_failed_export_is_reported_as_an_error(mcp, monkeypatch):
    server, _ = mcp
    monkeypatch.setattr(runner, "export_bundle", lambda blend, **kw: {"ok": False, "errors": ["model: boom"], "objects": 1})
    result = _call(server, "asset_export", path="house/house.blend")
    assert result["_error"] is True and result["errors"] == ["model: boom"]


def test_the_runner_hands_blender_the_script_and_arguments_and_cleans_up(tmp_path, monkeypatch):
    blend = tmp_path / "house.blend"
    blend.write_bytes(b"BLEND")
    fake = tmp_path / "fake_blender"
    fake.write_text(
        f"#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\n"
        "args = Path(sys.argv[sys.argv.index('--') + 1])\n"
        "config = json.loads(args.read_text())\n"
        "assert Path(sys.argv[sys.argv.index('--python') + 1]).name == 'blender_export.py'\n"
        "bundle = Path(config['bundle']); (bundle / 'model.glb').write_bytes(b'glTF')\n"
        "(bundle / 'manifest.json').write_text('{}')\n"
        "args.with_name('result.json').write_text(json.dumps({'ok': True, 'errors': [], 'objects': 2, 'seen': [config['collection'], config['resolution']]}))\n"
        "print('Blender quit')\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("BLENDER_PATH", str(fake))
    stale = conv.bundle_dir(blend)
    stale.mkdir()
    (stale / "view_front.png").write_bytes(b"old")
    result = runner.export_bundle(blend, collection="Export", resolution=256)
    assert result["ok"] is True and result["seen"] == ["Export", 256] and result["objects"] == 2
    assert (stale / "model.glb").is_file() and not (stale / "view_front.png").exists() and not (stale / ".work").exists()


def test_the_runner_reports_a_blender_that_leaves_no_result(tmp_path, monkeypatch):
    blend = tmp_path / "house.blend"
    blend.write_bytes(b"BLEND")
    fake = tmp_path / "fake_blender"
    fake.write_text(f"#!{sys.executable}\nprint('segfault')\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("BLENDER_PATH", str(fake))
    with pytest.raises(runner.BlenderError, match="without a result"):
        runner.export_bundle(blend)
    monkeypatch.setenv("BLENDER_PATH", str(tmp_path / "missing"))
    with pytest.raises(runner.BlenderError, match="not a file"):
        runner.find_blender()


def test_the_ten_views_are_the_four_sides_top_bottom_and_four_orthographic_corners():
    assert list(conv.VIEWS) == ["front", "right", "back", "left", "top", "bottom", "ortho_top_1", "ortho_top_2", "ortho_bottom_1", "ortho_bottom_2"]
    assert all(conv.VIEWS[n][1] > 0 for n in ("ortho_top_1", "ortho_top_2")) and all(conv.VIEWS[n][1] < 0 for n in ("ortho_bottom_1", "ortho_bottom_2"))
