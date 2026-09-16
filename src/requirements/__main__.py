"""Requirements & Quality's real entrypoint: `python -m requirements`."""

from __future__ import annotations

import uvicorn

from common.paths import Paths
from requirements import db
from requirements.app import build_app


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()

    conn = db.connect(paths.pillar_db_path("requirements"))
    app = build_app(conn, paths)

    socket_path = paths.socket_path("requirements")
    if socket_path.exists():
        socket_path.unlink()

    uvicorn.run(app, uds=str(socket_path), log_level="info")


if __name__ == "__main__":
    main()
