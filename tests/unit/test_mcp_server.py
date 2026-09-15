"""The JSON-RPC 2.0 / MCP protocol surface itself — `MCPServer.handle_request`
-- kept separate from `test_mcp_tools.py` (argument translation) and
`test_mcp_client.py` (the HTTP/socket seam)."""

import json

import pytest

from common.paths import Paths
from mcp_server import client as client_mod
from mcp_server import tools as tools_mod
from mcp_server.server import MCPServer


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


@pytest.fixture
def server(paths):
    return MCPServer(paths=paths, version="9.9.9")


def test_initialize_reports_protocol_and_server_info(server):
    response = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response["result"]["serverInfo"] == {"name": "little-workshop", "version": "9.9.9"}
    assert response["result"]["protocolVersion"]
    assert response["result"]["capabilities"] == {"tools": {}}


def test_tools_list_returns_every_declared_tool(server):
    response = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {tool["name"] for tool in response["result"]["tools"]}
    assert names == {tool.name for tool in tools_mod.TOOLS}


def test_notifications_initialized_returns_nothing(server):
    assert server.handle_request({"method": "notifications/initialized"}) is None


def test_ping_returns_empty_result(server):
    response = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response["result"] == {}


def test_unknown_method_is_a_json_rpc_error(server):
    response = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "not/a/real/method"})
    assert response["error"]["code"] == -32601


def test_tools_call_unknown_tool_is_invalid_params(server):
    response = server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "not_a_tool", "arguments": {}}}
    )
    assert response["error"]["code"] == -32602


def test_tools_call_missing_required_argument_is_invalid_params(server):
    response = server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "workshop_project_get", "arguments": {}}}
    )
    assert response["error"]["code"] == -32602
    assert "name" in response["error"]["message"]


def test_tools_call_unknown_argument_is_invalid_params(server):
    response = server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "workshop_core_status", "arguments": {"bogus": True}}}
    )
    assert response["error"]["code"] == -32602
    assert "bogus" in response["error"]["message"]


def test_tools_call_success_wraps_json_payload(server, monkeypatch):
    monkeypatch.setattr(client_mod, "core_status", lambda paths: {"pillars": {"registry": {"status": "active"}}})
    response = server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "workshop_core_status", "arguments": {}}}
    )
    result = response["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["pillars"]["registry"]["status"] == "active"


def test_tools_call_pillar_unavailable_is_a_tool_error_not_a_protocol_error(server):
    # Nothing is listening on Core's socket for this paths fixture, so the
    # real client call genuinely raises PillarUnavailable.
    response = server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "workshop_core_status", "arguments": {}}}
    )
    assert "error" not in response
    result = response["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["success"] is False


def test_tools_call_job_outcome_failed_is_reported_as_tool_error(server, monkeypatch):
    monkeypatch.setattr(client_mod, "create_job", lambda paths, **fields: {"outcome": "failed", "error": "boom"})
    response = server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "workshop_project_remove", "arguments": {"name": "little-widget"}}}
    )
    result = response["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"] == "boom"


def test_notification_call_with_no_id_returns_none_even_on_error(server):
    assert server.handle_request({"method": "tools/call", "params": {"name": "nope"}}) is None
