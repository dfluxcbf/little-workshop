import json

from agent.report import AgentResult, clear_result, load_result, render_report, result_path


def test_result_path_is_outside_any_repo(tmp_path):
    path = result_path(tmp_path, "little-meals", "run123")
    assert tmp_path in path.parents
    assert "little-meals" in path.name


def test_load_result_parses_valid_json(tmp_path):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({
        "status": "done",
        "background": "why this exists",
        "goal": "what we want",
        "technical_decisions": ["chose A over B"],
        "executed_tasks": "did the thing",
        "test_results": "42 passed",
    }))
    result = load_result(path)
    assert result.status == "done"
    assert result.sections["background"] == "why this exists"
    assert result.sections["technical_decisions"] == "- chose A over B"
    assert result.test_results == "42 passed"
    assert result.source == "result file"


def test_load_result_missing_file_returns_none(tmp_path):
    assert load_result(tmp_path / "missing.json") is None


def test_load_result_malformed_json_returns_none(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json{{{")
    assert load_result(path) is None


def test_load_result_unknown_status_degrades_to_partial(tmp_path):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"status": "something-else"}))
    result = load_result(path)
    assert result.status == "partial"


def test_load_result_missing_status_degrades_to_partial(tmp_path):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({}))
    result = load_result(path)
    assert result.status == "partial"


def test_clear_result_removes_file(tmp_path):
    path = tmp_path / "result.json"
    path.write_text("{}")
    clear_result(path)
    assert not path.exists()


def test_clear_result_missing_file_is_noop(tmp_path):
    clear_result(tmp_path / "missing.json")  # must not raise


def test_render_report_has_seven_sections():
    result = AgentResult(status="done", sections={
        "background": "bg", "goal": "goal", "technical_decisions": "td", "executed_tasks": "et",
    }, test_results="ok")
    report = render_report(
        project_id="little-meals", title="Fix the thing", intent="bug", branch="improvement/x",
        result=result, pr_url="https://github.com/x/y/pull/1",
    )
    for heading in ("Background", "Goal", "Technical decisions", "Executed tasks", "Screenshot", "Test validation results", "Pull request"):
        assert f"### {heading}" in report


def test_render_report_falls_back_to_summary_for_missing_executed_tasks():
    result = AgentResult(status="partial", sections={})
    report = render_report(
        project_id="little-meals", title="Task", intent="task", branch="feature/x",
        result=result, summary="did some stuff",
    )
    assert "did some stuff" in report


def test_render_report_marks_summary_sourced_results():
    result = AgentResult.from_summary("a quick summary", "partial")
    report = render_report(project_id="p", title="t", intent="task", branch="b", result=result)
    assert "did not leave a structured result" in report


def test_render_report_notes_missing_pr_link():
    result = AgentResult(status="done", sections={"executed_tasks": "x"})
    report = render_report(project_id="p", title="t", intent="task", branch="b", result=result, pr_url=None)
    assert "added once the branch is pushed" in report
