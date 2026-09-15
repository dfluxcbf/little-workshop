"""Deployment & Servers' real entrypoint: `python -m deployment`."""

from __future__ import annotations

import uvicorn

from common.paths import Paths
from deployment import db
from deployment.app import build_app


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()

    conn = db.connect(paths.pillar_db_path("deployment"))
    app = build_app(paths, conn)

    socket_path = paths.socket_path("deployment")
    if socket_path.exists():
        socket_path.unlink()

    uvicorn.run(app, uds=str(socket_path), log_level="info")


if __name__ == "__main__":
    main()
