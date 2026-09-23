"""core.health — probe_health against a real (but not always listening)
Unix domain socket. Uses a real, unbound socket path to exercise the
genuine ConnectError retry path rather than mocking httpx."""

from core.health import probe_health


def test_probe_health_succeeds_immediately_when_reachable(tmp_path):
    import threading
    import time

    import uvicorn
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/v1/health")
    def health():
        return {"ok": True, "pillar": "test"}

    socket_path = tmp_path / "test.sock"
    config = uvicorn.Config(app, uds=str(socket_path), log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if getattr(server, "started", False):
                break
            time.sleep(0.02)
        result = probe_health(socket_path, timeout=2.0, max_retries=5, retry_delay=0.01)
        assert result.ok is True
        assert result.attempts == 1
        assert result.report == {"ok": True, "pillar": "test"}
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_probe_health_exhausts_retries_and_reports_last_error(tmp_path):
    sleeps = []
    result = probe_health(
        tmp_path / "never-listening.sock", timeout=0.2, max_retries=3, retry_delay=0.0, sleep=sleeps.append,
    )
    assert result.ok is False
    assert result.attempts == 3
    assert result.error is not None
    assert len(sleeps) == 2  # sleeps between attempts, never after the last one


def test_probe_health_retries_on_non_200_status(tmp_path):
    import threading
    import time

    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI()
    attempt_count = {"n": 0}

    @app.get("/v1/health")
    def health():
        attempt_count["n"] += 1
        if attempt_count["n"] < 2:
            return JSONResponse({"detail": "not ready"}, status_code=503)
        return {"ok": True}

    socket_path = tmp_path / "flaky.sock"
    config = uvicorn.Config(app, uds=str(socket_path), log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if getattr(server, "started", False):
                break
            time.sleep(0.02)
        result = probe_health(socket_path, timeout=2.0, max_retries=5, retry_delay=0.01)
        assert result.ok is True
    finally:
        server.should_exit = True
        thread.join(timeout=5)
