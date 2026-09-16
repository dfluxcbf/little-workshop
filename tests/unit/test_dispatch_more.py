"""dispatch.py's non-200 status-code branch, the only gap
test_dispatch.py didn't reach yet."""

import httpx

from orchestration import db, dispatch
from orchestration.models import Job, JobStatus


def mock_client_factory(handler):
    def _factory(socket_path, *, timeout=5.0):
        return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://pillar")

    return _factory


def test_dispatch_marks_failed_on_non_200_response(monkeypatch, tmp_path):
    conn = db.connect(tmp_path / "orchestration.db")

    def handler(request):
        return httpx.Response(500, text="internal error")

    monkeypatch.setattr(dispatch, "client_for_socket", mock_client_factory(handler))
    job = Job(type="noop", target_pillar="reference")
    db.insert_job(conn, job)
    result = dispatch.dispatch_job(conn, job, socket_lookup=lambda pillar: tmp_path / "ref.sock")

    assert result.status == JobStatus.FAILED
    assert "500" in result.error
    conn.close()
