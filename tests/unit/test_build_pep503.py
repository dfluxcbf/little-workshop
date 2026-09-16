from build.models import Package, PackageKind
from build.pep503 import normalize_name, render_project_index, render_root_index


def test_normalize_name_lowercases_and_collapses_separators():
    assert normalize_name("My_Package..Name") == "my-package-name"
    assert normalize_name("my-package-name") == "my-package-name"
    assert normalize_name("My.Package_Name") == "my-package-name"


def test_normalize_name_collapses_runs_of_separators():
    assert normalize_name("a___b") == "a-b"
    assert normalize_name("a--b") == "a-b"


def test_render_root_index_links_every_project_name():
    html = render_root_index(["alpha", "beta"])
    assert '<a href="alpha/">alpha</a>' in html
    assert '<a href="beta/">beta</a>' in html


def test_render_root_index_empty_list_is_still_valid_html():
    html = render_root_index([])
    assert "<html>" in html and "</html>" in html


def test_render_project_index_links_to_the_download_path():
    packages = [
        Package(id="1", project_id="p1", kind=PackageKind.PYPI, name="pkg", normalized_name="pkg",
                version="1.0.0", filename="pkg-1.0.0-py3-none-any.whl"),
    ]
    html = render_project_index("pkg", packages)
    assert '<a href="../../packages/pkg-1.0.0-py3-none-any.whl">pkg-1.0.0-py3-none-any.whl</a>' in html


def test_render_project_index_lists_every_version():
    packages = [
        Package(id="1", project_id="p1", kind=PackageKind.PYPI, name="pkg", normalized_name="pkg",
                version="1.0.0", filename="pkg-1.0.0.whl"),
        Package(id="2", project_id="p1", kind=PackageKind.PYPI, name="pkg", normalized_name="pkg",
                version="2.0.0", filename="pkg-2.0.0.whl"),
    ]
    html = render_project_index("pkg", packages)
    assert "pkg-1.0.0.whl" in html
    assert "pkg-2.0.0.whl" in html
