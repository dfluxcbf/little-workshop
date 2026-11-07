"""Image Generation's real entrypoint: `python -m generation`."""

from __future__ import annotations

import threading

from common import events, serving
from common.incident import install_thread_incident_reporting
from common.paths import Paths
from generation import config as conf
from generation import db
from generation.app import build_app
from generation.worker import Engine


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()

    conn = db.connect(paths.pillar_db_path(conf.PILLAR))
    engine = Engine(conn, threading.RLock(), load_config=lambda: conf.load(paths.pillar_config_dir(conf.PILLAR)),
                    store_dir=paths.generated_dir,
                    emit=lambda name, project, data: events.emit_in_background(paths, name, project, data))
    app = build_app(conn, paths, engine=engine)

    install_thread_incident_reporting(paths, pillar=conf.PILLAR)
    threading.Thread(target=engine.run_forever, args=(serving.stopping,), daemon=True).start()

    serving.serve(app, paths.socket_path(conf.PILLAR))


if __name__ == "__main__":
    main()
