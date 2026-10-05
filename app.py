#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Local Douyin creator/video collection, library, and metrics API."""
__version__ = "1.1.0"
import json, sqlite3, csv, io, re, sys, os, subprocess, time
import urllib.request
from urllib.parse import urlsplit
from datetime import datetime, date
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query, Body, Request, UploadFile, File, Form
from fastapi.responses import Response, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

if getattr(sys, "frozen", False):
    BASE = Path(sys.executable).resolve().parent
    STATIC_DIR = Path(getattr(sys, "_MEIPASS", BASE)) / "static"
else:
    BASE = Path(__file__).resolve().parent
    STATIC_DIR = BASE / "static"

_embedded_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
sys.path.insert(0, str(_embedded_root / "core"))
sys.path.insert(0, str(_embedded_root / "work"))
from _paths import CODE_ROOT, CODE_CORE_DIR, CODE_WORK_DIR, APP_STATE_DIR, CONFIG_JSON, LEGACY_CONFIG_JSON, get_data_dir, get_python, read_public_config
sys.path.insert(0, str(CODE_CORE_DIR))

def _data_dir():
    return get_data_dir()


DATA_DIR = _data_dir()
DATA_DIR.mkdir(parents=True,exist_ok=True)
os.environ['DYDB_HOME']=str(DATA_DIR)
from storage import write_json,read_json,resource_lock,backup_database
from metrics_service import query_metrics, export_csv as export_metrics_csv, safe_csv_value
import metrics_import_service as metrics_import

DB_PATH = DATA_DIR / "douyin_blog.db"
SCHEMA_VERSION = 1

app = FastAPI(title="抖音博主数据库")


@app.middleware("http")
async def protect_local_api_from_cross_origin_writes(request: Request, call_next):
    if request.url.path.startswith('/api/') and request.method in {'POST','PUT','PATCH','DELETE'}:
        host=(request.url.hostname or '').lower()
        if host not in {'localhost','127.0.0.1','::1'}:
            return JSONResponse(status_code=403,content={'detail':'只允许从本机访问此接口'})
        origin=request.headers.get('origin')
        if origin:
            parsed=urlsplit(origin)
            origin_host=(parsed.hostname or '').lower()
            expected_port=request.url.port or (443 if request.url.scheme=='https' else 80)
            origin_port=parsed.port or (443 if parsed.scheme=='https' else 80)
            if (parsed.scheme not in {'http','https'} or parsed.username or parsed.password
                    or origin_host not in {'localhost','127.0.0.1','::1'}
                    or origin_host!=host or origin_port!=expected_port):
                return JSONResponse(status_code=403,content={'detail':'已拒绝来自其他网站的本机写入请求'})
    return await call_next(request)


