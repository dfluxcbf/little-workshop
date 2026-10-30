"""Registry & Portfolio's real entrypoint: `python -m registry`."""

from __future__ import annotations

from common import serving
from common.paths import Paths
from registry import db
from registry.app import build_app


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()

    conn = db.connect(paths.pillar_db_path("registry"))
    app = build_app(conn, paths)

    serving.serve(app, paths.socket_path("registry"))


if __name__ == "__main__":
    main()
