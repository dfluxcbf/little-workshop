from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket


def test_resolve_pillar_socket_returns_none_when_core_unreachable(tmp_path):
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    assert resolve_pillar_socket(paths, "orchestration", timeout=0.5) is None
