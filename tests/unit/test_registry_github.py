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


# -- ensure_label: M16's bootstrap-little-project parity gap ----------------


def test_bootstrap_labels_has_all_seven_real_labels():
    names = [name for name, _, _ in github.BOOTSTRAP_LABELS]
    assert names == [
        "claude", "claude:done", "claude:failed", "claude:awaiting-finish",
        "claude:finished", "claude:skipped", "claude:queued",
        "docs", "milestone", "bug", "task",
    ]


def test_ensure_label_with_gh_uses_force_flag(monkeypatch):
    captured = {}

    def fake_run(args, capture_output, text, check):
        captured["args"] = args
        return subprocess.CompletedProcess(args, returncode=0)

    monkeypatch.setattr(github.subprocess, "run", fake_run)
    github.ensure_label_with_gh("dfluxcbf/little-widget", "claude", "7057ff", "desc")
    assert captured["args"][:4] == ["gh", "label", "create", "claude"]
    assert "--force" in captured["args"]
    assert "dfluxcbf/little-widget" in captured["args"]


def test_ensure_label_with_gh_raises_on_failure(monkeypatch):
    def fake_run(args, capture_output, text, check):
        return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(github.subprocess, "run", fake_run)
    with pytest.raises(github.GitHubError):
        github.ensure_label_with_gh("dfluxcbf/little-widget", "claude", "7057ff", "desc")


def test_ensure_label_posts_to_create_when_label_is_new(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request):
        captured["request"] = request
        return FakeResponse()

    monkeypatch.setattr(github.urllib.request, "urlopen", fake_urlopen)
    github.ensure_label("dfluxcbf/little-widget", "fake-token", "claude", "7057ff", "desc")

    assert captured["request"].get_method() == "POST"
    assert captured["request"].full_url == "https://api.github.com/repos/dfluxcbf/little-widget/labels"
    sent = json.loads(captured["request"].data)
    assert sent == {"name": "claude", "color": "7057ff", "description": "desc"}


def test_ensure_label_falls_back_to_patch_when_label_already_exists(monkeypatch):
    import io

    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request):
        calls.append(request)
        if request.get_method() == "POST":
            raise urllib.error.HTTPError("url", 422, "Unprocessable", {}, io.BytesIO(b'{"message": "already exists"}'))
        return FakeResponse()

    monkeypatch.setattr(github.urllib.request, "urlopen", fake_urlopen)
    github.ensure_label("dfluxcbf/little-widget", "fake-token", "claude", "7057ff", "desc")

    assert len(calls) == 2
    assert calls[1].get_method() == "PATCH"
    assert calls[1].full_url == "https://api.github.com/repos/dfluxcbf/little-widget/labels/claude"


def test_ensure_label_raises_on_a_non_422_create_error(monkeypatch):
    import io

    def fake_urlopen(request):
        raise urllib.error.HTTPError("url", 401, "Bad credentials", {}, io.BytesIO(b'{"message": "bad creds"}'))

    monkeypatch.setattr(github.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(github.GitHubError):
        github.ensure_label("dfluxcbf/little-widget", "fake-token", "claude", "7057ff", "desc")


def test_ensure_label_raises_when_the_patch_fallback_also_fails(monkeypatch):
    import io

    def fake_urlopen(request):
        if request.get_method() == "POST":
            raise urllib.error.HTTPError("url", 422, "Unprocessable", {}, io.BytesIO(b'{"message": "already exists"}'))
        raise urllib.error.HTTPError("url", 500, "Server Error", {}, io.BytesIO(b'{"message": "boom"}'))

    monkeypatch.setattr(github.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(github.GitHubError):
        github.ensure_label("dfluxcbf/little-widget", "fake-token", "claude", "7057ff", "desc")
