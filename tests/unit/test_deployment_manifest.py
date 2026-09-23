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


# -- externally managed servers ------------------------------------------------------------

EXTERNAL = """
[server]
name = "little-workshop"
command = "python3 bridge.py"
port = 8790
match = "dashboard_bridge.py"
external = true
unit = "little-workshop-dashboard-bridge.service"

[server.tailscale]
https_port = 443
service = "little-workshop"
"""


def test_parse_external_manifest():
    from deployment.manifest import parse_manifest

    m = parse_manifest(EXTERNAL)
    assert m.external is True and m.unit == "little-workshop-dashboard-bridge.service" and m.port == 8790


def test_servers_are_not_external_by_default():
    from deployment.manifest import parse_manifest

    m = parse_manifest('[server]\nname = "x"\ncommand = "run"\nport = 1\n')
    assert m.external is False and m.unit is None


@pytest.mark.parametrize("extra,message", [
    ('external = "yes"', "external must be true or false"),
    ('external = true\nunit = ""', "non-empty systemd unit name"),
    ('external = true\nunit = 7', "non-empty systemd unit name"),
    ('unit = "some.service"', "only applies to an external server"),
])
def test_invalid_external_fields_are_rejected(extra, message):
    from deployment.manifest import ManifestError, parse_manifest

    with pytest.raises(ManifestError, match=message):
        parse_manifest(f'[server]\nname = "x"\ncommand = "run"\nport = 1\n{extra}\n')


def test_external_manifest_round_trips_through_render():
    from deployment.manifest import parse_manifest, render_manifest

    again = parse_manifest(render_manifest(parse_manifest(EXTERNAL)))
    assert again.external is True and again.unit == "little-workshop-dashboard-bridge.service" and again.port == 8790
    assert "external" not in render_manifest(parse_manifest('[server]\nname = "x"\ncommand = "run"\nport = 1\n'))
