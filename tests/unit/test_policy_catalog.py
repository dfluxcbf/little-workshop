from policy.catalog import CatalogEntry, find_entry, load_catalog


SAMPLE = """# Policy catalog

## A. Project structure & inter-project independence

| Topic | EARS requirement | Source |
|---|---|---|
| `standard-folders` | The project shall contain `docs/`, `src/`, `tests/`, and `tools/` folders at its root. | `project_structure_policy.md` |
| `extra-folders-documented` | WHERE a project has folders beyond the standard layout, the project's `docs/architecture.md` shall document their purpose. | `project_structure_policy.md` |

## B. Documentation

| Topic | EARS requirement | Source |
|---|---|---|
| `standard-doc-set` | The project shall contain `docs/design.md`. | `documentation_policy.md` |

Some prose outside any table row should be ignored, including a line
with a pipe | in it that isn't a real table row.
"""


def test_load_catalog_parses_every_row(tmp_path):
    path = tmp_path / "catalog.md"
    path.write_text(SAMPLE, encoding="utf-8")
    entries = load_catalog(path)
    assert len(entries) == 3
    assert [e.topic for e in entries] == ["standard-folders", "extra-folders-documented", "standard-doc-set"]


def test_load_catalog_strips_backticks_from_ears_text_and_source(tmp_path):
    path = tmp_path / "catalog.md"
    path.write_text(SAMPLE, encoding="utf-8")
    entries = load_catalog(path)
    first = entries[0]
    assert "`" not in first.ears_text
    assert "docs/" in first.ears_text
    assert first.source == "project_structure_policy.md"


def test_load_catalog_assigns_section_and_title(tmp_path):
    path = tmp_path / "catalog.md"
    path.write_text(SAMPLE, encoding="utf-8")
    entries = load_catalog(path)
    assert entries[0].section == "A"
    assert entries[0].section_title == "Project structure & inter-project independence"
    assert entries[2].section == "B"
    assert entries[2].section_title == "Documentation"


def test_load_catalog_ignores_the_header_row(tmp_path):
    path = tmp_path / "catalog.md"
    path.write_text(SAMPLE, encoding="utf-8")
    entries = load_catalog(path)
    assert all(e.topic != "Topic" for e in entries)


def test_load_catalog_ignores_prose_lines_with_pipes(tmp_path):
    path = tmp_path / "catalog.md"
    path.write_text(SAMPLE, encoding="utf-8")
    entries = load_catalog(path)
    assert all("Some prose" not in e.ears_text for e in entries)


def test_load_catalog_against_the_real_catalog_file():
    entries = load_catalog()
    assert len(entries) >= 50
    topics = [e.topic for e in entries]
    assert len(topics) == len(set(topics))  # every topic is unique
    assert "two-long-lived-branches" in topics
    assert "pillar-exclusive-storage" in topics


def test_find_entry_returns_the_matching_entry(tmp_path):
    path = tmp_path / "catalog.md"
    path.write_text(SAMPLE, encoding="utf-8")
    entries = load_catalog(path)
    found = find_entry("standard-doc-set", entries)
    assert found.section == "B"


def test_find_entry_returns_none_for_unknown_topic(tmp_path):
    path = tmp_path / "catalog.md"
    path.write_text(SAMPLE, encoding="utf-8")
    entries = load_catalog(path)
    assert find_entry("ghost-topic", entries) is None


def test_find_entry_defaults_to_loading_the_real_catalog():
    found = find_entry("two-long-lived-branches")
    assert found is not None
    assert found.section == "C"
