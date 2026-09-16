"""Build & Packages' real entrypoint: `python -m build`."""

from __future__ import annotations

import uvicorn

from build import db
from build.app import build_app
from common.paths import Paths


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()

    conn = db.connect(paths.pillar_db_path("build"))
    app = build_app(conn, paths)

    socket_path = paths.socket_path("build")
    if socket_path.exists():
        socket_path.unlink()

    uvicorn.run(app, uds=str(socket_path), log_level="info")


if __name__ == "__main__":
    main()
