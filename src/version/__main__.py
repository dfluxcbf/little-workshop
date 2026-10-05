"""Version Control & Release's real entrypoint: `python -m version`."""

from __future__ import annotations

from common import serving
from common.paths import Paths
from version.app import build_app


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()

    app = build_app(paths)
    # Before serving: a Job the last run accepted and never finished is failed (REQ-000000410).
    app.state.settle_interrupted_jobs()

    serving.serve(app, paths.socket_path("version"))


if __name__ == "__main__":
    main()
