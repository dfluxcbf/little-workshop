import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
INTEGRATION = ROOT / "tests" / "integration"

for path in (SRC, INTEGRATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _systemd_user_available() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.stdout.strip() in ("running", "degraded")


requires_systemd_user = pytest.mark.skipif(
    not _systemd_user_available(), reason="systemd --user session not available"
)
