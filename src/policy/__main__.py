"""Policy & Compliance's real entrypoint: `python -m policy`."""

from __future__ import annotations

import uvicorn

from common.paths import Paths
from policy import db
from policy.app import build_app


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()

    conn = db.connect(paths.pillar_db_path("policy"))
    app = build_app(conn, paths)

    socket_path = paths.socket_path("policy")
    if socket_path.exists():
        socket_path.unlink()

    uvicorn.run(app, uds=str(socket_path), log_level="info")


if __name__ == "__main__":
    main()
