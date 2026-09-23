"""The standard on-disk layout for a project `project.bootstrap` scaffolds
— ported from `bootstrap-little-project`'s `scaffold.py`, templates
adapted (see `templates.py`)."""

from __future__ import annotations

from pathlib import Path

from registry import templates


def module_name_for(project_name: str) -> str:
    return project_name.replace("-", "_")


def create_project_tree(dest: Path, name: str) -> list[Path]:
    """Write the standard docs/src/tests layout into `dest`.

    `dest` must not already exist. Returns the list of files written.
    """
    if dest.exists():
        raise FileExistsError(f"{dest} already exists")

    written: list[Path] = []

    def write(rel_path: str, content: str, *, executable: bool = False) -> None:
        path = dest / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if executable:
            path.chmod(path.stat().st_mode | 0o111)
        written.append(path)

    write("docs/design.md", templates.design_md(name))
    write("docs/architecture.md", templates.architecture_md(name))
    write("docs/milestones.md", templates.milestones_md())

    write(f"src/{templates.PLACEHOLDER_FILENAME}", "")
    write(f"tests/{templates.PLACEHOLDER_FILENAME}", "")

    write("README.md", templates.readme_md(name))
    write(".gitignore", templates.gitignore())
    write("VERSION", templates.version_file())
    write("MODULE.bazel", templates.module_bazel(module_name_for(name)))
    write("BUILD.bazel", templates.build_bazel())

    return written
