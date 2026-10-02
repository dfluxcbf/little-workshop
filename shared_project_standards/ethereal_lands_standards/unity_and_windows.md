# Ethereal Lands: Unity and Windows

Source: the project's `.claude/AGENTS.md` and `README.md`.

## Where it runs

Windows checkout at `C:\little-projects\ethereal-lands`; from WSL
`/mnt/c/little-projects/ethereal-lands`. The Unity Editor is the Windows Editor
(`/mnt/c/Program Files/Unity/Hub/Editor/<version>/Editor/Unity.exe`).

## Working with the Editor

- Drive the open Editor through the Unity CLI (`unity status`, `unity command`,
  `unity command eval '<C#>'`), not by hand-editing scene, prefab or asset YAML. It needs the
  `com.unity.pipeline` package (`unity pipeline install`).
- If `unity status` shows no ready Editor, the project may have compile errors (Safe Mode): fix them
  first. Do not fall back to blind YAML edits.
- Gameplay code is C# under `Assets/`, one assembly definition (`.asmdef`) per feature. Tests go in
  `Assets/Tests/EditMode` and `Assets/Tests/PlayMode` with their own test `.asmdef`s.
- The economy rules are C# in `Assets/Machination/Core/`, an assembly with no engine references
  that the game, the Editor and the .NET command line (`tools/elm`) all compile.

## Never

- Edit or commit `Library/`, `Temp/`, `Obj/`, `Logs/`, `UserSettings/` or `Build*/`.
- Add, move, rename or delete an asset without its `.meta` file, or hand-write a `.meta` GUID.
- Commit binary assets outside Git LFS (`.gitattributes` routes them; run `git lfs install` once per
  clone).

## Tests

```
tools/run-unity-tests [--platform=EditMode|PlayMode] [--junit-xml=PATH]
```

Runs in the Windows Editor in batch mode and writes JUnit-XML (default
`TestResults/junit-<platform>.xml`), ready for `workshop_requirements_import_junit`. Batch mode cannot
open a project the Editor already has open: close it first, or run the tests from the open Editor
through the Unity CLI.

Differences from the shared testing standard: no `pytest`, no `tests/unit`/`tests/ui` layout, and the
Python coverage policy does not apply. Coverage tooling is `tools/unity-coverage.py`; NUnit output is
converted with `tools/nunit_to_junit.py`.

## Per-clone git setup

```
git lfs install
git config merge.unityyamlmerge.name "Unity SmartMerge"
git config merge.unityyamlmerge.driver "'<Editor>/Data/Tools/UnityYAMLMerge.exe' merge -p %O %B %A %A"
```

## Agent tasks

A task for this project must name the Windows checkout path, must not assume Bazel or a `src/` tree,
and should state which of `docs/design/`, `docs/game/`, `docs/machination/` are read-only.

Last reviewed: 2026-10-01 (hand edit 2026-10-01: instructions moved to `.claude/AGENTS.md`).
