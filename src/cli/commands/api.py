"""`lws` commands of the Dashboard HTTP gateway's tokens."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from cli.commands.common import _print
from common.paths import Paths


def cmd_api_token_create(args: argparse.Namespace) -> int:
    from common import api_tokens

    created = api_tokens.create(Paths.from_env(), args.project_id, args.label or "")
    if args.write:
        target = Path(args.write)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(created["token"] + "\n")
        created = {**created, "token": f"written to {target} (keep it out of git)"}
    _print({**created, "use": "Authorization: Bearer <token> on http://127.0.0.1:8790/api/v1/tools/<tool>"})
    return 0


def cmd_api_token_list(args: argparse.Namespace) -> int:
    from common import api_tokens

    _print({"tokens": api_tokens.list_tokens(Paths.from_env(), args.project_id)})
    return 0


def cmd_api_token_revoke(args: argparse.Namespace) -> int:
    from common import api_tokens

    if not api_tokens.revoke(Paths.from_env(), args.token_id, args.project_id):
        print(f"error: no token {args.token_id} for {args.project_id}", file=sys.stderr)
        return 1
    _print({"revoked": args.token_id, "project_id": args.project_id})
    return 0


def register(subparsers) -> None:
    api = subparsers.add_parser("api", help="Tokens for the Dashboard's HTTP gateway (/api/v1/tools)")
    api_sub = api.add_subparsers(dest="api_command", required=True)
    token_create = api_sub.add_parser("token-create", help="A new gateway token for a project (shown once)")
    token_create.add_argument("--project-id", required=True)
    token_create.add_argument("--label", help="What the token is for, e.g. 'Unity editor'")
    token_create.add_argument("--write", help="Write the token to this file (mode 0600) instead of printing it")
    token_create.set_defaults(func=cmd_api_token_create)
    token_list = api_sub.add_parser("token-list", help="A project's gateway tokens (never the tokens themselves)")
    token_list.add_argument("--project-id", required=True)
    token_list.set_defaults(func=cmd_api_token_list)
    token_revoke = api_sub.add_parser("token-revoke", help="Revoke a project's gateway token by id")
    token_revoke.add_argument("--project-id", required=True)
    token_revoke.add_argument("token_id")
    token_revoke.set_defaults(func=cmd_api_token_revoke)
