"""Agentic Development's real entrypoint: `python -m agent`."""

from __future__ import annotations

import uvicorn

from common.paths import Paths
from agent import db
from agent.app import build_app, recover_interrupted_runs, resume_doc_updates


def main() -> None:
    paths = Paths.from_env()
    paths.ensure_layout()

    conn = db.connect(paths.pillar_db_path("agent"))
    app = build_app(paths, conn)
    # Before serving, so no new run can be mistaken for an interrupted one;
    # reporting to Orchestration is best-effort, its own timeout the backstop.
    recover_interrupted_runs(paths, conn)
    resume_doc_updates(paths, conn, app.state.doc_flow)

    socket_path = paths.socket_path("agent")
    if socket_path.exists():
        socket_path.unlink()

    uvicorn.run(app, uds=str(socket_path), log_level="info")


if __name__ == "__main__":
    main()
