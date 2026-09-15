from common.logs import append_line, trim_to_size


def test_append_line_writes_and_reads_back(tmp_path):
    log = tmp_path / "core.log"
    append_line(log, "first event")
    append_line(log, "second event")
    assert log.read_text().splitlines() == ["first event", "second event"]


def test_trim_to_size_drops_oldest_lines_first(tmp_path):
    log = tmp_path / "core.log"
    log.write_text("".join(f"line-{i:04d}\n" for i in range(1000)), encoding="utf-8")
    original_size = log.stat().st_size

    trim_to_size(log, max_bytes=original_size // 4)

    remaining = log.read_text().splitlines()
    assert remaining[-1] == "line-0999"
    assert "line-0000" not in remaining
    assert log.stat().st_size <= original_size // 4


def test_trim_to_size_noop_when_under_budget(tmp_path):
    log = tmp_path / "core.log"
    log.write_text("small\n", encoding="utf-8")
    trim_to_size(log, max_bytes=10_000)
    assert log.read_text() == "small\n"


def test_trim_to_size_missing_file_is_noop(tmp_path):
    trim_to_size(tmp_path / "missing.log", max_bytes=10)  # must not raise


def test_append_line_enforces_rolling_window(tmp_path):
    log = tmp_path / "core.log"
    for i in range(200):
        append_line(log, f"event-{i}", max_bytes=500)
    assert log.stat().st_size <= 500
    assert "event-199" in log.read_text()
    assert "event-0\n" not in log.read_text()
