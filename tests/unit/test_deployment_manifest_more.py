"""Additional manifest.py error-path coverage, extending
test_deployment_manifest.py's happy-path tests."""

import pytest

from deployment.manifest import ManifestError, parse_manifest


def test_parse_invalid_toml_syntax_raises():
    with pytest.raises(ManifestError, match="invalid TOML"):
        parse_manifest("[server\nname = oops")


def test_parse_missing_name_raises():
    with pytest.raises(ManifestError, match="name"):
        parse_manifest('[server]\ncommand = "x"\nport = 1\n')


def test_parse_missing_port_raises():
    with pytest.raises(ManifestError, match="port"):
        parse_manifest('[server]\nname = "x"\ncommand = "x"\n')


def test_parse_port_must_be_an_integer():
    with pytest.raises(ManifestError):
        parse_manifest('[server]\nname = "x"\ncommand = "x"\nport = "not-a-number"\n')


def test_parse_empty_name_raises():
    with pytest.raises(ManifestError):
        parse_manifest('[server]\nname = ""\ncommand = "x"\nport = 1\n')


def test_parse_rejects_negative_confirm_timeout():
    with pytest.raises(ManifestError, match="confirm_timeout_seconds"):
        parse_manifest('[server]\nname="x"\ncommand="x"\nport=1\nconfirm_timeout_seconds=-5\n')


def test_parse_rejects_non_integer_confirm_timeout():
    with pytest.raises(ManifestError):
        parse_manifest('[server]\nname="x"\ncommand="x"\nport=1\nconfirm_timeout_seconds="soon"\n')


def test_parse_rejects_non_table_tailscale():
    with pytest.raises(ManifestError, match="server.tailscale"):
        parse_manifest('[server]\nname="x"\ncommand="x"\nport=1\ntailscale = "nope"\n')


def test_parse_rejects_non_table_screenshot():
    with pytest.raises(ManifestError, match="server.screenshot"):
        parse_manifest('[server]\nname="x"\ncommand="x"\nport=1\nscreenshot = "nope"\n')


def test_parse_manifest_without_server_section_at_all_raises():
    with pytest.raises(ManifestError, match="missing \\[server\\]"):
        parse_manifest("[not_server]\nfoo = 1\n")


def test_parse_tailscale_defaults_when_only_https_port_given():
    manifest = parse_manifest('[server]\nname="x"\ncommand="x"\nport=1\n\n[server.tailscale]\nhttps_port=8443\n')
    assert manifest.tailscale.service is None
    assert manifest.tailscale.service_https_port == 443


def test_parse_screenshot_defaults_when_table_present_but_empty():
    manifest = parse_manifest('[server]\nname="x"\ncommand="x"\nport=1\n\n[server.screenshot]\n')
    assert manifest.screenshot.enabled is True
    assert manifest.screenshot.paths == ["/"]
    assert manifest.screenshot.port is None
