"""Usage & Efficiency's real entrypoint: `python -m usage`."""

from __future__ import annotations

import threading

from common import serving
from common.paths import Paths
from usage import db, proxy
from usage.app import build_app, run_tick_forever


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()

    conn = db.connect(paths.pillar_db_path("usage"))
    app = build_app(conn, paths)

    # The loopback port Claude Code posts to; without it the pillar still serves its socket.
    proxy.ensure_proxy(paths)
    threading.Thread(target=run_tick_forever, args=(app, threading.Event()), daemon=True).start()

    serving.serve(app, paths.socket_path("usage"))


if __name__ == "__main__":
    main()
