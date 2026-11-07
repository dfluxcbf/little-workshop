"""`lws` commands of Core."""

from __future__ import annotations

import argparse

from cli import client
from cli.commands.common import _print
from common.paths import Paths


def cmd_core_status(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.core_status(paths))
    return 0


def register(subparsers) -> None:
    core = subparsers.add_parser("core", help="Core supervisor commands")
    core_sub = core.add_subparsers(dest="core_command", required=True)
    core_status = core_sub.add_parser("status", help="Show every pillar's registered status")
    core_status.set_defaults(func=cmd_core_status)
