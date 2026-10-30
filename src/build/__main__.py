"""Build & Packages' real entrypoint: `python -m build`."""

from __future__ import annotations

from build import db
from build.app import build_app
from common import serving
from common.paths import Paths


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()

    conn = db.connect(paths.pillar_db_path("build"))
    app = build_app(conn, paths)

    serving.serve(app, paths.socket_path("build"))


if __name__ == "__main__":
    main()
