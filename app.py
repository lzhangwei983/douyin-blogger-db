#!/usr/bin/env python
# -*- coding: utf-8 -*-
# 免责声明：本文件仅供个人学习/研究/个人备份示例，禁止商用与再分发，使用者自负合规责任。详见 LICENSE / DISCLAIMER.md
"""抖音博主数据库 - FastAPI 后端 + SQLite"""
__version__ = "1.0.3"
import json, sqlite3, csv, io, re, sys, os, subprocess, time
import urllib.request
from datetime import datetime, date
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query, Body, Request
from fastapi.responses import Response, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

if getattr(sys, "frozen", False):
    BASE = Path(sys.executable).resolve().parent
    STATIC_DIR = Path(getattr(sys, "_MEIPASS", BASE)) / "static"
else:
    BASE = Path(__file__).resolve().parent
    STATIC_DIR = BASE / "static"
def _data_dir():
    cands = [Path("D:/DouyinBlogDB")] if sys.platform == "win32" else []
    cands.append(BASE)
    for p in cands:
        try:
            p.mkdir(parents=True, exist_ok=True)
            t = p / ".wtest"
            t.write_text("x")
            t.unlink()
            return p
        except OSError:
            continue
    return BASE

DATA_DIR = _data_dir()
DB_PATH = DATA_DIR / "douyin_blog.db"

app = FastAPI(title="抖音博主数据库")

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

