"""Dashboard's real entrypoint: `python -m dashboard`."""

from __future__ import annotations

from common import serving
from common.paths import Paths
from dashboard.app import build_app


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()

    app = build_app(paths)

    serving.serve(app, paths.socket_path("dashboard"))


if __name__ == "__main__":
    main()
