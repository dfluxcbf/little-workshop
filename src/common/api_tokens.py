"""Tokens for the Dashboard's HTTP gateway (`/api/v1/tools/...`, docs/pillars/dashboard.md "HTTP gateway").

A token belongs to one project: code in that project (a Windows service, a Unity editor script, a script run on
the user's phone over the tailnet) calls the gateway with it, and the gateway acts as that project, as a Claude
session in it would. Only a token's SHA-256 is kept, in `~/.config/little-workshop/api_tokens.json` (mode 0600);
the token itself is shown once, when it is created. A token is a credential the user hands out, so creating and
revoking one are the user's decisions (`lws api token-create` / `token-revoke` always prompt in Claude sessions).
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from common.paths import Paths

TOKEN_PREFIX = "lwst_"


def tokens_path(paths: Paths) -> Path:
    return paths.config_home / "api_tokens.json"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load(paths: Paths) -> list[dict]:
    try:
        data = json.loads(tokens_path(paths).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    tokens = data.get("tokens") if isinstance(data, dict) else None
    return [t for t in tokens or [] if isinstance(t, dict) and t.get("sha256") and t.get("project_id")]


def _save(paths: Paths, tokens: list[dict]) -> None:
    path = tokens_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"tokens": tokens}, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def _public(entry: dict) -> dict:
    return {k: entry.get(k) for k in ("id", "project_id", "label", "created_at")}


def create(paths: Paths, project_id: str, label: str = "") -> dict:
    """A new token for `project_id`: its public fields plus `token`, which is never stored or shown again."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    digest = _digest(token)
    entry = {
        "id": digest[:12],
        "project_id": project_id,
        "label": label,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "sha256": digest,
    }
    _save(paths, [*_load(paths), entry])
    return {**_public(entry), "token": token}


def list_tokens(paths: Paths, project_id: str | None = None) -> list[dict]:
    return [_public(t) for t in _load(paths) if project_id is None or t["project_id"] == project_id]


def revoke(paths: Paths, token_id: str, project_id: str | None = None) -> bool:
    """Remove the token with this id (of this project, when given). True when one was removed."""
    tokens = _load(paths)
    kept = [t for t in tokens if not (t.get("id") == token_id and (project_id is None or t["project_id"] == project_id))]
    if len(kept) == len(tokens):
        return False
    _save(paths, kept)
    return True


def project_for(paths: Paths, token: str | None) -> str | None:
    """The project a presented token belongs to, or None (unknown, revoked or malformed)."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    digest = _digest(token)
    for entry in _load(paths):
        if hmac.compare_digest(entry["sha256"], digest):
            return entry["project_id"]
    return None
