"""Requirements & Quality's real entrypoint: `python -m requirements`."""

from __future__ import annotations

from common import serving
from common.paths import Paths
from requirements import db
from requirements.app import build_app


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()

    conn = db.connect(paths.pillar_db_path("requirements"))
    app = build_app(conn, paths)

    serving.serve(app, paths.socket_path("requirements"))


if __name__ == "__main__":
    main()
