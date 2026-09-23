"""core.registry — Core's in-memory pillar registry."""

from pathlib import Path

from core.registry import PillarStatus, Registry, RegistryEntry


def test_is_healthy_true_for_healthy_and_reconnected():
    registry = Registry()
    registry.set(RegistryEntry(name="a", socket_path=Path("/a.sock"), status=PillarStatus.HEALTHY))
    registry.set(RegistryEntry(name="b", socket_path=Path("/b.sock"), status=PillarStatus.RECONNECTED))
    assert registry.is_healthy("a") is True
    assert registry.is_healthy("b") is True


def test_is_healthy_false_for_other_statuses():
    registry = Registry()
    registry.set(RegistryEntry(name="a", socket_path=Path("/a.sock"), status=PillarStatus.CRASHED))
    assert registry.is_healthy("a") is False


def test_is_healthy_false_for_unknown_pillar():
    registry = Registry()
    assert registry.is_healthy("nonexistent") is False


def test_socket_for_returns_path_for_known_pillar():
    registry = Registry()
    registry.set(RegistryEntry(name="a", socket_path=Path("/a.sock")))
    assert registry.socket_for("a") == Path("/a.sock")


def test_socket_for_returns_none_for_unknown_pillar():
    registry = Registry()
    assert registry.socket_for("nonexistent") is None


def test_get_returns_entry():
    registry = Registry()
    entry = RegistryEntry(name="a", socket_path=Path("/a.sock"))
    registry.set(entry)
    assert registry.get("a") is entry


def test_get_returns_none_for_unknown_pillar():
    registry = Registry()
    assert registry.get("nonexistent") is None


def test_as_status_dict_renders_every_entry():
    registry = Registry()
    registry.set(RegistryEntry(name="a", socket_path=Path("/a.sock"), status=PillarStatus.HEALTHY, crash_count=2))
    status = registry.as_status_dict()
    assert status == {
        "a": {"status": "healthy", "socket_path": "/a.sock", "interface_version": "v1", "crash_count": 2},
    }
