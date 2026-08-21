#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""API 回归测试：覆盖全部端点、导出 BOM、边界与 404"""
import sys, io, csv, json, time, urllib.request, pytest
from pathlib import Path
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app

@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr(app, "DB_PATH", db)
    app.init_db()
    with TestClient(app.app) as c:
        # 1 博主 + 2 视频（一条带字幕+分析）
        bid = c.post("/api/bloggers", json={"name": "测试博主", "slug": "testb", "platform": "抖音",
                                            "douyin_id": "123", "homepage_url": "https://www.douyin.com/user/123",
                                            "tags": "测试"}).json()["id"]
        v1 = c.post(f"/api/bloggers/{bid}/videos", json={
            "url": "https://www.douyin.com/video/1111111111111111111", "kind": "视频",
            "title": "视频一", "upload_date": "20260101", "like_count": 12345, "comment_count": 10,
            "subtitle": "这是一段测试字幕。"}).json()["id"]
        v2 = c.post(f"/api/bloggers/{bid}/videos", json={
            "url": "https://www.douyin.com/note/2222222222222222222", "kind": "图文",
            "title": "图文二", "upload_date": "20260202", "like_count": 5}).json()["id"]
        c.put(f"/api/videos/{v1}", json={"subtitle": "校对版：这是一段测试字幕。"})
        c.put(f"/api/videos/{v1}", json={"status": "empty"})
        con = app.get_db()
        con.execute(
            """INSERT INTO analyses(video_id,full_md,summary,key_points,advice,industries,risks,credibility,actionable,parsed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (v1, "md", "摘要", "观点", "建议", "行业", "风险", "中等", "值得", "2026-01-01 00:00:00"))
        con.execute("UPDATE videos SET has_analysis=1 WHERE id=?", (v1,))
        con.commit(); con.close()
        yield c, bid, v1, v2

# ---------- bloggers ----------
def test_list_bloggers(client):
    c, bid, _, _ = client
    r = c.get("/api/bloggers")
    assert r.status_code == 200
    b = r.json()[0]
    assert b["name"] == "测试博主"
    assert b["video_count"] == 2
    assert b["video_kind_count"] == 1
    assert b["note_count"] == 1
    assert b["subtitle_count"] == 1
    assert b["analyzed_count"] == 1
    assert b["max_likes"] == 12345
    assert b["hit_count"] == 1

def test_search_bloggers(client):
    c, _, _, _ = client
    assert len(c.get("/api/bloggers?q=测试").json()) == 1
    assert len(c.get("/api/bloggers?q=不存在").json()) == 0

def test_get_blogger_stats(client):
    c, bid, _, _ = client
    b = c.get(f"/api/bloggers/{bid}").json()
    s = b["stats"]
    assert s["video_count"] == 2
    assert s["days"] == 32  # 20260101 ~ 20260202

def test_get_blogger_404(client):
    c, _, _, _ = client
    assert c.get("/api/bloggers/999").status_code == 404

def test_create_blogger_duplicate_slug(client):
    c, _, _, _ = client
    r = c.post("/api/bloggers", json={"name": "重复", "slug": "testb"})
    assert r.status_code == 400

def test_update_blogger(client):
    c, bid, _, _ = client
    r = c.put(f"/api/bloggers/{bid}", json={"bio": "新简介", "tags": "a,b"})
    assert r.status_code == 200
    assert c.get(f"/api/bloggers/{bid}").json()["bio"] == "新简介"

def test_delete_blogger_cascade(client):
    c, bid, v1, _ = client
    assert c.delete(f"/api/bloggers/{bid}").status_code == 200
    assert c.get(f"/api/videos/{v1}").status_code == 404

# ---------- videos ----------
def test_list_videos_filters(client):
    c, bid, _, _ = client
    r = c.get(f"/api/bloggers/{bid}/videos")
    assert r.json()["total"] == 2
    assert c.get(f"/api/bloggers/{bid}/videos?kind=图文").json()["total"] == 1
    assert c.get(f"/api/bloggers/{bid}/videos?kind=视频").json()["total"] == 1
    assert c.get(f"/api/bloggers/{bid}/videos?analyzed=1").json()["total"] == 1
    assert c.get(f"/api/bloggers/{bid}/videos?analyzed=0").json()["total"] == 1
    assert c.get(f"/api/bloggers/{bid}/videos?sort=like_desc").json()["items"][0]["like_count"] == 12345
    assert c.get(f"/api/bloggers/{bid}/videos?q=图文").json()["total"] == 1

def test_create_video_extracts_id_and_seq(client):
    c, bid, _, _ = client
    vid = c.post(f"/api/bloggers/{bid}/videos", json={
        "url": "https://www.douyin.com/video/3333333333333333333"}).json()["id"]
    v = c.get(f"/api/videos/{vid}").json()["video"]
    assert v["video_id"] == "3333333333333333333"
    assert v["seq"] == 3
    # prev/next 导航
    assert c.get(f"/api/videos/{vid}").json()["prev_id"] is not None

def test_create_video_duplicate(client):
    c, bid, _, _ = client
    r = c.post(f"/api/bloggers/{bid}/videos", json={"url": "https://www.douyin.com/video/1111111111111111111"})
    assert r.status_code == 400

def test_get_video_detail(client):
    c, bid, v1, v2 = client
    d = c.get(f"/api/videos/{v1}").json()
    assert d["video"]["subtitle"] == "校对版：这是一段测试字幕。"
    assert d["video"]["subtitle_chars"] == 13
    assert d["analysis"]["summary"] == "摘要"
    assert d["blogger"]["name"] == "测试博主"
    assert d["next_id"] == v2
    assert d["prev_id"] is None

def test_get_video_404(client):
    c, _, _, _ = client
    assert c.get("/api/videos/9999").status_code == 404

def test_update_video_subtitle_updates_chars(client):
    c, _, v1, _ = client
    c.put(f"/api/videos/{v1}", json={"subtitle": "新字幕"})
    v = c.get(f"/api/videos/{v1}").json()["video"]
    assert v["subtitle_chars"] == 3

def test_delete_video(client):
    c, _, v2, _ = client
    assert c.delete(f"/api/videos/{v2}").status_code == 200
    assert c.get(f"/api/videos/{v2}").status_code == 404

# ---------- analysis ----------
def test_analysis_404(client):
    c, _, _, v2 = client
    assert c.get(f"/api/videos/{v2}/analysis").status_code == 404

# ---------- export ----------
def test_export_json(client):
    c, bid, _, _ = client
    r = c.get(f"/api/export?format=json&blogger_id={bid}")
    assert r.status_code == 200
    d = r.json()
    assert d[0]["name"] == "测试博主"
    assert len(d[0]["videos"]) == 2
    assert d[0]["videos"][0]["analysis"]["summary"] == "摘要"

def test_export_json_all(client):
    c, _, _, _ = client
    assert len(c.get("/api/export?format=json").json()) == 1

def test_export_csv_bom(client):
    c, bid, _, _ = client
    r = c.get(f"/api/export?format=csv&blogger_id={bid}")
    assert r.status_code == 200
    assert r.content[:3] == b"\xef\xbb\xbf"  # BOM
    rows = list(csv.DictReader(io.StringIO(r.text[1:]), delimiter=","))
    assert len(rows) == 2
    assert rows[0]["博主"] == "测试博主"
    assert rows[0]["点赞"] == "12345"

def test_export_csv_header_line(client):
    c, bid, _, _ = client
    r = c.get(f"/api/export?format=csv&blogger_id={bid}")
    first = r.text.splitlines()[0]
    assert "博主" in first and "字幕字数" in first

# ---------- download ----------
def test_download_404(client):
    c, _, _, _ = client
    assert c.get("/api/videos/9999/download").status_code == 404

def test_download_no_cookie(client, monkeypatch):
    c, _, v1, _ = client
    monkeypatch.setattr(app, "find_cookie", lambda: None)
    tid = c.get(f"/api/videos/{v1}/download").json()["task_id"]
    for _ in range(20):
        st = c.get(f"/api/tasks/{tid}").json()
        if st["status"] != "running":
            break
        time.sleep(0.2)
    assert st["status"] == "failed"
    assert "Cookie" in st["log"][-1]

def test_download_no_url(client, monkeypatch):
    c, _, v1, _ = client
    monkeypatch.setattr(app, "find_cookie", lambda: "x.txt")
    con = app.get_db()
    con.execute("UPDATE videos SET url='' WHERE id=?", (v1,))
    con.commit(); con.close()
    assert c.get(f"/api/videos/{v1}/download").status_code == 400

def test_task_status_404(client):
    c, _, _, _ = client
    assert c.get("/api/tasks/999").status_code == 404

def test_create_video_with_images(client):
    c, bid, _, _ = client
    vid = c.post(f"/api/bloggers/{bid}/videos", json={
        "url": "https://www.douyin.com/note/5555555555555555555", "kind": "图文",
        "images": '["https://a.com/1.jpg","https://a.com/2.jpg"]'}).json()["id"]
    v = c.get(f"/api/videos/{vid}").json()["video"]
    assert v["images"] == '["https://a.com/1.jpg","https://a.com/2.jpg"]'

def test_download_note_images(client, monkeypatch, tmp_path):
    c, _, v1, _ = client
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path)
    con = app.get_db()
    con.execute("UPDATE videos SET kind='图文', images='[\"https://a.com/1.jpg\",\"https://a.com/2.jpg\"]' WHERE id=?", (v1,))
    con.commit(); con.close()
    class FakeResp:
        def read(self): return b"\xff\xd8fake"
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResp())
    tid = c.get(f"/api/videos/{v1}/download").json()["task_id"]
    for _ in range(30):
        st = c.get(f"/api/tasks/{tid}").json()
        if st["status"] != "running":
            break
        time.sleep(0.2)
    assert st["status"] == "done"
    files = list((tmp_path / "1" / "01_note").glob("*.jpg"))
    assert len(files) == 2

# ---------- static ----------
def test_home_page(client):
    c, _, _, _ = client
    r = c.get("/")
    assert r.status_code == 200
    assert "抖音博主数据库" in r.text

def test_unknown_api_404(client):
    c, _, _, _ = client
    assert c.get("/api/nonexistent").status_code == 404