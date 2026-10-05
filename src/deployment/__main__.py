"""Deployment & Servers' real entrypoint: `python -m deployment`."""

from __future__ import annotations

from common import serving
from common.paths import Paths
from deployment import db
from deployment.app import build_app


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()

    conn = db.connect(paths.pillar_db_path("deployment"))
    app = build_app(paths, conn)

    serving.serve(app, paths.socket_path("deployment"))


if __name__ == "__main__":
    main()
