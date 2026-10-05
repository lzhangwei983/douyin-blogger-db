import csv
import io
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app


HEADERS = ["作者昵称", "uid", "作者链接", "作者粉丝数", "视频标题", "视频标签", "视频链接",
           "发布时间", "视频时长", "播放量", "推荐数", "点赞数", "评论数", "收藏数", "转发数", "采集时间"]


def csv_bytes(rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "metrics-import-test.db"
    monkeypatch.setattr(app, "DB_PATH", db)
    app.init_db()
    with TestClient(app.app, base_url="http://127.0.0.1") as c:
        bid = c.post("/api/bloggers", json={"name": "现有库博主", "slug": "existing"}).json()["id"]
        c.post(f"/api/bloggers/{bid}/videos", json={"url": "https://www.douyin.com/video/80000001", "title": "现有库作品", "like_count": 9})
        yield c, db


def test_preview_confirm_and_compare_preserve_nullable_values(client, monkeypatch):
    c, db = client
    content = csv_bytes([
        ["甲博主", "uid-a", "https://www.douyin.com/user/uid-a", "5.6万", "样本一", "教程", "https://www.douyin.com/video/90000001", "2026-09-01", "1分20秒", "1.2万", "7", "0", "", "4", "3", "2026-09-02"],
        ["甲博主", "uid-a", "https://www.douyin.com/user/uid-a", "5.6万", "样本二", "教程", "https://www.douyin.com/video/90000002", "20260902", "00:42", "800", "5", "20", "4", "0", "", ""],
        ["乙博主", "uid-b", "https://www.douyin.com/user/uid-b", "0", "样本三", "评测", "https://www.douyin.com/video/90000003", "2026/09/03", "42", "", "", "5", "2", "1", "0", ""],
        ["", "", "", "", "无效行", "", "", "", "", "", "", "", "", "", "", ""],
    ])
    preview = c.post("/api/metrics/import/preview", data={"source_name": "采集工具A"}, files={"file": ("items.csv", content, "text/csv")})
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data["row_count"] == 3 and data["skipped_count"] == 1
    assert {"play_count", "recommend_count"}.issubset({field["field"] for field in data["fields"]})
    assert c.get("/api/metrics/imports").json() == []
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 1

    imported = c.post("/api/metrics/import/confirm", json={"preview_id": data["preview_id"]})
    assert imported.status_code == 200, imported.text
    batch_id = imported.json()["id"]
    accounts = c.get(f"/api/metrics/imports/{batch_id}/accounts").json()
    assert len(accounts) == 2
    compared = c.get("/api/metrics", params={"batch_id": batch_id, "limit": 20}).json()
    assert compared["total"] == 3
    assert compared["source_mode"] == "imported" and compared["nullable_metrics"] is True
    assert "play_count" in compared["available_fields"]
    first = next(row for row in compared["items"] if row["video_id"] == "90000001")
    assert first["play_count"] == 12000
    assert first["recommend_count"] == 7
    assert first["like_count"] == 0
    assert first["comment_count"] is None
    assert first["duration"] == 80
    assert first["captured_at"] == "20260902"
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM bloggers").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 1

    duplicate = c.post("/api/metrics/import/preview", data={"source_name": "另一个名字"}, files={"file": ("items.csv", content, "text/csv")})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["batch_id"] == batch_id
    export = c.get("/api/metrics/export.csv", params={"batch_id": batch_id})
    assert export.status_code == 200 and export.content.startswith(b"\xef\xbb\xbf")
    assert "播放量" in export.content.decode("utf-8-sig")


def test_import_rejects_bad_headers_and_cancel_discards_preview(client):
    c, _ = client
    bad = c.post("/api/metrics/import/preview", files={"file": ("bad.csv", b"x,y\n1,2\n", "text/csv")})
    assert bad.status_code == 400 and "博主字段" in bad.json()["detail"]
    content = csv_bytes([["甲", "uid", "", "", "标题", "", "", "", "", "", "0", "", "", "", ""]])
    preview = c.post("/api/metrics/import/preview", files={"file": ("cancel.tsv.csv", content, "text/csv")})
    assert preview.status_code == 200
    result = c.delete(f"/api/metrics/import/preview/{preview.json()['preview_id']}")
    assert result.status_code == 200 and result.json()["ok"]
    missing = c.post("/api/metrics/import/confirm", json={"preview_id": preview.json()["preview_id"]})
    assert missing.status_code == 400


def test_batch_delete_only_removes_imported_rows(client, monkeypatch):
    c, db = client
    monkeypatch.setattr(app, "backup_database", lambda *args, **kwargs: None)
    content = csv_bytes([["甲", "uid", "", "", "标题", "", "https://www.douyin.com/video/90000011", "", "", "", "0", "", "", "", ""]])
    preview = c.post("/api/metrics/import/preview", files={"file": ("one.csv", content, "text/csv")}).json()
    batch = c.post("/api/metrics/import/confirm", json={"preview_id": preview["preview_id"]}).json()
    assert c.delete(f"/api/metrics/imports/{batch['id']}").status_code == 200
    assert c.get("/api/metrics/imports").json() == []
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM metric_import_rows").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 1