@app.exception_handler(sqlite3.OperationalError)
async def sqlite_operational_error(request: Request, exc: sqlite3.OperationalError):
    message = str(exc).lower()
    if "locked" in message or "busy" in message:
        return JSONResponse(status_code=503, content={"detail": "数据库正在被另一个任务使用，请稍后重试"},
                            headers={"Retry-After": "2"})
    return JSONResponse(status_code=500, content={"detail": "本机数据库操作失败，请检查数据库文件和目录权限"})

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_db():
    con = sqlite3.connect(DB_PATH, timeout=2.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 2000")
    return con


def _database_needs_migration_backup(path):
    if not Path(path).is_file() or Path(path).stat().st_size == 0:
        return False
    with sqlite3.connect(path, timeout=2) as probe:
        version = probe.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise sqlite3.DatabaseError("数据库版本高于当前程序，请使用对应的新版应用")
        if version < SCHEMA_VERSION:
            return True
        tables = {row[0] for row in probe.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'bloggers', 'videos', 'analyses'} <= tables:
            return True
        expected = {'bloggers': {'manual_fields'}, 'videos': {'images', 'manual_fields'}}
        for table, columns in expected.items():
            actual = {row[1] for row in probe.execute('PRAGMA table_info(' + table + ')')}
            if not columns <= actual:
                return True
    return False


def init_db():
    try:
        if _database_needs_migration_backup(DB_PATH):
            backup_database(DB_PATH, "before-schema-migration")
    except sqlite3.Error as error:
        raise sqlite3.DatabaseError("数据库结构无法安全升级；请检查文件是否被占用，原数据库未迁移") from error
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
    metrics_import.init_schema(con)
    # 迁移：旧库补充新列
    cols = {r[1] for r in con.execute("PRAGMA table_info(videos)")}
    if "images" not in cols:
        con.execute("ALTER TABLE videos ADD COLUMN images TEXT")
    for table in ('bloggers','videos'):
        columns={r[1] for r in con.execute('PRAGMA table_info('+table+')')}
        if 'manual_fields' not in columns:con.execute("ALTER TABLE "+table+" ADD COLUMN manual_fields TEXT DEFAULT '[]'")
    con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
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


def normalize_public_video_url(value):
    from fetch_user_videos import CollectionError
    from video_collection_service import normalize_video_url
    try:
        return normalize_video_url(value)
    except CollectionError as error:
        raise HTTPException(400, str(error)) from None


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
    if not b.name.strip():raise HTTPException(400,'名称不能为空')
    import uuid
    slug = b.slug.strip() or re.sub(r"[^a-zA-Z0-9_-]", "", b.name.lower()) or 'blogger-'+uuid.uuid4().hex[:12]
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',slug):raise HTTPException(400,'博主标识仅可使用字母、数字、下划线和连字符（最多64位）')
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
    table="videos" if "vid" in locals() else "bloggers"
    rid=vid if "vid" in locals() else bid
    existing=con.execute('SELECT manual_fields FROM '+table+' WHERE id=?',(rid,)).fetchone()
    if not existing:con.close();raise HTTPException(404,'记录不存在')
    edited=set(json.loads(existing['manual_fields'] or '[]'))
    edited.update(f.split('=')[0] for f in fields if f.split('=')[0]!='subtitle_chars')
    fields.append('manual_fields=?');args.append(json.dumps(sorted(edited)))
    fields.append("updated_at=?")
    args.append(now())
    args.append(bid)
    con.execute(f"UPDATE bloggers SET {','.join(fields)} WHERE id=?", args)
    con.commit()
    con.close()
    return {"ok": True}

@app.delete("/api/bloggers/{bid}")
def delete_blogger(bid: int):
    backup_database(DB_PATH,"before-delete")
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
        where.append("(v.title LIKE ? OR v.notes LIKE ? OR v.tags LIKE ? OR v.subtitle LIKE ? OR v.id IN (SELECT video_id FROM analyses WHERE full_md LIKE ?))")
        like = f"%{q}%"
        args += [like]*5
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
    safe_url=normalize_public_video_url(v.url)
    con = get_db()
    if not con.execute("SELECT 1 FROM bloggers WHERE id=?", (bid,)).fetchone():
        con.close()
        raise HTTPException(404, "博主不存在")
    m = re.search(r"(?:/video/|/note/)(\d+)", safe_url)
    video_id = m.group(1) if m else None
    max_seq = con.execute("SELECT COALESCE(MAX(seq),0) FROM videos WHERE blogger_id=?", (bid,)).fetchone()[0]
    try:
        cur = con.execute(
            """INSERT INTO videos(blogger_id,seq,video_id,url,kind,title,upload_date,duration,
               like_count,comment_count,repost_count,save_count,status,subtitle,subtitle_chars,has_analysis,images,notes,tags,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?)""",
            (bid, max_seq + 1, video_id, safe_url, v.kind, v.title, v.upload_date or None, v.duration,
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
    table="videos" if "vid" in locals() else "bloggers"
    rid=vid if "vid" in locals() else bid
    existing=con.execute('SELECT manual_fields FROM '+table+' WHERE id=?',(rid,)).fetchone()
    if not existing:con.close();raise HTTPException(404,'记录不存在')
    edited=set(json.loads(existing['manual_fields'] or '[]'))
    edited.update(f.split('=')[0] for f in fields if f.split('=')[0]!='subtitle_chars')
    fields.append('manual_fields=?');args.append(json.dumps(sorted(edited)))
    fields.append("updated_at=?")
    args.append(now())
    args.append(vid)
    con.execute(f"UPDATE videos SET {','.join(fields)} WHERE id=?", args)
    con.commit()
    con.close()
    return {"ok": True}

@app.delete("/api/videos/{vid}")
def delete_video(vid: int):
    backup_database(DB_PATH,"before-delete")
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










def _proc_alive(pid, expected_script=None, created_at=None):
    from process_identity import process_matches_pid
    return process_matches_pid(pid, expected_script, created_at)

def _tsv_total(slug):
    tsv = (DATA_DIR / "work" / f"{slug}_videos.tsv")
    if not tsv.exists():
        return 0
    try:
        with tsv.open(encoding="utf-8") as f:
            return max(sum(1 for _ in f) - 1, 0)
    except Exception:
        return 0

@app.get("/api/pipeline/status")
def api_pipeline_status():
    pipes=[]
    output_root=DATA_DIR/"outputs"
    if output_root.exists():
        from process_identity import process_file_is_running
        for folder in sorted(output_root.iterdir()):
            if not folder.is_dir():
                continue
            pid_file=folder/"pipeline.pid"
            text_dir=folder/"txt"
            if not pid_file.exists() and not text_dir.exists():
                continue
            done=len(list(text_dir.glob("*.txt"))) if text_dir.exists() else 0
            total=_tsv_total(folder.name)
            todo=read_json(folder/"todo.json",{}) or {}
            running=process_file_is_running(pid_file,CODE_WORK_DIR/"pipeline.py") if pid_file.exists() else False
            if pid_file.exists() and not running:
                pid_file.unlink(missing_ok=True)
            log_file=folder/"pipeline.log"
            log_tail=log_file.read_text(encoding="utf-8",errors="replace").splitlines()[-6:] if log_file.exists() else []
            pipes.append({"name":folder.name,"done":done,"total":total,
                          "skip":len(todo.get("skip") or []),"running":running,"log_tail":log_tail})
    return {"pipes":pipes}



@app.get("/api/settings")
def api_settings():
    settings=read_public_config()
    whisper={"num_workers":4,"beam_size":1,"concurrency":1,
             "sleep_min":1,"sleep_max":3,"device":"auto",
             **(settings.get("whisper") or {})}
    return {"whisper":whisper,"browser":settings.get("browser") or {"mode":"background"}}




@app.put("/api/settings")
async def api_save_settings(request: Request):
    body=await request.json()
    current=read_public_config()
    whisper=current.get("whisper") or {}
    incoming=body.get("whisper") if isinstance(body.get("whisper"),dict) else body
    for key in ("num_workers","beam_size","concurrency","sleep_min","sleep_max"):
        value=incoming.get(key)
        if isinstance(value,(int,float)) and not isinstance(value,bool):
            whisper[key]=max(1,min(32,int(value)))
    whisper["concurrency"]=min(2,int(whisper.get("concurrency",1)))
    whisper["sleep_max"]=max(int(whisper.get("sleep_min",1)),int(whisper.get("sleep_max",3)))
    if incoming.get("device") in ("auto","cpu","cuda"):
        whisper["device"]=incoming["device"]
    settings={"whisper":whisper}
    mode=(body.get("browser") or {}).get("mode")
    if mode in ("background","visible"):
        settings["browser"]={"mode":mode}
    if "proxy" in body:
        settings["yt_proxy"]=str(body.get("proxy") or "").strip()
    CONFIG_JSON.parent.mkdir(parents=True,exist_ok=True)
    with resource_lock("public-config"):
        write_json(CONFIG_JSON,settings)
    return {"ok":True}




# ---------- cookies: 状态 + 自助上传（P1-6 + 失效告警） ----------
def _cookie_status(platform="douyin"):
    if platform!="douyin":
        raise ValueError("该公开版本仅支持抖音登录")
    base=DATA_DIR/"work"
    json_path=base/"douyin_cookies.json"
    txt_path=base/"douyin_cookies.txt"
    flag=read_json(base/"cookie_status.json",{}) or {}
    expired=bool((flag.get("douyin") or {}).get("expired",False))
    result={"platform":"douyin","exists":json_path.is_file() or txt_path.is_file(),
            "expired":expired,"msg":"登录状态已失效，请重新登录抖音" if expired else ""}
    for path in (json_path,txt_path):
        if not path.is_file():
            continue
        try:
            result.update({"mtime":path.stat().st_mtime,"size":path.stat().st_size})
            if path.suffix==".json":
                cookies=json.loads(path.read_text(encoding="utf-8-sig"))
                result["count"]=len(cookies) if isinstance(cookies,list) else 0
        except (OSError,ValueError,TypeError):
            result["expired"]=True
            result["msg"]="登录状态文件无法读取，请重新登录抖音"
        break
    return result



@app.get("/api/cookies/status")
def api_cookies_status():
    return {"douyin":_cookie_status("douyin")}


def is_douyin_cookie_domain(domain):
    value=str(domain or "").strip().lower().lstrip(".")
    return value == "douyin.com" or value.endswith(".douyin.com")



@app.post("/api/cookies/upload")
async def api_cookies_upload(platform: str=Query("douyin"),file: UploadFile=File(...)):
    if platform.lower()!="douyin":
        raise HTTPException(400,"此版本只支持抖音登录")
    content=await file.read(1024*1024+1)
    await file.close()
    if len(content)>1024*1024:
        raise HTTPException(400,"Cookie 文件不能超过1MB")
    from _cookie_utils import netscape_to_playwright,playwright_to_netscape,load_playwright_cookies
    from storage import atomic_text
    base=DATA_DIR/"work"
    base.mkdir(parents=True,exist_ok=True)
    temp=base/("upload-"+__import__("uuid").uuid4().hex)
    try:
        raw=content.decode("utf-8-sig")
        try:
            payload=json.loads(raw)
        except ValueError:
            payload=None
        if isinstance(payload,list):
            payload=[cookie for cookie in payload
                     if isinstance(cookie,dict) and is_douyin_cookie_domain(cookie.get("domain"))]
            if not payload:
                raise ValueError("文件中没有抖音域名的Cookie；原登录状态未改变")
            write_json(temp.with_suffix(".json"),payload)
        elif "Netscape HTTP Cookie File" in raw:
            atomic_text(temp.with_suffix(".txt"),raw)
            netscape_to_playwright(temp.with_suffix(".txt"),temp.with_suffix(".json"))
        else:
            raise ValueError("请上传Netscape TXT或Cookie JSON数组")
        cookies=load_playwright_cookies(temp.with_suffix(".json"))
        cookies=[cookie for cookie in cookies if is_douyin_cookie_domain(cookie.get("domain"))]
        if not cookies:
            raise ValueError("文件中没有可用的抖音Cookie；原登录状态未改变")
        write_json(temp.with_suffix(".json"),cookies)
        with resource_lock("cookie-douyin"):
            write_json(base/"douyin_cookies.json",cookies)
            playwright_to_netscape(cookies,base/"douyin_cookies.txt")
            flags=read_json(base/"cookie_status.json",{}) or {}
            flags.pop("douyin",None)
            write_json(base/"cookie_status.json",flags)
        return {"ok":True,"count":len(cookies)}
    except (ValueError,UnicodeError) as error:
        raise HTTPException(400,str(error)) from None
    finally:
        temp.with_suffix(".json").unlink(missing_ok=True)
        temp.with_suffix(".txt").unlink(missing_ok=True)




# ---------- platforms 开关（P1-6） ----------



def _queue_blogger(blogger_id):
    con=get_db()
    try:row=con.execute('SELECT id,slug,name FROM bloggers WHERE id=?',(blogger_id,)).fetchone()
    finally:con.close()
    if not row:raise HTTPException(404,'博主不存在')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',row['slug']):raise HTTPException(409,'博主标识不安全，请先修正博主资料')
    return dict(row)

@app.get("/api/queue")
def api_queue(blogger_id: int = None):
    """转写队列：按选中的博主读作品清单与字幕进度。"""
    con=get_db()
    try:bloggers=[dict(row) for row in con.execute('SELECT id,slug,name FROM bloggers ORDER BY created_at DESC')]
    finally:con.close()
    if blogger_id is None:return {'blogger_id':None,'blogger_name':'','bloggers':bloggers,'items':[],'todo':{'order':[],'skip':[]}}
    blogger=_queue_blogger(blogger_id);slug=blogger['slug']
    tsv=DATA_DIR/'work'/f'{slug}_videos.tsv';txt_dir=DATA_DIR/'outputs'/slug/'txt';out=[]
    if tsv.exists():
        try:
            with tsv.open(encoding='utf-8',newline='') as handle:
                for r in csv.DictReader(handle,delimiter='\t'):
                    seq=int(r.get('序号') or 0);url=(r.get('链接') or '').strip()
                    m=re.search(r'(?:/video/|/note/)(\d+)',url);vid=m.group(1) if m else 'unknown'
                    out.append({'seq':seq,'vid':vid,'title':r.get('标题') or '', 'url':url,
                                'done':(txt_dir/f'{seq:02d}_video_{vid}_subtitle.txt').is_file()})
        except (OSError,ValueError,csv.Error):
            raise HTTPException(500,'读取该博主作品清单失败，请检查 TSV 文件格式')
    todo={'order':[],'skip':[]};tp=DATA_DIR/'outputs'/slug/'todo.json'
    if tp.exists():todo=read_json(tp,todo) or todo
    return {'blogger_id':blogger_id,'blogger_name':blogger['name'],'bloggers':bloggers,'items':out,'todo':todo}

@app.put("/api/queue")
async def api_save_queue(request: Request):
    body=await request.json()
    try:blogger_id=int(body.get('blogger_id'))
    except (TypeError,ValueError):raise HTTPException(400,'请选择要调控的博主')
    blogger=_queue_blogger(blogger_id)
    try:d={k:list(dict.fromkeys(int(x) for x in body.get(k,[]))) for k in ('order','skip')}
    except (TypeError,ValueError):raise HTTPException(400,'队列序号格式无效')
    if any(x<1 for values in d.values() for x in values):raise HTTPException(400,'队列序号必须大于0')
    with resource_lock('queue-'+str(blogger_id)):
        write_json(DATA_DIR/'outputs'/blogger['slug']/'todo.json',d)
    return {'ok':True,'blogger_id':blogger_id,'saved':d}







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
                w.writerow([safe_csv_value(bd["name"]), safe_csv_value(bd.get("douyin_id","")), v.get("seq",""), safe_csv_value(v.get("kind","")),
                            safe_csv_value(v.get("video_id","")), safe_csv_value(v.get("url","")), safe_csv_value(v.get("title","")), safe_csv_value(v.get("upload_date","")),
                            v.get("duration",0), v.get("like_count",0), v.get("comment_count",0),
                            v.get("repost_count",0), v.get("save_count",0), safe_csv_value(v.get("status","")),
                            1 if v.get("has_analysis") else 0,
                            safe_csv_value((v.get("analysis") or {}).get("credibility","")[:200] if v.get("analysis") else ""),
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

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
DOWNLOAD_DIR = DATA_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
_tasks = {}
_tid = 0

def find_cookie():
    for p in (DATA_DIR/'work/douyin_cookies.txt',DATA_DIR/'douyin_cookies.txt',BASE/'douyin_cookies.txt',BASE/'work/douyin_cookies.txt'):
        if not p.exists() or p.stat().st_size==0:continue
        try:
            from cookie_scope import load_cookie_jar
            from _cookie_utils import playwright_to_netscape
            jar=load_cookie_jar(p)
            cookies=[{'name':cookie.name,'value':cookie.value,'domain':cookie.domain,'path':cookie.path,
                      'secure':cookie.secure,'httpOnly':cookie.has_nonstandard_attr('HttpOnly'),
                      'expires':cookie.expires if cookie.expires is not None else -1}
                     for cookie in jar if not cookie.is_expired() and is_douyin_cookie_domain(cookie.domain)]
            if not cookies:continue
            scoped_dir=DATA_DIR/'work'/'.download-cookie-tmp'
            scoped_dir.mkdir(parents=True,exist_ok=True)
            scoped_path=scoped_dir/(__import__('uuid').uuid4().hex+'.txt')
            playwright_to_netscape(cookies,scoped_path)
            return str(scoped_path)
        except (OSError,ValueError):
            continue
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
    safe_url=normalize_public_video_url(r["url"])
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
            import yt_dlp
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(safe_url, download=True)
            f = ydl.prepare_filename(info)
            _tasks[tid]["path"] = f
            _tasks[tid]["status"] = "done"
        except Exception as e:
            _tasks[tid]["status"] = "failed"
            _tasks[tid]["log"].append(str(e)[:300])
        finally:
            try:
                Path(ck).unlink(missing_ok=True)
            except OSError:
                pass

    fn = run_images if r["kind"] == "图文" else run_video
    threading.Thread(target=fn, daemon=True).start()
    return {"task_id": tid}

@app.get("/api/tasks/{tid}")
def task_status(tid: int):
    t = _tasks.get(tid)
    if not t:
        raise HTTPException(404, "任务不存在")
    return t

# ---------- profile collection ----------
@app.post('/api/collection')
def api_collection_start(payload: dict=Body(...)):
    import collection_service as service
    service.BASE_DIR=DATA_DIR
    service.APP_STATE_DIR=APP_STATE_DIR
    try:return service.start(payload.get('url'),payload.get('limit',50),payload.get('date_from'),payload.get('date_to'))
    except service.RangeConflictError as error:raise HTTPException(409,str(error))
    except service.CollectionError as error:raise HTTPException(400,str(error))
    except OSError:raise HTTPException(500,'无法保存采集任务，请检查数据目录权限')

@app.get('/api/collection/jobs/{tid}')
def api_collection_status(tid: str):
    import collection_service as service
    service.BASE_DIR=DATA_DIR
    service.APP_STATE_DIR=APP_STATE_DIR
    try:
        from task_store import get
        task=get(tid)
        if task and task['kind']=='video_collect':
            import video_collection_service
            return video_collection_service.status(tid)
        return service.status(tid)
    except (FileNotFoundError,service.CollectionError):raise HTTPException(404,'采集任务不存在')

@app.post('/api/collection/login')
def api_collection_login():
    import collection_service as service
    service.BASE_DIR=DATA_DIR
    service.APP_STATE_DIR=APP_STATE_DIR
    try:return service.start_login()
    except service.CollectionError as error:raise HTTPException(400,str(error))
    except OSError:raise HTTPException(500,'登录任务无法保存，请检查数据目录权限')

# ---------- static ----------
@app.post('/api/collection/videos')
def api_video_collection(payload: dict=Body(...)):
    import collection_service as shared
    import video_collection_service as service
    shared.BASE_DIR=DATA_DIR;shared.APP_STATE_DIR=APP_STATE_DIR
    try:return service.start(payload.get('urls'))
    except service.CollectionError as error:raise HTTPException(400,str(error))
    except OSError:raise HTTPException(500,'视频采集任务无法保存，请检查数据目录权限')

@app.post('/api/collection/videos/{tid}/retry')
def api_video_collection_retry(tid: str):
    import collection_service as shared
    import video_collection_service as service
    shared.BASE_DIR=DATA_DIR;shared.APP_STATE_DIR=APP_STATE_DIR
    try:return service.retry(tid)
    except FileNotFoundError:raise HTTPException(404,'视频采集任务不存在')
    except service.CollectionError as error:raise HTTPException(400,str(error))








@app.get('/api/search')
def api_search(q: str='',limit: int=30):
    q=q.strip()[:300]
    if not q:return {'items':[],'total':0}
    con=get_db();like='%'+q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
    where="(v.title LIKE ? ESCAPE '\\' OR v.subtitle LIKE ? ESCAPE '\\' OR v.notes LIKE ? ESCAPE '\\' OR a.full_md LIKE ? ESCAPE '\\')"
    rows=con.execute('SELECT v.id,v.title,v.subtitle,v.notes,v.upload_date,b.name blogger,a.full_md FROM videos v JOIN bloggers b ON b.id=v.blogger_id LEFT JOIN analyses a ON a.video_id=v.id WHERE '+where+' ORDER BY v.upload_date DESC LIMIT ?',[like]*4+[max(1,min(limit,100))]).fetchall()
    total=con.execute('SELECT COUNT(*) FROM videos v LEFT JOIN analyses a ON a.video_id=v.id WHERE '+where,[like]*4).fetchone()[0];con.close()
    out=[]
    for r in rows:
        matches=[]
        for key,label in [('title','标题'),('subtitle','字幕'),('notes','备注'),('full_md','分析')]:
            text=r[key] or '';idx=text.lower().find(q.lower())
            if idx>=0:matches.append({'source':label,'snippet':text[max(0,idx-65):idx+len(q)+160]})
        out.append({'id':r['id'],'title':r['title'],'blogger':r['blogger'],'date':r['upload_date'],'matches':matches})
    return {'items':out,'total':total}

@app.get('/api/metrics/imports')
def api_metrics_imports():
    con=get_db()
    try:return metrics_import.list_batches(con)
    finally:con.close()

@app.post('/api/metrics/import/preview')
async def api_metrics_import_preview(file: UploadFile=File(...),source_name: str=Form('')):
    try:
        content=await file.read(metrics_import.MAX_FILE_BYTES+1)
        con=get_db()
        try:return metrics_import.create_preview(con,content,file.filename or '',source_name)
        finally:con.close()
    except metrics_import.DuplicateImportError as e:
        raise HTTPException(409,detail={'message':str(e),'batch_id':e.batch_id})
    except metrics_import.ImportErrorDetail as e:
        raise HTTPException(400,str(e))
    finally:
        await file.close()

@app.post('/api/metrics/import/confirm')
def api_metrics_import_confirm(payload: dict=Body(...)):
    preview_id=str(payload.get('preview_id') or '').strip()
    if len(preview_id)>100:raise HTTPException(400,'导入预览编号无效')
    con=get_db()
    try:return metrics_import.confirm_preview(con,preview_id)
    except metrics_import.DuplicateImportError as e:
        raise HTTPException(409,detail={'message':str(e),'batch_id':e.batch_id})
    except metrics_import.ImportErrorDetail as e:
        raise HTTPException(400,str(e))
    finally:con.close()

@app.delete('/api/metrics/import/preview/{preview_id}')
def api_metrics_import_cancel(preview_id: str):
    con=get_db()
    try:return {'ok':metrics_import.cancel_preview(con,preview_id)}
    finally:con.close()

@app.get('/api/metrics/imports/{batch_id}/accounts')
def api_metrics_import_accounts(batch_id: int):
    con=get_db()
    try:return metrics_import.list_accounts(con,batch_id)
    except metrics_import.ImportErrorDetail as e:raise HTTPException(404,str(e))
    finally:con.close()

@app.delete('/api/metrics/imports/{batch_id}')
def api_metrics_import_delete(batch_id: int):
    con=get_db()
    try:
        if not con.execute('SELECT 1 FROM metric_import_batches WHERE id=?',(batch_id,)).fetchone():
            raise HTTPException(404,'导入批次不存在')
        backup_database(DB_PATH,'before-metric-import-delete')
        metrics_import.delete_batch(con,batch_id)
        return {'ok':True}
    except metrics_import.ImportErrorDetail as e:raise HTTPException(404,str(e))
    finally:con.close()

@app.get('/api/metrics')
def api_metrics(q: str='',blogger_ids: str='',video_ids: str='',date_from: str='',date_to: str='',
                min_duration: int=None,max_duration: int=None,min_likes: int=None,min_comments: int=None,
                min_shares: int=None,min_saves: int=None,kind: str='',sort: str='date_desc',
                limit: int=100,offset: int=0,batch_id: int=0,creator_keys: str=''):
    """Query local records, or one explicitly selected external CSV import batch."""
    filters={'q':q,'blogger_ids':blogger_ids,'video_ids':video_ids,'date_from':date_from,'date_to':date_to,
             'min_duration':min_duration,'max_duration':max_duration,'min_likes':min_likes,
             'min_comments':min_comments,'min_shares':min_shares,'min_saves':min_saves,'kind':kind,
             'creator_keys':creator_keys}
    con=get_db()
    try:
        return query_metrics(con,{**filters,'sort':sort,'limit':limit,'offset':offset},batch_id=batch_id)
    except ValueError as e:
        raise HTTPException(400,str(e))
    finally:
        con.close()

@app.get('/api/metrics/export.csv')
def api_metrics_export(q: str='',blogger_ids: str='',video_ids: str='',date_from: str='',date_to: str='',
                       min_duration: int=None,max_duration: int=None,min_likes: int=None,min_comments: int=None,
                       min_shares: int=None,min_saves: int=None,kind: str='',batch_id: int=0,creator_keys: str=''):
    """Download a bounded CSV of the same filtered records shown in the comparison page."""
    filters={'q':q,'blogger_ids':blogger_ids,'video_ids':video_ids,'date_from':date_from,'date_to':date_to,
             'min_duration':min_duration,'max_duration':max_duration,'min_likes':min_likes,
             'min_comments':min_comments,'min_shares':min_shares,'min_saves':min_saves,'kind':kind,
             'creator_keys':creator_keys}
    con=get_db()
    try:
        body,count=export_metrics_csv(con,filters,batch_id=batch_id)
        return Response(content=body.encode('utf-8-sig'),media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition':'attachment; filename="douyin_metrics.csv"',
                                 'X-Export-Count':str(count)})
    except ValueError as e:
        raise HTTPException(400,str(e))
    finally:
        con.close()

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    print("抖音博主数据库已启动: http://127.0.0.1:8321")
    uvicorn.run(app, host="127.0.0.1", port=8321)
