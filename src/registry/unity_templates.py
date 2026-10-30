"""Templates for a `template="unity"` project `project.bootstrap` scaffolds
(REQ-000000274..278): a Windows-native Unity game whose checkout lives on
the Windows drive (e.g. `/mnt/c/little-projects/<name>`) but is managed
from WSL. Unity itself creates `Assets/`, `Packages/` and
`ProjectSettings/` the first time the Editor opens the folder, so none of
those are written here -- only what the repository needs around them.
"""

from __future__ import annotations

from registry import claude_templates

TEST_RESULTS_DIR = "TestResults"


def readme_md(name: str) -> str:
    return f"""# {name}

TBD, purpose not yet defined.

A Unity game for Windows. See `docs/design.md` for details as they're
filled in, and `.claude/AGENTS.md` for how Claude sessions work in this project.

## First-time setup

Create the Unity project inside this checkout (it already holds the docs
and git settings; Unity keeps them), from WSL in this directory:

    "/mnt/c/Program Files/Unity/Hub/Editor/<version>/Editor/Unity.exe" \\
        -batchmode -nographics -quit -createProject "$(wslpath -w .)"

Then `git lfs install`, commit the generated `Assets/`, `Packages/` and
`ProjectSettings/`, and open the project with `unity open .` or Unity Hub.

## Tests

    tools/run-unity-tests [--platform=EditMode|PlayMode] [--junit-xml=PATH]

Managed by little-workshop's Registry & Portfolio pillar.
"""


def gitignore() -> str:
    """REQ-000000276."""
    return f"""# Unity generated
/[Ll]ibrary/
/[Tt]emp/
/[Oo]bj/
/[Bb]uild/
/[Bb]uilds/
/[Ll]ogs/
/[Uu]ser[Ss]ettings/
/[Mm]emoryCaptures/
/[Rr]ecordings/
*.pidb.meta
*.pdb.meta
*.mdb.meta
sysinfo.txt
crashlytics-build.properties

# IDE generated
.vs/
.vscode/
.idea/
.gradle/
*.csproj
*.unityproj
*.sln
*.slnx
*.suo
*.user
*.userprefs
*.pidb
*.booproj

# Build outputs
*.apk
*.aab
*.unitypackage
*_BurstDebugInformation_DoNotShip/

# tools/run-unity-tests output
/{TEST_RESULTS_DIR}/

# Claude Code per-machine settings
.claude/settings.local.json
.claude/worktrees/
"""


_LFS_PATTERNS = [
    # textures / images
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.bmp", "*.tga", "*.tif", "*.tiff", "*.psd", "*.psb",
    "*.exr", "*.hdr", "*.dds", "*.ktx", "*.svg-bin",
    # 3D models
    "*.fbx", "*.obj", "*.blend", "*.max", "*.ma", "*.mb", "*.3ds", "*.dae", "*.gltf", "*.glb", "*.usd", "*.usdz",
    # audio
    "*.wav", "*.mp3", "*.ogg", "*.aif", "*.aiff", "*.flac", "*.mod", "*.it", "*.s3m", "*.xm", "*.bank",
    # video
    "*.mp4", "*.mov", "*.avi", "*.webm", "*.mkv",
    # fonts
    "*.ttf", "*.otf",
    # archives / native / misc binary
    "*.zip", "*.7z", "*.rar", "*.gz", "*.dll", "*.so", "*.dylib", "*.a", "*.exe", "*.pdf",
    "*.cubemap", "*.unitypackage", "*.bytes",
]

_UNITY_YAML_PATTERNS = [
    "*.unity", "*.prefab", "*.asset", "*.mat", "*.anim", "*.controller", "*.overrideController",
    "*.physicMaterial", "*.physicsMaterial2D", "*.playable", "*.mask", "*.brush", "*.flare",
    "*.fontsettings", "*.guiskin", "*.giparams", "*.renderTexture", "*.spriteatlas", "*.spriteatlasv2",
    "*.terrainlayer", "*.mixer", "*.shadervariants", "*.preset", "*.signal", "*.lighting",
]


