import pytest

from version.stages import GitflowVersion, bump_dev, bump_minor, bump_patch, bump_rc, normalize_to_stable


def test_parse_stable():
    v = GitflowVersion.parse("1.2.3")
    assert (v.major, v.minor, v.patch, v.prerelease) == (1, 2, 3, None)
    assert str(v) == "1.2.3"


def test_parse_dev_and_rc():
    dev = GitflowVersion.parse("1.2.3-dev.4")
    assert dev.is_dev() and dev.prerelease_num == 4
    rc = GitflowVersion.parse("1.2.3-rc.2")
    assert rc.is_rc() and rc.prerelease_num == 2
    assert rc.rc_tag() == "v1.2.3-rc.2"


def test_parse_invalid_raises():
    with pytest.raises(ValueError):
        GitflowVersion.parse("not-a-version")


def test_bump_patch_and_minor():
    v = GitflowVersion(1, 2, 3)
    assert str(bump_patch(v)) == "1.2.4"
    assert str(bump_minor(v)) == "1.3.0"


def test_bump_dev_and_rc_monotonic():
    v = GitflowVersion(1, 2, 3)
    d1 = bump_dev(v)
    d2 = bump_dev(d1)
    assert str(d1) == "1.2.3-dev.1"
    assert str(d2) == "1.2.3-dev.2"

    r1 = bump_rc(v)
    r2 = bump_rc(r1)
    assert str(r1) == "1.2.3-rc.1"
    assert str(r2) == "1.2.3-rc.2"


def test_normalize_to_stable_strips_prerelease():
    v = GitflowVersion.parse("1.2.3-rc.5")
    assert str(normalize_to_stable(v)) == "1.2.3"


def test_production_tag():
    v = GitflowVersion(2, 0, 0)
    assert v.production_tag() == "v2.0.0"
