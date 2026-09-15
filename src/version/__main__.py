"""Version Control & Release's real entrypoint: `python -m version`."""

from __future__ import annotations

import uvicorn

from common.paths import Paths
from version.app import build_app


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()

    app = build_app(paths)

    socket_path = paths.socket_path("version")
    if socket_path.exists():
        socket_path.unlink()

    uvicorn.run(app, uds=str(socket_path), log_level="info")


if __name__ == "__main__":
    main()