def gitattributes() -> str:
    """REQ-000000275. The `unityyamlmerge` driver itself is configured per
    clone (see .claude/AGENTS.md); without it git falls back to a normal text merge."""
    lines = [
        "# Line endings: normalise text, let each tool check out its own style",
        "* text=auto",
        "*.cs text diff=csharp eol=lf",
        "*.shader text eol=lf",
        "*.hlsl text eol=lf",
        "*.cginc text eol=lf",
        "*.json text eol=lf",
        "*.asmdef text eol=lf",
        "*.uss text eol=lf",
        "*.uxml text eol=lf",
        "",
        "# Unity YAML assets: text, merged semantically",
        "*.meta text eol=lf merge=unityyamlmerge",
    ]
    lines += [f"{p} text eol=lf merge=unityyamlmerge linguist-generated" for p in _UNITY_YAML_PATTERNS]
    lines += ["", "# Binary assets: Git LFS"]
    lines += [f"{p} filter=lfs diff=lfs merge=lfs -text" for p in _LFS_PATTERNS]
    lines += ["", "# Shell tools stay LF so they run under WSL", "tools/* text eol=lf", ""]
    return "\n".join(lines)


def agents_md(name: str) -> str:
    """The unity layout's `.claude/AGENTS.md`: the working rules of REQ-000000278, where
    REQ-000000294 puts them, then an import of the section every project shares."""
    return f"""# {name} — notes for Claude

{claude_templates.BRIEFING_NOTE}

A Unity game targeting Windows. The checkout lives on the Windows drive
(`C:\\little-projects\\{name}`, `/mnt/c/little-projects/{name}` from WSL)
and is managed from WSL by little-workshop.

## Working with the Editor

- Drive the open Unity Editor through the Unity CLI instead of
  hand-editing scene/prefab/asset YAML: `unity status`, `unity command`,
  `unity command eval '<C#>'`. It needs
  the `com.unity.pipeline` package in the project (`unity pipeline install`).
- If `unity status` shows no ready Editor, the project may have compile
  errors (Safe Mode) — fix them first; do not fall back to blind YAML edits.
- Write gameplay code as C# under `Assets/`, with assembly definitions
  (`.asmdef`) per feature; put tests in `Assets/Tests/EditMode` and
  `Assets/Tests/PlayMode` with their own test `.asmdef`s.

## What Unity owns

- `Library/`, `Temp/`, `Obj/`, `Logs/`, `UserSettings/` and `Build*/` are
  Unity's and git-ignored: never edit or commit them.
- An asset is added, moved, renamed or deleted together with its `.meta`
  file, whose GUID the Editor writes; the commit hook refuses a commit that
  separates them (`unity-meta-files-paired`).
- Binary assets go through Git LFS, which `.gitattributes` routes (run
  `git lfs install` once per clone); the commit hook refuses one committed
  as a plain blob (`lfs-tracked-files-are-pointers`).

## Tests

    tools/run-unity-tests [--platform=EditMode|PlayMode] [--junit-xml=PATH]

Runs the tests in the Windows Editor in batch mode and writes a JUnit-XML
report (default `{TEST_RESULTS_DIR}/junit-<platform>.xml`), ready for
little-workshop's `workshop_requirements_import_junit`. Batch mode cannot
open a project the Editor already has open — close it first, or run the
tests from the open Editor via the Unity CLI.

## Per-clone git setup

    git lfs install
    git config merge.unityyamlmerge.name "Unity SmartMerge"
    git config merge.unityyamlmerge.driver "'<Editor>/Data/Tools/UnityYAMLMerge.exe' merge -p %O %B %A %A"

{claude_templates.SHARED_BRIEFING_IMPORT}
"""


