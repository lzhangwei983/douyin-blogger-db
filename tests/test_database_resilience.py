import sqlite3
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app


def test_sqlite_write_lock_returns_retryable_chinese_response(tmp_path, monkeypatch):
    database = tmp_path / "locked.sqlite"
    monkeypatch.setattr(app, "DB_PATH", database)
    app.init_db()
    blocker = sqlite3.connect(database, timeout=0, isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        with TestClient(app.app, base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
            started = time.monotonic()
            response = client.post("/api/bloggers", json={"name": "锁测试", "slug": "lock-test"})
            elapsed = time.monotonic() - started
        assert response.status_code == 503
        assert "数据库" in response.json()["detail"]
        assert "稍后重试" in response.json()["detail"]
        assert elapsed < 4.0
    finally:
        blocker.rollback()
        blocker.close()
