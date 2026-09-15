import pytest

from deployment.manifest import ManifestError, parse_manifest, render_manifest


def test_parse_minimal_manifest():
    manifest = parse_manifest(
        """
[server]
name = "little-meals"
command = "lmeals serve"
port = 8765
"""
    )
    assert manifest.name == "little-meals"
    assert manifest.command == "lmeals serve"
    assert manifest.port == 8765
    assert manifest.match == "lmeals serve"  # defaults to command
    assert manifest.install == ["bazel", "run", "//:install"]
    assert manifest.tailscale is None


def test_parse_full_manifest_with_tailscale_and_env():
    manifest = parse_manifest(
        """
[server]
name = "little-meals"
command = "lmeals serve"
port = 8765
match = ".local/bin/lmeals serve"
install = ["bazel", "run", "//:install"]
ready_timeout_seconds = 60
stop_timeout_seconds = 20
confirm_timeout_seconds = 600

[server.env]
FOO = "bar"

[server.tailscale]
https_port = 443
service = "meals"
service_https_port = 443
"""
    )
    assert manifest.match == ".local/bin/lmeals serve"
    assert manifest.ready_timeout_seconds == 60
    assert manifest.confirm_timeout_seconds == 600
    assert manifest.env == {"FOO": "bar"}
    assert manifest.tailscale.service == "meals"


def test_parse_missing_server_table_raises():
    with pytest.raises(ManifestError):
        parse_manifest("")


def test_parse_missing_required_field_raises():
    with pytest.raises(ManifestError):
        parse_manifest('[server]\nname = "x"\nport = 1\n')  # no command


def test_parse_rejects_non_positive_confirm_timeout():
    with pytest.raises(ManifestError):
        parse_manifest('[server]\nname="x"\ncommand="x"\nport=1\nconfirm_timeout_seconds=0\n')


def test_render_then_parse_round_trips():
    manifest = parse_manifest(
        """
[server]
name = "little-meals"
command = "lmeals serve"
port = 8765
confirm_timeout_seconds = 600

[server.tailscale]
https_port = 443
service = "meals"
"""
    )
    rendered = render_manifest(manifest)
    reparsed = parse_manifest(rendered)
    assert reparsed == manifest


def test_parse_and_render_screenshot_config_round_trips():
    manifest = parse_manifest(
        """
[server]
name = "little-meals"
command = "lmeals serve"
port = 8765

[server.screenshot]
enabled = true
command = ["pipx", "run", "--spec", "little-meals=={version}", "lmeals", "serve", "--port", "{port}"]
paths = ["/", "/recipes"]
wait_ms = 3000
"""
    )
    assert manifest.screenshot.enabled is True
    assert manifest.screenshot.paths == ["/", "/recipes"]
    assert manifest.screenshot.wait_ms == 3000

    reparsed = parse_manifest(render_manifest(manifest))
    assert reparsed == manifest
