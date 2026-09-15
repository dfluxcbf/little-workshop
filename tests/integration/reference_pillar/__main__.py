from __future__ import annotations

import os
import sys

import uvicorn

from reference_pillar.app import build_app


def main() -> None:
    socket_path = sys.argv[1] if len(sys.argv) > 1 else os.environ["LWS_REFERENCE_SOCKET"]
    uvicorn.run(build_app(), uds=socket_path, log_level="warning")


if __name__ == "__main__":
    main()
