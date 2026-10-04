"""Usage & Efficiency's real entrypoint: `python -m usage`."""

from __future__ import annotations

import threading

import uvicorn

from common.paths import Paths
from usage import db, proxy
from usage.app import build_app, run_tick_forever


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()

    conn = db.connect(paths.pillar_db_path("usage"))
    app = build_app(conn, paths)

    socket_path = paths.socket_path("usage")
    if socket_path.exists():
        socket_path.unlink()

    # The loopback port Claude Code posts to; without it the pillar still serves its socket.
    proxy.ensure_proxy(paths)
    threading.Thread(target=run_tick_forever, args=(app, threading.Event()), daemon=True).start()

    uvicorn.run(app, uds=str(socket_path), log_level="info")


if __name__ == "__main__":
    main()
