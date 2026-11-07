"""Requirements & Quality's real entrypoint: `python -m requirements`."""

from __future__ import annotations

from common import serving
from common.paths import Paths
from requirements import db
from requirements.app import CONFLICT_SCAN_POLL_SECONDS, build_app


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()

    conn = db.connect(paths.pillar_db_path("requirements"))
    app = build_app(conn, paths, conflict_scan_poll_seconds=CONFLICT_SCAN_POLL_SECONDS)

    serving.serve(app, paths.socket_path("requirements"))


if __name__ == "__main__":
    main()
