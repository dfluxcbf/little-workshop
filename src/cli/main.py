"""`lws` — docs/architecture.md's CLI entry: a thin, stateless proxy, one
function per subcommand. Just enough for M1: `lws core status`,
`lws job create/list/status`.
"""

from __future__ import annotations

import argparse
import os
import sys

from cli import client
from cli.commands import agent, api, build, core, deployment, mcp, orchestration, policy, registry, requirements, usage, version, windows
from common.paths import Paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lws")
    subparsers = parser.add_subparsers(dest="command", required=True)

    core.register(subparsers)
    usage.register(subparsers)
    policy.register_bypass(subparsers)
    orchestration.register(subparsers)
    registry.register(subparsers)
    version.register(subparsers)
    requirements.register(subparsers)
    build.register(subparsers)
    policy.register(subparsers)
    deployment.register(subparsers)
    agent.register(subparsers)
    mcp.register(subparsers)
    windows.register(subparsers)
    api.register(subparsers)

    return parser


def _subparser_for(parser: argparse.ArgumentParser, argv: list[str]) -> argparse.ArgumentParser:
    """The (sub)parser the command words at the front of `argv` select (`version status` -> its parser)."""
    current = parser
    for word in argv:
        if word.startswith("-"):
            break
        subactions = [a for a in current._actions if isinstance(a, argparse._SubParsersAction)]
        if not subactions or word not in subactions[0].choices:
            break
        current = subactions[0].choices[word]
    return current


def _windows_argv(parser: argparse.ArgumentParser, argv: list[str], cwd: str, projects_of) -> list[str]:
    """`lws` started by the Windows shim (docs/architecture.md, "Windows-hosted projects"): Windows paths become WSL
    paths, and a command taking `--project-id` without one gets the registered project holding the folder the
    shim was run from."""
    from common import windows

    argv = windows.translate_argv(argv, windows.mount_root())
    if any(a == "--project-id" or a.startswith("--project-id=") for a in argv):
        return argv
    sub = _subparser_for(parser, argv)
    if not any("--project-id" in a.option_strings for a in sub._actions):
        return argv
    try:
        project = windows.project_for_cwd(cwd, projects_of())
    except Exception:  # noqa: BLE001 - no registry answer: argparse reports the missing option as usual
        project = None
    return [*argv, "--project-id", project] if project else argv


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    if os.environ.get("LWS_FROM_WINDOWS"):
        argv = _windows_argv(parser, sys.argv[1:] if argv is None else argv, os.getcwd(),
                             lambda: client.list_projects(Paths.from_env())["projects"])
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
