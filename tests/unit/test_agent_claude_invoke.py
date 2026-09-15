import json

from agent.claude_invoke import claude_argv, parse_claude_output, parse_status


def test_claude_argv_basic():
    argv = claude_argv("do the thing", user_messages=False)
    assert argv[:3] == ["claude", "-p", "do the thing"]
    assert "--dangerously-skip-permissions" in argv
    assert "--brief" not in argv


def test_claude_argv_includes_session_and_model():
    argv = claude_argv("x", session_name="lws-meals-run1", model="opus")
    assert "--remote-control" in argv and "lws-meals-run1" in argv
    assert "--model" in argv and "opus" in argv


def test_claude_argv_includes_budget():
    argv = claude_argv("x", max_budget_usd=5.0)
    assert "--max-budget-usd" in argv and "5.0" in argv


def test_parse_claude_output_plain_json():
    stdout = json.dumps({"result": "final answer", "is_error": False})
    text, is_error, raw = parse_claude_output(stdout)
    assert text == "final answer"
    assert is_error is False


def test_parse_claude_output_error():
    stdout = json.dumps({"result": "oops", "is_error": True})
    text, is_error, raw = parse_claude_output(stdout)
    assert is_error is True


def test_parse_claude_output_list_format():
    stdout = json.dumps([{"type": "system"}, {"type": "result", "result": "the answer"}])
    text, is_error, raw = parse_claude_output(stdout)
    assert text == "the answer"


def test_parse_claude_output_unparseable_falls_back_to_raw_text():
    text, is_error, raw = parse_claude_output("not json at all")
    assert text == "not json at all"
    assert is_error is False
    assert raw == {}


def test_parse_claude_output_finds_trailing_json_line():
    stdout = "some log noise\n" + json.dumps({"result": "final", "is_error": False})
    text, is_error, raw = parse_claude_output(stdout)
    assert text == "final"


def test_parse_status_done():
    assert parse_status("blah blah\nAGENT_STATUS: done") == "done"


def test_parse_status_missing_defaults_to_partial():
    assert parse_status("no marker here") == "partial"


def test_parse_status_takes_last_match():
    assert parse_status("AGENT_STATUS: partial\nmore text\nAGENT_STATUS: done") == "done"


def test_parse_status_case_insensitive():
    assert parse_status("agent_status: DONE") == "done"
