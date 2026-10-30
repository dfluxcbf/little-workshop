"""Follow-ups to the Claude-usage work found when checking it from a fresh session (2026-10-04)."""

import json
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from cli import main as cli_main
from common.paths import Paths
from common.shell import CommandResult
from mcp_server import tools as tools_mod
from usage import db, doctor
from usage.app import build_app

NOW = datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "usage.db")
    yield connection
    connection.close()


# --- REQ-000000352: The doctor looks for programs where the owner's sessions find them ---


class Runner:
    def __init__(self, found):
        self.found, self.calls = found, []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if argv[:2] == ["bash", "-ic"]:
            name = argv[2].rsplit(" ", 1)[-1]
            noise = "welcome from .bashrc\n"  # an interactive shell's start-up may print
            return CommandResult(0, f"{noise}/home/u/.nvm/bin/{name}\n", "") if name in self.found else CommandResult(1, noise, "")
        return CommandResult(0, "", "")


def test_a_program_on_the_interactive_shells_path_is_found():
    runner = Runner({"typescript-language-server"})
    which = doctor.login_shell_which(runner)
    assert which("typescript-language-server") == "/home/u/.nvm/bin/typescript-language-server"
    assert which("csharp-ls") is None
    assert runner.calls[0] == ["bash", "-ic", "command -v typescript-language-server"]
    assert which("rm -rf /") is None and len(runner.calls) == 2  # not a program name: never handed to the shell


def test_the_doctor_uses_the_interactive_shells_path_by_default(conn, paths):
    runner = Runner({"pyright-langserver", "typescript-language-server", "clangd", "csharp-ls", "claude"})
    report = doctor.run(conn, paths, runner=runner, settings_path=paths.config_home / "none.json")
    servers = {c["check"]: c["ok"] for c in report["checks"] if c["check"].startswith("language_server:")}
    assert servers and all(servers.values())


# --- REQ-000000353: A summary includes what arrived since the last rollup ---


def test_a_summary_rolls_up_first_when_the_last_rollup_is_older_than_a_minute(conn, paths, monkeypatch):
    app = build_app(conn, paths, projects=lambda: [], agent_usage=lambda since: [], emit=lambda *a: None, outcome_sources=lambda names: {})
    client = TestClient(app)
    def add(request_id):
        db.upsert_session(conn, "s1", seen=NOW.isoformat(), tag_project="little-meals")
        db.upsert_request(conn, {"request_id": request_id, "session_id": "s1", "ts": NOW.isoformat(), "model": "m", "cache_read_tokens": 10, "source": "otel"})
        conn.commit()
    add("r1")
    assert client.get("/v1/summary").json()["total"]["requests"] == 1  # never rolled up: rolled up now
    add("r2")
    assert client.get("/v1/summary").json()["total"]["requests"] == 1  # rolled up a moment ago: served as it is
    from usage import app as usage_app
    monkeypatch.setattr(usage_app, "SUMMARY_ROLLUP_SECONDS", 0.0)
    assert client.get("/v1/summary").json()["total"]["requests"] == 2


# --- REQ-000000354: Candidates are returned a page at a time ---


def _candidates(conn, n):
    for i in range(n):
        db.record_candidate(conn, {"signature": f"Bash(job {i})", "class": "hybrid", "score": 4, "occurrences": 10, "sessions": 3,
                                   "weekly_value": float(n - i), "median_cost": 1.0, "proposed_form": "a job",
                                   "examples": [{"project": "p", "calls": ["x"] * 5}] * 3, "checks": {"a": True}, "projects": {"p": 3},
                                   "schedule": {"frequency": "weekly"}})
    conn.commit()


def test_the_candidates_route_takes_a_limit_and_says_how_many_there_are(conn, paths):
    _candidates(conn, 30)
    client = TestClient(build_app(conn, paths, projects=lambda: [], agent_usage=lambda since: [], emit=lambda *a: None, outcome_sources=lambda names: {}))
    page = client.get("/v1/candidates", params={"limit": 5}).json()
    assert [c["signature"] for c in page] == [f"Bash(job {i})" for i in range(5)]
    assert len(client.get("/v1/candidates").json()) == 30  # the Dashboard still gets them all


def test_the_mcp_tool_returns_a_short_page_with_one_example_each(paths, monkeypatch):
    asked = []
    rows = [{"signature": f"s{i}", "examples": [{"calls": ["a"]}, {"calls": ["b"]}], "checks": {}} for i in range(3)]
    monkeypatch.setattr(tools_mod.client, "usage_candidates", lambda p, **kw: asked.append(kw) or rows)
    result = tools_mod.TOOLS_BY_NAME["workshop_usage_candidates"].handler(paths, {})
    assert asked == [{"state": "open", "limit": 10}]
    assert [len(c["examples"]) for c in result] == [1, 1, 1]
    tools_mod.TOOLS_BY_NAME["workshop_usage_candidates"].handler(paths, {"limit": 500})
    assert asked[-1]["limit"] == 50  # capped


def test_lws_usage_candidates_takes_a_limit(capsys, monkeypatch):
    asked = []
    monkeypatch.setattr(cli_main.client, "usage_candidates", lambda p, **kw: asked.append(kw) or [])
    assert cli_main.main(["usage", "candidates", "--limit", "3"]) == 0
    assert asked == [{"state": "open", "limit": 3}]
