# Build and structure standard

Sources: `/home/crist/little-projects/docs/policies/build_policy.md`, `docs/governance_policy.md`
("What's kept"), `src/dashboard/job_catalog.py`, policy `wheel-filename-has-version`.

## Folder layout

`docs/`, `src/`, `tests/`, `tools/` at the project root, plus `VERSION`, `README.md`,
`BUILD.bazel`, `MODULE.bazel`. Python projects add `pyproject.toml`.

The super-repo's `project_structure_policy.md` (cited by the build policy) does not exist; this
layout is the working rule. One `little-*` project **never imports another's source**; it consumes
the other's installed working version like a third-party package.

Exception by nature: a game engine project (Ethereal Lands) keeps the engine's own layout
(`Assets/`, `Packages/`, `ProjectSettings/`); see its folder.

## Bazel

Bazel is the canonical build. Non-Bazel build files may remain for external compatibility but are
marked deprecated in `architecture.md` once Bazel covers them.

| Command | Purpose |
|---|---|
| `bazel build //:all` | Build every artifact |
| `bazel build //:wheel` | Python wheel |
| `bazel build //:vsix` | VS Code extension, where shipped |
| `bazel build //:exe` | Native executable, where compiled |
| `bazel run //:install` | Build and install locally; how a project becomes usable by siblings |
| `bazel test //...` | Automated tests |

Only implement the targets that fit the project's artifacts, under these exact names. The Dashboard
Build job runs `bazel build //...` and, when asked, `bazel run //:install` in a throwaway checkout
of the current commit, under the project's own queue so an install never overlaps a Launch.

## Tests need no manual install

Python projects configure `pytest` to resolve `src/` via `pythonpath`, not `pip install -e .`. A
project is normalized only when `bazel build //:all` succeeds and its test targets pass.

## Portability

No Bazel command depends on a username or hard-coded absolute path. Resolve host-specific values at
run time (`$HOME`, `$(location ...)`, `command -v`).

## Dependency isolation

- Python CLI tools install with `pipx`, never `pip install --user`.
- Non-hermetic test dependencies go in a project-local, gitignored `.venv/`.
- VS Code extensions package with `npx`, never `npm install -g`.

## Missing system dependencies

Unisolatable tools (compiler, `node`, `pipx`) are checked in a preflight **before** any work:

1. check everything required on `PATH`;
2. report every missing tool in one pass (stop at a tier whose prerequisite is itself missing);
3. print one copy-pasteable install command per missing tool.

## Packaging

A built Python wheel's file name carries the project version (`<name>-<version>-<tags>.whl`) so the
package index can publish it (policy `wheel-filename-has-version`, gate `package_publish`).

## Servers

Project servers run on demand; down is normal. Deployment & Servers launches them, and a Relaunch
picks up a new install.

Last reviewed: 2026-10-01.
