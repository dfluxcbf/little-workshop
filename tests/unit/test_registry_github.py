"""Never hits the real GitHub API — every test mocks subprocess/urllib
at this module's own boundary (per the sandbox_github_token_blocked
constraint: no real token is ever exercised in this repo's tests)."""

import json
import subprocess
import urllib.error
import urllib.request

import pytest

from registry import github


def test_gh_available_false_when_gh_not_on_path(monkeypatch):
    monkeypatch.setattr(github.shutil, "which", lambda name: None)
    assert github.gh_available() is False


def test_gh_available_false_when_not_logged_in(monkeypatch):
    monkeypatch.setattr(github.shutil, "which", lambda name: "/usr/bin/gh")

    def fake_run(args, capture_output, text, check):
        return subprocess.CompletedProcess(args, returncode=1)

    monkeypatch.setattr(github.subprocess, "run", fake_run)
    assert github.gh_available() is False


def test_gh_available_true_when_logged_in(monkeypatch):
    monkeypatch.setattr(github.shutil, "which", lambda name: "/usr/bin/gh")

    def fake_run(args, capture_output, text, check):
        return subprocess.CompletedProcess(args, returncode=0)

    monkeypatch.setattr(github.subprocess, "run", fake_run)
    assert github.gh_available() is True


def test_create_repository_with_gh_returns_full_name(monkeypatch):
    def fake_run(args, capture_output, text, check):
        assert args[:3] == ["gh", "repo", "create"]
        return subprocess.CompletedProcess(args, returncode=0, stdout="https://github.com/dfluxcbf/little-widget\n")

    monkeypatch.setattr(github.subprocess, "run", fake_run)
    result = github.create_repository_with_gh("little-widget", "dfluxcbf")
    assert result["full_name"] == "dfluxcbf/little-widget"


def test_create_repository_with_gh_includes_description_flag_when_given(monkeypatch):
    captured = {}

    def fake_run(args, capture_output, text, check):
        captured["args"] = args
        return subprocess.CompletedProcess(args, returncode=0, stdout="https://github.com/dfluxcbf/little-widget\n")

    monkeypatch.setattr(github.subprocess, "run", fake_run)
    github.create_repository_with_gh("little-widget", "dfluxcbf", description="a widget")
    assert "--description" in captured["args"]
    assert "a widget" in captured["args"]


def test_create_repository_with_gh_omits_description_flag_when_blank(monkeypatch):
    captured = {}

    def fake_run(args, capture_output, text, check):
        captured["args"] = args
        return subprocess.CompletedProcess(args, returncode=0, stdout="")

    monkeypatch.setattr(github.subprocess, "run", fake_run)
    github.create_repository_with_gh("little-widget", "dfluxcbf")
    assert "--description" not in captured["args"]


def test_create_repository_with_gh_public_flag(monkeypatch):
    captured = {}

    def fake_run(args, capture_output, text, check):
        captured["args"] = args
        return subprocess.CompletedProcess(args, returncode=0, stdout="")

    monkeypatch.setattr(github.subprocess, "run", fake_run)
    github.create_repository_with_gh("little-widget", "dfluxcbf", private=False)
    assert "--public" in captured["args"]
    assert "--private" not in captured["args"]


def test_create_repository_with_gh_raises_on_failure(monkeypatch):
    def fake_run(args, capture_output, text, check):
        return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="already exists")

    monkeypatch.setattr(github.subprocess, "run", fake_run)
    with pytest.raises(github.GitHubError):
        github.create_repository_with_gh("little-widget", "dfluxcbf")


def test_create_repository_rest_posts_expected_payload(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"full_name": "dfluxcbf/little-widget", "html_url": "https://x"}).encode()

    def fake_urlopen(request):
        captured["request"] = request
        return FakeResponse()

    monkeypatch.setattr(github.urllib.request, "urlopen", fake_urlopen)
    result = github.create_repository("little-widget", "fake-token", private=True, description="desc")

    assert result["full_name"] == "dfluxcbf/little-widget"
    sent = json.loads(captured["request"].data)
    assert sent == {"name": "little-widget", "private": True, "description": "desc", "auto_init": False}
    assert captured["request"].get_header("Authorization") == "token fake-token"


def test_create_repository_raises_github_error_on_http_error(monkeypatch):
    import io

    def fake_urlopen(request):
        raise urllib.error.HTTPError("url", 422, "Unprocessable", {}, io.BytesIO(b'{"message": "bad"}'))

    monkeypatch.setattr(github.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(github.GitHubError):
        github.create_repository("little-widget", "fake-token")