def init_db():
    con = get_db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS bloggers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        platform TEXT DEFAULT '抖音',
        douyin_id TEXT,
        homepage_url TEXT,
        bio TEXT,
        notes TEXT,
        tags TEXT DEFAULT '',
        created_at TEXT,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        blogger_id INTEGER NOT NULL REFERENCES bloggers(id) ON DELETE CASCADE,
        seq INTEGER,
        video_id TEXT UNIQUE,
        url TEXT NOT NULL,
        kind TEXT DEFAULT '视频',
        title TEXT,
        upload_date TEXT,
        duration INTEGER,
        like_count INTEGER DEFAULT 0,
        comment_count INTEGER DEFAULT 0,
        repost_count INTEGER DEFAULT 0,
        save_count INTEGER DEFAULT 0,
        status TEXT DEFAULT 'ok',
        subtitle TEXT,
        subtitle_chars INTEGER DEFAULT 0,
        has_analysis INTEGER DEFAULT 0,
        images TEXT,
        notes TEXT,
        tags TEXT DEFAULT '',
        created_at TEXT,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INTEGER NOT NULL UNIQUE REFERENCES videos(id) ON DELETE CASCADE,
        full_md TEXT,
        summary TEXT, key_points TEXT, advice TEXT, industries TEXT,
        risks TEXT, credibility TEXT, actionable TEXT,
        parsed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_videos_blogger ON videos(blogger_id);
    CREATE INDEX IF NOT EXISTS idx_videos_upload ON videos(upload_date);
    """)
    # 迁移：旧库补充新列
    cols = {r[1] for r in con.execute("PRAGMA table_info(videos)")}
    if "images" not in cols:
        con.execute("ALTER TABLE videos ADD COLUMN images TEXT")
    con.commit()
    con.close()

init_db()

# ---------- models ----------
class BloggerIn(BaseModel):
    name: str
    slug: str = ""
    platform: str = "抖音"
    douyin_id: str = ""
    homepage_url: str = ""
    bio: str = ""
    notes: str = ""
    tags: str = ""

class VideoIn(BaseModel):
    url: str
    kind: str = "视频"
    title: str = ""
    upload_date: str = ""
    duration: int = 0
    like_count: int = 0
    comment_count: int = 0
    repost_count: int = 0
    save_count: int = 0
    status: str = "ok"
    subtitle: str = ""
    images: str = ""
    notes: str = ""
    tags: str = ""

class VideoPatch(BaseModel):
    title: str = None
    notes: str = None
    tags: str = None
    status: str = None
    subtitle: str = None

class BloggerPatch(BaseModel):
    name: str = None
    douyin_id: str = None
    homepage_url: str = None
    bio: str = None
    notes: str = None
    tags: str = None

# ---------- helpers ----------
def row_blogger(r):
    d = dict(r)
    return d

def row_video(r):
    d = dict(r)
    return d

# ---------- bloggers ----------
@app.get("/api/bloggers")
def list_bloggers(q: str = ""):
    con = get_db()
    sql = """SELECT b.*,
        (SELECT COUNT(*) FROM videos v WHERE v.blogger_id = b.id) AS video_count,
        (SELECT COUNT(*) FROM videos v WHERE v.blogger_id = b.id AND v.kind='视频') AS video_kind_count,
        (SELECT COUNT(*) FROM videos v WHERE v.blogger_id = b.id AND v.kind='图文') AS note_count,
        (SELECT COUNT(*) FROM videos v WHERE v.blogger_id = b.id AND v.subtitle IS NOT NULL AND v.subtitle != '') AS subtitle_count,
        (SELECT COUNT(*) FROM videos v WHERE v.blogger_id = b.id AND v.has_analysis=1) AS analyzed_count,
        (SELECT COALESCE(AVG(like_count),0) FROM videos v WHERE v.blogger_id = b.id AND v.like_count > 0) AS avg_likes,
        (SELECT COALESCE(MAX(like_count),0) FROM videos v WHERE v.blogger_id = b.id) AS max_likes,
        (SELECT COUNT(*) FROM videos v WHERE v.blogger_id = b.id AND v.like_count >= 10000) AS hit_count,
        (SELECT MAX(upload_date) FROM videos v WHERE v.blogger_id = b.id) AS latest_date,
        (SELECT MIN(upload_date) FROM videos v WHERE v.blogger_id = b.id) AS earliest_date
        FROM bloggers b"""
    where, args = "", []
    if q:
        where = " WHERE b.name LIKE ? OR b.tags LIKE ? OR b.douyin_id LIKE ?"
        like = f"%{q}%"
        args = [like, like, like]
    rows = con.execute(sql + where + " ORDER BY b.created_at DESC", args).fetchall()
    con.close()
    return [row_blogger(r) for r in rows]

@app.post("/api/bloggers")
def create_blogger(b: BloggerIn):
    slug = b.slug.strip() or re.sub(r"[^a-zA-Z0-9_-]", "", b.name.lower())
    con = get_db()
    try:
        cur = con.execute(
            "INSERT INTO bloggers(slug,name,platform,douyin_id,homepage_url,bio,notes,tags,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (slug, b.name, b.platform, b.douyin_id, b.homepage_url, b.bio, b.notes, b.tags, now(), now()))
        con.commit()
        vid = cur.lastrowid
    except sqlite3.IntegrityError:
        con.close()
        raise HTTPException(400, "slug 已存在")
    con.close()
    return {"id": vid}

@app.get("/api/bloggers/{bid}")
def get_blogger(bid: int):
    con = get_db()
    r = con.execute("SELECT * FROM bloggers WHERE id=?", (bid,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404, "博主不存在")
    stats = con.execute("""SELECT
        (SELECT COUNT(*) FROM videos WHERE blogger_id=?) AS video_count,
        (SELECT COUNT(*) FROM videos WHERE blogger_id=? AND kind='视频') AS video_kind_count,
        (SELECT COUNT(*) FROM videos WHERE blogger_id=? AND kind='图文') AS note_count,
        (SELECT COUNT(*) FROM videos WHERE blogger_id=? AND subtitle IS NOT NULL AND subtitle != '') AS subtitle_count,
        (SELECT COUNT(*) FROM videos WHERE blogger_id=? AND has_analysis=1) AS analyzed_count,
        (SELECT COALESCE(AVG(like_count),0) FROM videos WHERE blogger_id=? AND like_count>0) AS avg_likes,
        (SELECT COALESCE(MAX(like_count),0) FROM videos WHERE blogger_id=?) AS max_likes,
        (SELECT COUNT(*) FROM videos WHERE blogger_id=? AND like_count>=10000) AS hit_count,
        (SELECT MIN(upload_date) FROM videos WHERE blogger_id=?) AS earliest_date,
        (SELECT MAX(upload_date) FROM videos WHERE blogger_id=?) AS latest_date,
        (SELECT COALESCE(CAST(julianday(substr(MAX(upload_date),1,4)||'-'||substr(MAX(upload_date),5,2)||'-'||substr(MAX(upload_date),7,2))-julianday(substr(MIN(upload_date),1,4)||'-'||substr(MIN(upload_date),5,2)||'-'||substr(MIN(upload_date),7,2)) AS INTEGER),0) FROM videos WHERE blogger_id=?) AS days
        """, (bid,)*11).fetchone()
    con.close()
    return {**row_blogger(r), "stats": dict(stats)}

@app.put("/api/bloggers/{bid}")
def update_blogger(bid: int, b: BloggerPatch):
    con = get_db()
    fields, args = [], []
    for k in ("name", "douyin_id", "homepage_url", "bio", "notes", "tags"):
        v = getattr(b, k)
        if v is not None:
            fields.append(f"{k}=?")
            args.append(v)
    if not fields:
        con.close()
        return {"ok": True}
    fields.append("updated_at=?")
    args.append(now())
    args.append(bid)
    con.execute(f"UPDATE bloggers SET {','.join(fields)} WHERE id=?", args)
    con.commit()
    con.close()
    return {"ok": True}

@app.delete("/api/bloggers/{bid}")
def delete_blogger(bid: int):
    con = get_db()
    con.execute("DELETE FROM bloggers WHERE id=?", (bid,))
    con.commit()
    con.close()
    return {"ok": True}

# ---------- videos ----------
@app.get("/api/bloggers/{bid}/videos")
def list_videos(bid: int, q: str = "", kind: str = "", analyzed: str = "",
                sort: str = "upload_desc", limit: int = 1000, offset: int = 0):
    con = get_db()
    where, args = ["v.blogger_id=?"], [bid]
    if q:
        where.append("(v.title LIKE ? OR v.notes LIKE ? OR v.tags LIKE ?)")
        like = f"%{q}%"
        args += [like, like, like]
    if kind:
        where.append("v.kind=?")
        args.append(kind)
    if analyzed == "1":
        where.append("v.has_analysis=1")
    elif analyzed == "0":
        where.append("v.has_analysis=0")
    order = {
        "upload_desc": "v.upload_date DESC, v.seq DESC",
        "upload_asc": "v.upload_date ASC, v.seq ASC",
        "like_desc": "v.like_count DESC",
        "seq_asc": "v.seq ASC",
    }.get(sort, "v.upload_date DESC, v.seq DESC")
    rows = con.execute(
        f"SELECT v.*, a.credibility FROM videos v LEFT JOIN analyses a ON a.video_id=v.id WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ? OFFSET ?",
        args + [limit, offset]).fetchall()
    total = con.execute(f"SELECT COUNT(*) c FROM videos v WHERE {' AND '.join(where)}", args).fetchone()["c"]
    con.close()
    return {"items": [row_video(r) for r in rows], "total": total}

@app.post("/api/bloggers/{bid}/videos")
def create_video(bid: int, v: VideoIn):
    con = get_db()
    if not con.execute("SELECT 1 FROM bloggers WHERE id=?", (bid,)).fetchone():
        con.close()
        raise HTTPException(404, "博主不存在")
    m = re.search(r"(?:/video/|/note/)(\d+)", v.url)
    video_id = m.group(1) if m else None
    max_seq = con.execute("SELECT COALESCE(MAX(seq),0) FROM videos WHERE blogger_id=?", (bid,)).fetchone()[0]
    try:
        cur = con.execute(
            """INSERT INTO videos(blogger_id,seq,video_id,url,kind,title,upload_date,duration,
               like_count,comment_count,repost_count,save_count,status,subtitle,subtitle_chars,has_analysis,images,notes,tags,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?)""",
            (bid, max_seq + 1, video_id, v.url, v.kind, v.title, v.upload_date or None, v.duration,
             v.like_count, v.comment_count, v.repost_count, v.save_count, v.status,
             v.subtitle, len(v.subtitle or ""), v.images, v.notes, v.tags, now(), now()))
        con.commit()
        vid = cur.lastrowid
    except sqlite3.IntegrityError:
        con.close()
        raise HTTPException(400, "该视频已存在")
    con.close()
    return {"id": vid}

@app.get("/api/videos/{vid}")
def get_video(vid: int):
    con = get_db()
    r = con.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404, "视频不存在")
    a = con.execute("SELECT * FROM analyses WHERE video_id=?", (vid,)).fetchone()
    # 上下条
    blogger_id = r["blogger_id"]
    prev = con.execute("SELECT id FROM videos WHERE blogger_id=? AND seq<? ORDER BY seq DESC LIMIT 1", (blogger_id, r["seq"] or 999999)).fetchone()
    nxt = con.execute("SELECT id FROM videos WHERE blogger_id=? AND seq>? ORDER BY seq ASC LIMIT 1", (blogger_id, r["seq"] or 0)).fetchone()
    b = con.execute("SELECT id, name FROM bloggers WHERE id=?", (blogger_id,)).fetchone()
    con.close()
    return {"video": row_video(r), "analysis": dict(a) if a else None,
            "prev_id": prev["id"] if prev else None, "next_id": nxt["id"] if nxt else None,
            "blogger": dict(b)}

@app.put("/api/videos/{vid}")
def update_video(vid: int, p: VideoPatch):
    con = get_db()
    fields, args = [], []
    if p.title is not None:
        fields.append("title=?"); args.append(p.title)
    if p.notes is not None:
        fields.append("notes=?"); args.append(p.notes)
    if p.tags is not None:
        fields.append("tags=?"); args.append(p.tags)
    if p.status is not None:
        fields.append("status=?"); args.append(p.status)
    if p.subtitle is not None:
        fields.append("subtitle=?"); args.append(p.subtitle)
        fields.append("subtitle_chars=?"); args.append(len(p.subtitle))
    if not fields:
        con.close()
        return {"ok": True}
    fields.append("updated_at=?")
    args.append(now())
    args.append(vid)
    con.execute(f"UPDATE videos SET {','.join(fields)} WHERE id=?", args)
    con.commit()
    con.close()
    return {"ok": True}

@app.delete("/api/videos/{vid}")
def delete_video(vid: int):
    con = get_db()
    con.execute("DELETE FROM videos WHERE id=?", (vid,))
    con.commit()
    con.close()
    return {"ok": True}

# ---------- analysis ----------
@app.get("/api/videos/{vid}/analysis")
def get_analysis(vid: int):
    con = get_db()
    a = con.execute("SELECT * FROM analyses WHERE video_id=?", (vid,)).fetchone()
    con.close()
    if not a:
        raise HTTPException(404, "无分析")
    return dict(a)

# ---------- export ----------
@app.get("/api/daily")
def api_daily(limit: int = 1):
    daily_dir = Path(r"D:/DouyinBlogDB/daily/report")
    if not daily_dir.exists():
        return {"items": []}
    files = sorted(daily_dir.glob("*.md"), reverse=True)[:limit]
    out = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        topics, cur = [], None
        for line in text.splitlines():
            if line.startswith("## "):
                cur = {"name": line[3:].strip(), "items": []}
                topics.append(cur)
            elif cur is not None and "💬" in line:
                m2 = re.match(r"^\s*-\s*💬\s*(.*)$", line)
                if cur["items"] and m2:
                    cur["items"][-1]["summary"] = m2.group(1).strip()
            elif cur is not None and line.startswith("- ["):
                m = re.match(r"- \[(.*?)\]\((.*?)\)\s*—\s*(.*)$", line)
                if m:
                    meta = m.group(3).split(" · ")
                    cur["items"].append({
                        "title": m.group(1), "url": m.group(2),
                        "author": meta[0] if len(meta) > 0 else "",
                        "platform": meta[1] if len(meta) > 1 else "",
                        "hot": meta[2] if len(meta) > 2 else "",
                        "date": meta[3] if len(meta) > 3 else "",
                    })
        out.append({"date": f.stem, "topics": topics})
    return {"items": out}

@app.get("/api/daily/{date}/raw")
def api_daily_raw(date: str):
    import re
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise HTTPException(status_code=400, detail="Invalid date")
    daily_dir = Path(r"D:/DouyinBlogDB/daily/report")
    md = daily_dir / f"{date}.md"
    if not md.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return {"content": md.read_text(encoding="utf-8")}

@app.delete("/api/daily/{date}")
def api_delete_daily(date: str):
    import shutil, re
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise HTTPException(status_code=400, detail="Invalid date")
    daily_dir = Path(r"D:/DouyinBlogDB/daily/report")
    removed = []
    for pat in [f"{date}.md", f"{date}_candidates.json", f"{date}_pending.json", f"{date}_summary.json"]:
        p = daily_dir / pat
        if p.exists():
            p.unlink()
            removed.append(pat)
    # transcripts dir
    tr = daily_dir / f"{date}_transcripts"
    if tr.exists():
        shutil.rmtree(tr)
        removed.append(f"{date}_transcripts/")
    exp = daily_dir / "exports" / f"每日信息差_{date}.html"
    if exp.exists():
        exp.unlink()
        removed.append(exp.name)
    if not removed:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True, "removed": removed}

@app.put("/api/daily/{date}")
async def api_update_daily(date: str, request: Request):
    import re
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise HTTPException(status_code=400, detail="Invalid date")
    body = await request.json()
    content = body.get("content", "")
    if not content.strip():
        raise HTTPException(status_code=400, detail="Empty content")
    daily_dir = Path(r"D:/DouyinBlogDB/daily/report")
    md_path = daily_dir / f"{date}.md"
    md_path.write_text(content, encoding="utf-8")
    return {"ok": True}

@app.get("/api/version")
def api_version():
    cur = __version__
    latest = cur
    url = "https://github.com/lzhangwei983/douyin-blogger-db/releases/latest"
    try:
        req = urllib.request.Request("https://api.github.com/repos/lzhangwei983/douyin-blogger-db/releases/latest",
                                      headers={"User-Agent": "douyin-blogger-db", "Accept": "application/vnd.github.v3+json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            latest = (data.get("tag_name") or cur).lstrip("v")
            url = data.get("html_url") or url
    except Exception:
        pass
    def parse(v): 
        try: return [int(x) for x in re.findall(r"\d+", v)]
        except: return [0]
    is_old = parse(latest) > parse(cur)
    return {"current": cur, "latest": latest, "is_old": is_old, "url": url}

_DAILY_CSS = """
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;max-width:840px;margin:0 auto;padding:24px;color:#1c232c;background:#f7f7f8;line-height:1.7}
h1{font-size:22px;border-bottom:2px solid #2f6fdb;padding-bottom:8px;margin-bottom:4px}
.sub{color:#7d828c;font-size:12px;margin-bottom:8px}
.topic{background:#fff;border:1px solid #e0e2e6;border-radius:10px;padding:14px 16px;margin:14px 0}
.topic h2{font-size:16px;margin:0 0 10px;color:#2f6fdb}
.item{border-top:1px solid #eee;padding:10px 0}
.item:first-child{border-top:none}
.t{font-weight:600;font-size:14px}
.t a{color:#2f6fdb;text-decoration:none}
.t a:hover{text-decoration:underline}
.m{color:#7d828c;font-size:12px;margin:2px 0}
.s{font-size:13px;color:#333;margin-top:5px;background:#f3f6fb;padding:8px 10px;border-radius:6px;border-left:3px solid #2f6fdb}
footer{color:#999;font-size:12px;margin-top:20px;text-align:center}
"""

@app.get("/api/daily/export")
def api_daily_export(date: str = ""):
    daily_dir = Path(r"D:/DouyinBlogDB/daily/report")
    if not date:
        cands = sorted(daily_dir.glob("*_candidates.json"), reverse=True)
        if cands:
            date = cands[0].stem.replace("_candidates", "")
    md = daily_dir / f"{date}.md"
    if not md.exists():
        return {"ok": False, "msg": f"未找到 {date}.md"}
    topics, cur = [], None
    for line in md.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            cur = {"name": line[3:].strip(), "items": []}
            topics.append(cur)
        elif cur is not None and "💬" in line:
            m2 = re.match(r"^\s*-\s*💬\s*(.*)$", line)
            if cur["items"] and m2:
                cur["items"][-1]["summary"] = m2.group(1).strip()
        elif cur is not None and line.startswith("- ["):
            m = re.match(r"- \[(.*?)\]\((.*?)\)\s*—\s*(.*)$", line)
            if m:
                meta = m.group(3).split(" · ")
                cur["items"].append({"title": m.group(1), "url": m.group(2),
                                     "author": meta[0] if len(meta) > 0 else "",
                                     "platform": meta[1] if len(meta) > 1 else "",
                                     "hot": meta[2] if len(meta) > 2 else "",
                                     "date": meta[3] if len(meta) > 3 else ""})
    esc = lambda s: (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    body = ""
    for t in topics:
        body += f'<section class="topic"><h2>{esc(t["name"])}</h2>'
        for it in t["items"]:
            link = f'<a href="{esc(it["url"])}" target="_blank" rel="noopener">{esc(it["title"])}</a>' if it["url"] else esc(it["title"])
            meta = " · ".join(x for x in [it["author"], it["platform"],
                                          ("热度" + it["hot"] if it["hot"] else ""), it["date"]] if x)
            body += f'<div class="item"><div class="t">{link}</div>'
            if meta:
                body += f'<div class="m">{esc(meta)}</div>'
            if it.get("summary"):
                body += f'<div class="s">{esc(it["summary"])}</div>'
            body += "</div>"
        body += "</section>"
    html = (f'<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>每日 AI 信息差 · {esc(date)}</title><style>{_DAILY_CSS}</style></head>'
            f'<body><h1>📡 每日 AI 信息差 · {esc(date)}</h1>'
            f'<div class="sub">由「抖音博主数据库」生成 · 共 {len(topics)} 个主题</div>'
            f'{body}<footer>抖音博主数据库 · 自动生成</footer></body></html>')
    out_dir = daily_dir / "exports"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"每日信息差_{date}.html"
    out.write_text(html, encoding="utf-8")
    return {"ok": True, "path": str(out), "date": date}

def _proc_alive(pid):
    try:
        if os.name == "nt":
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                               capture_output=True, text=True,
                               creationflags=0x08000000)
            return str(pid) in r.stdout
        return pid > 0 and Path(f"/proc/{pid}").exists()
    except Exception:
        return False

def _tsv_total(slug):
    tsv = Path(rf"D:/DouyinBlogDB/work/{slug}_videos.tsv")
    if not tsv.exists():
        return 0
    try:
        with tsv.open(encoding="utf-8") as f:
            return max(sum(1 for _ in f) - 1, 0)
    except Exception:
        return 0

@app.get("/api/pipeline/status")
def api_pipeline_status():
    pipes = []
    out_root = Path(r"D:/DouyinBlogDB/outputs")
    if out_root.exists():
        for d in sorted(out_root.iterdir()):
            if not d.is_dir():
                continue
            pid_f = d / "pipeline.pid"
            txt_dir = d / "txt"
            if not pid_f.exists() and not txt_dir.exists():
                continue
            done = len(list(txt_dir.glob("*.txt"))) if txt_dir.exists() else 0
            total = _tsv_total(d.name)
            t_f = d / "todo.json"
            skip_n = 0
            if t_f.exists():
                try:
                    skip_n = len(json.loads(t_f.read_text(encoding="utf-8")).get("skip") or [])
                except Exception:
                    skip_n = 0
            alive = False
            if pid_f.exists():
                try:
                    alive = _proc_alive(int(pid_f.read_text().strip()))
                except Exception:
                    alive = False
            log_lines = []
            log = d / "pipeline.log"
            if log.exists():
                log_lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-6:]
            pipes.append({"name": d.name, "done": done, "total": total, "skip": skip_n,
                          "running": alive, "log_tail": log_lines})
    # 信息差转写进度
    daily = {"date": None, "transcribed": 0, "candidates": 0, "summarized": 0, "running": False}
    daily_dir = Path(r"D:/DouyinBlogDB/daily/report")
    if daily_dir.exists():
        cands = sorted(daily_dir.glob("*_candidates.json"), reverse=True)
        if cands:
            date = cands[0].stem.replace("_candidates", "")
            daily["date"] = date
            try:
                c = json.loads(cands[0].read_text(encoding="utf-8"))
                daily["candidates"] = sum(len(t.get("items") or []) for t in c.get("topics") or [])
            except Exception:
                pass
            tr_dir = daily_dir / f"{date}_transcripts"
            if tr_dir.exists():
                daily["transcribed"] = len(list(tr_dir.glob("*.txt")))
            pend = daily_dir / f"{date}_pending.json"
            if pend.exists():
                try:
                    daily["transcribed"] = len(json.loads(pend.read_text(encoding="utf-8")).get("items") or [])
                except Exception:
                    pass
            sum_f = daily_dir / f"{date}_summary.json"
            if sum_f.exists():
                try:
                    daily["summarized"] = len(json.loads(sum_f.read_text(encoding="utf-8")))
                except Exception:
                    pass
    # 信息差转写是否在运行（daily_summary 写的 flag，10 分钟内新鲜算运行中）
    flag = daily_dir / "transcribing.flag"
    if flag.exists():
        try:
            fresh = (time.time() - flag.stat().st_mtime) < 600
        except Exception:
            fresh = False
        daily["running"] = fresh
    return {"pipes": pipes, "daily": daily}

@app.get("/api/settings")
def api_settings():
    cfg = {"whisper": {"num_workers": 4, "beam_size": 1,
                       "concurrency": 1, "sleep_min": 1, "sleep_max": 3}}
    p = Path(r"D:/DouyinBlogDB/daily/config.json")
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            cfg.update({k: v for k, v in d.items() if k in cfg and k != "whisper"})
            cfg["whisper"].update(d.get("whisper") or {})
        except Exception:
            pass
    return cfg

@app.put("/api/settings")
async def api_save_settings(request: Request):
    body = await request.json()
    p = Path(r"D:/DouyinBlogDB/daily/config.json")
    d = {}
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            d = {}
    wh = d.setdefault("whisper", {})
    for k in ("num_workers", "beam_size", "concurrency", "sleep_min", "sleep_max"):
        if k in body and isinstance(body[k], (int, float)):
            wh[k] = max(1, int(body[k]))
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "saved": d}

@app.get("/api/queue")
def api_queue():
    """转写队列：未转写条目 + 当前调控(todo.json)"""
    out = []
    tsv = Path(r"D:/DouyinBlogDB/work/lisziran_videos.tsv")
    txt_dir = Path(r"D:/DouyinBlogDB/outputs/lisziran/txt")
    if tsv.exists():
        try:
            rows = list(csv.DictReader(open(tsv, encoding="utf-8"), delimiter="\t"))
            for r in rows:
                seq = int(r.get("序号") or 0)
                url = r.get("链接", "").strip()
                m = re.search(r"(?:/video/|/note/)(\d+)", url)
                vid = m.group(1) if m else "unknown"
                done = (txt_dir / f"{seq:02d}_video_{vid}_subtitle.txt").exists()
                out.append({"seq": seq, "vid": vid, "title": r.get("标题", "") or "",
                            "url": url, "done": done})
        except Exception:
            pass
    todo = {"order": [], "skip": []}
    tp = Path(r"D:/DouyinBlogDB/outputs/lisziran/todo.json")
    if tp.exists():
        try:
            todo = json.loads(tp.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"items": out, "todo": todo}

@app.put("/api/queue")
async def api_save_queue(request: Request):
    body = await request.json()
    tp = Path(r"D:/DouyinBlogDB/outputs/lisziran/todo.json")
    tp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "saved": body}

@app.get("/api/schedule")
def api_schedule():
    """项目调度：顺序/启停 + 实时状态"""
    p = Path(r"D:/DouyinBlogDB/daily/schedule.json")
    items = []
    completed = {}
    failed = {}
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            items = d.get("items", [])
            completed = d.get("completed", {})
            failed = d.get("failed", {})
        except Exception:
            pass
    if not items:
        items = [{"id": "lisziran", "enabled": True, "state": "auto"},
                 {"id": "daily", "enabled": True, "state": "auto"}]
    # 运行时状态
    def _skip_count(out_dir):
        t = out_dir / "todo.json"
        if t.exists():
            try:
                return len(json.loads(t.read_text(encoding="utf-8")).get("skip") or [])
            except Exception:
                return 0
        return 0
    def st_lisziran():
        pid_f = Path(r"D:/DouyinBlogDB/outputs/lisziran/pipeline.pid")
        running = False
        if pid_f.exists():
            try:
                running = _proc_alive(int(pid_f.read_text().strip()))
            except Exception:
                running = False
        txt = len(list(Path(r"D:/DouyinBlogDB/outputs/lisziran/txt").glob("*.txt")))
        rows = _tsv_total("lisziran")
        skip = _skip_count(Path(r"D:/DouyinBlogDB/outputs/lisziran"))
        return {"running": running, "progress": f"{txt}/{rows}", "done": (txt + skip) >= rows,
                "detail": f"{txt} 条字幕 / TSV {rows} 条（{skip} 条跳过）" if skip else f"{txt} 条字幕 / TSV {rows} 条"}
    def st_daily():
        rp = Path(r"D:/DouyinBlogDB/daily/report")
        flag = rp / "transcribing.flag"
        running = False
        if flag.exists():
            try:
                running = (time.time() - flag.stat().st_mtime) < 600
            except Exception:
                running = False
        cands = sorted(rp.glob("*_candidates.json"), reverse=True)
        if not cands:
            return {"running": running, "progress": "无任务", "done": True, "detail": "今日无候选"}
        date = cands[0].stem.replace("_candidates", "")
        tr = rp / f"{date}_transcripts"
        n = len(list(tr.glob("*.txt"))) if tr.exists() else 0
        try:
            c = json.loads(cands[0].read_text(encoding="utf-8"))
            tot = sum(len(t.get("items") or []) for t in c.get("topics") or [])
        except Exception:
            tot = n
        return {"running": running, "progress": f"{n}/{tot}", "done": n >= tot,
                "detail": f"{date} · 已转写 {n} / 候选 {tot}"}
    names = {"lisziran": "李自然说 · 字幕转写", "daily": "每日信息差 · 候选转写"}
    funcs = {"lisziran": st_lisziran, "daily": st_daily}
    out = []
    for it in items:
        f = funcs.get(it.get("id"))
        st = f() if f else {"running": False, "progress": "", "done": True, "detail": ""}
        fid = it.get("id")
        fmeta = failed.get(fid)
        if fmeta and not st["running"] and not st["done"]:
            st["detail"] = f"失败 {fmeta.get('count', 0)} 次，5 分钟后自动重试"
        if completed.get(fid):
            st["detail"] = st.get("detail") or "已完成，12h 内不自动重跑"
        out.append({**it, "name": names.get(fid, fid), **st})
    return {"items": out, "completed": completed, "failed": failed}

@app.put("/api/schedule")
async def api_save_schedule(request: Request):
    body = await request.json()
    p = Path(r"D:/DouyinBlogDB/daily/schedule.json")
    d = {"items": body.get("items", []), "completed": body.get("completed", {})}
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "saved": d}

@app.get("/api/export")
def export(format: str = "json", blogger_id: int = None):
    con = get_db()
    if blogger_id:
        bloggers = con.execute("SELECT * FROM bloggers WHERE id=?", (blogger_id,)).fetchall()
    else:
        bloggers = con.execute("SELECT * FROM bloggers").fetchall()
    data = []
    for b in bloggers:
        videos = con.execute("SELECT * FROM videos WHERE blogger_id=? ORDER BY seq ASC", (b["id"],)).fetchall()
        items = []
        for v in videos:
            a = con.execute("SELECT * FROM analyses WHERE video_id=?", (v["id"],)).fetchone()
            item = {k: v[k] for k in v.keys()}
            item.pop("blogger_id", None)
            item.pop("id", None)
            item["analysis"] = dict(a) if a else None
            items.append(item)
        bd = {k: b[k] for k in b.keys()}
        bd.pop("id", None)
        bd["videos"] = items
        data.append(bd)
    con.close()
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["博主", "抖音ID", "序号", "类型", "视频ID", "链接", "标题", "发布日期", "时长", "点赞", "评论", "转发", "收藏", "状态", "有无分析", "可信度判断", "字幕字数"])
        for bd in data:
            for v in bd["videos"]:
                w.writerow([bd["name"], bd.get("douyin_id",""), v.get("seq",""), v.get("kind",""),
                            v.get("video_id",""), v.get("url",""), v.get("title",""), v.get("upload_date",""),
                            v.get("duration",0), v.get("like_count",0), v.get("comment_count",0),
                            v.get("repost_count",0), v.get("save_count",0), v.get("status",""),
                            1 if v.get("has_analysis") else 0,
                            (v.get("analysis") or {}).get("credibility","")[:200] if v.get("analysis") else "",
                            v.get("subtitle_chars",0)])
        fname = f"douyin_blog_export_{date.today().isoformat()}.csv"
        return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})
    fname = f"douyin_blog_export_{date.today().isoformat()}.json"
    return Response(json.dumps(data, ensure_ascii=False, indent=1),
                    media_type="application/json; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})

# ---------- download ----------
import threading
import yt_dlp

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
DOWNLOAD_DIR = DATA_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
_tasks = {}
_tid = 0

def find_cookie():
    for c in (DATA_DIR / "douyin_cookies.txt", BASE / "douyin_cookies.txt", BASE / "work" / "douyin_cookies.txt"):
        if c.exists():
            return str(c)
    return None

@app.get("/api/videos/{vid}/download")
def download_video(vid: int):
    global _tid
    con = get_db()
    r = con.execute("SELECT url, video_id, seq, blogger_id, kind, images FROM videos WHERE id=?", (vid,)).fetchone()
    con.close()
    if not r:
        raise HTTPException(404, "视频不存在")
    if not r["url"]:
        raise HTTPException(400, "该视频没有链接")
    _tid += 1
    tid = _tid
    _tasks[tid] = {"status": "running", "log": [], "path": None}
    target = DOWNLOAD_DIR / str(r["blogger_id"])
    target.mkdir(parents=True, exist_ok=True)

    def run_images():
        import json as _json
        try:
            urls = _json.loads(r["images"] or "[]")
            if not urls:
                raise RuntimeError("该图文没有图片链接（需重新采集）")
            seq = (r["seq"] or 0)
            folder = target / f"{seq:02d}_note"
            folder.mkdir(parents=True, exist_ok=True)
            saved = []
            for i, u in enumerate(urls, 1):
                req = urllib.request.Request(u, headers={"User-Agent": UA, "Referer": "https://www.douyin.com/"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = resp.read()
                ext = ".jpg"
                path = folder / f"{i:02d}{ext}"
                path.write_bytes(data)
                saved.append(str(path))
            _tasks[tid]["path"] = str(folder)
            _tasks[tid]["status"] = "done"
            _tasks[tid]["log"] = [f"共 {len(saved)} 张图片"]
        except Exception as e:
            _tasks[tid]["status"] = "failed"
            _tasks[tid]["log"].append(str(e)[:300])

    def run_video():
        ck = find_cookie()
        if not ck:
            _tasks[tid]["status"] = "failed"
            _tasks[tid]["log"].append("未找到 Cookie：请将 douyin_cookies.txt 放到程序目录或 work/ 下")
            return
        opts = {
            "outtmpl": str(target / f"{r['seq']:02d}_{r['video_id'] or '%(id)s'}.%(ext)s"),
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "cookiefile": ck,
            "format": "best[height<=720]/best",
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(r["url"], download=True)
            f = ydl.prepare_filename(info)
            _tasks[tid]["path"] = f
            _tasks[tid]["status"] = "done"
        except Exception as e:
            _tasks[tid]["status"] = "failed"
            _tasks[tid]["log"].append(str(e)[:300])

    fn = run_images if r["kind"] == "图文" else run_video
    threading.Thread(target=fn, daemon=True).start()
    return {"task_id": tid}

@app.get("/api/tasks/{tid}")
def task_status(tid: int):
    t = _tasks.get(tid)
    if not t:
        raise HTTPException(404, "任务不存在")
    return t

# ---------- static ----------
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    print("抖音博主数据库已启动: http://127.0.0.1:8321")
    uvicorn.run(app, host="127.0.0.1", port=8321)
