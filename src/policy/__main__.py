"""Policy & Compliance's real entrypoint: `python -m policy`."""

from __future__ import annotations

from common import serving
from common.paths import Paths
from policy import db
from policy.app import build_app


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()

    conn = db.connect(paths.pillar_db_path("policy"))
    app = build_app(conn, paths)
    # Before serving, so no new check is taken for one the last stop cut short (REQ-000000405).
    app.state.rerun_interrupted_checks()

    serving.serve(app, paths.socket_path("policy"))


if __name__ == "__main__":
    main()