def run_unity_tests() -> str:
    """REQ-000000277: the bash entry point. Converts paths with `wslpath -w`
    only when the Editor is a Windows `.exe`, so a Linux Editor (or a test
    double) gets plain POSIX paths."""
    return f"""#!/usr/bin/env bash
# Run this Unity project's tests in the Unity Editor (batch mode) and write
# a JUnit-XML report. Usage:
#   tools/run-unity-tests [--platform=EditMode|PlayMode] [--junit-xml=PATH]
# The Editor is ProjectSettings/ProjectVersion.txt's version under the Unity
# Hub install dir, unless UNITY_EDITOR names one.
set -euo pipefail

root="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/.." && pwd)"
platform=EditMode
junit=""
for arg in "$@"; do
    case "$arg" in
        --platform=*) platform="${{arg#*=}}" ;;
        --junit-xml=*) junit="${{arg#*=}}" ;;
        *) echo "run-unity-tests: unknown argument: $arg" >&2; exit 2 ;;
    esac
done

results="$root/{TEST_RESULTS_DIR}"
mkdir -p "$results"
[ -n "$junit" ] || junit="$results/junit-$platform.xml"

editor="${{UNITY_EDITOR:-}}"
if [ -z "$editor" ]; then
    version_file="$root/ProjectSettings/ProjectVersion.txt"
    if [ ! -f "$version_file" ]; then
        echo "run-unity-tests: $version_file not found -- create the Unity project first" >&2
        exit 2
    fi
    version="$(sed -n 's/^m_EditorVersion: *//p' "$version_file" | tr -d '\\r')"
    editor="/mnt/c/Program Files/Unity/Hub/Editor/$version/Editor/Unity.exe"
fi
if [ ! -x "$editor" ]; then
    echo "run-unity-tests: Unity Editor not found at $editor (set UNITY_EDITOR)" >&2
    exit 2
fi

native_path() {{
    case "$editor" in
        *.exe) wslpath -w "$1" ;;
        *) printf '%s' "$1" ;;
    esac
}}

nunit="$results/nunit-$platform.xml"
log="$results/unity-$platform.log"
rm -f "$nunit"

set +e
"$editor" -batchmode -nographics \\
    -projectPath "$(native_path "$root")" \\
    -runTests -testPlatform "$platform" \\
    -testResults "$(native_path "$nunit")" \\
    -logFile "$(native_path "$log")"
code=$?
set -e

if [ ! -f "$nunit" ]; then
    echo "run-unity-tests: Unity produced no test results (exit $code); see $log" >&2
    [ "$code" -ne 0 ] || code=1
    exit "$code"
fi

python3 "$root/tools/nunit_to_junit.py" "$nunit" "$junit"
exit "$code"
"""


def nunit_to_junit_py() -> str:
    """REQ-000000277: NUnit3 (Unity Test Framework) -> JUnit XML."""
    return '''#!/usr/bin/env python3
"""Convert a Unity Test Framework (NUnit3) result file into JUnit XML.

Usage: nunit_to_junit.py NUNIT_XML JUNIT_XML
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

SKIPPED = {"Skipped", "Ignored", "Inconclusive"}


def convert(nunit_text: str) -> str:
    root = ET.fromstring(nunit_text)
    suites = ET.Element("testsuites")
    suite = ET.SubElement(suites, "testsuite", name=root.get("name", "Unity"))
    tests = failures = errors = skipped = 0
    for case in root.iter("test-case"):
        name = case.get("name", "")
        fullname = case.get("fullname", name)
        classname = case.get("classname") or (fullname[: -len(name) - 1] if fullname.endswith("." + name) else "")
        out = ET.SubElement(suite, "testcase", classname=classname, name=name, time=case.get("duration", "0"))
        result, label = case.get("result", ""), case.get("label", "")
        failure = case.find("failure")
        message = (failure.findtext("message") or "").strip() if failure is not None else ""
        stack = (failure.findtext("stack-trace") or "").strip() if failure is not None else ""
        tests += 1
        if result == "Failed" and label in ("Error", "Cancelled", "Invalid"):
            errors += 1
            ET.SubElement(out, "error", message=message or label).text = stack
        elif result == "Failed":
            failures += 1
            ET.SubElement(out, "failure", message=message or "failed").text = stack
        elif result in SKIPPED:
            skipped += 1
            reason = (case.findtext("reason/message") or label or result).strip()
            ET.SubElement(out, "skipped", message=reason)
    for key, value in (("tests", tests), ("failures", failures), ("errors", errors), ("skipped", skipped)):
        suite.set(key, str(value))
    return ET.tostring(suites, encoding="unicode", xml_declaration=True)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8-sig") as src:
        junit = convert(src.read())
    with open(argv[2], "w", encoding="utf-8") as dst:
        dst.write(junit)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''
