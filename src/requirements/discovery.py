"""Which tests belong to a test suite — worked out without running them.

A suite has no stored list of tests. Its *scope* is the set of test modules
it covers: its own name when that is a module name (little-workshop's
per-module suites, e.g. "test_agent_app"), plus the module of every test
result already recorded under it (a feature suite such as "Cook Along"
holds `tests.test_cook_along::…` results). Collecting the project's tests
(`pytest --collect-only`, nothing runs) and keeping the ones whose file is
in scope gives the suite's current tests — including any added since the
last run.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_MODULE_NAME_RE = re.compile(r"^[A-Za-z_][\w.]*$")


def file_module(path: str) -> str:
    """`tests/unit/test_x.py` -> `tests.unit.test_x` (a collected node id's file part)."""
    return path.removesuffix(".py").replace("/", ".").replace("\\", ".")


def result_module(test_name: str) -> str:
    """A recorded result's module part: JUnit's classname (`tests.test_x`, or
    `tests.test_x.TestClass` for a method), before the `::`."""
    return test_name.split("::", 1)[0] if "::" in test_name else ""


def suite_scope(suite_name: str, recorded_test_names: Iterable[str]) -> set[str]:
    scope = {m for m in (result_module(n) for n in recorded_test_names) if m}
    if _MODULE_NAME_RE.match(suite_name) and suite_name.split(".")[-1].startswith("test"):
        scope.add(suite_name)
    return scope


def file_in_scope(module: str, scope: set[str]) -> bool:
    """Whether a collected test file (as a dotted module) is one the scope names —
    exactly, by its bare file name, or as the module of a class inside it."""
    stem = module.rsplit(".", 1)[-1]
    return any(m == module or m == stem or m.startswith(module + ".") or module.endswith("." + m) for m in scope)


def files_for_scopes(node_ids: Iterable[str], scopes: dict[str, set[str]]) -> dict[str, list[str]]:
    """{suite key: [test file path]} — every collected file each scope covers."""
    files = sorted({n.split("::", 1)[0] for n in node_ids if "::" in n})
    return {key: [f for f in files if file_in_scope(file_module(f), scope)] for key, scope in scopes.items()}


def tests_for_scopes(node_ids: Iterable[str], scopes: dict[str, set[str]]) -> dict[str, list[str]]:
    """{suite key: [collected node id]} — the tests each suite would run."""
    ids = [n for n in node_ids if "::" in n]
    return {
        key: [n for n in ids if file_in_scope(file_module(n.split("::", 1)[0]), scope)]
        for key, scope in scopes.items()
    }


def owning_file(test_name: str, files: Iterable[str]) -> str | None:
    """The collected file a JUnit result came from (its classname is the file's
    dotted module, possibly followed by a class name)."""
    module = result_module(test_name)
    for f in files:
        dotted = file_module(f)
        if module == dotted or module.startswith(dotted + "."):
            return f
    return None
