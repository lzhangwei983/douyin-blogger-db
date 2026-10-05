"""Local, bounded CSV/TSV ingestion for the creator metrics comparison page."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath


MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_ROWS = 50_000
PREVIEW_TTL_HOURS = 24

FIELD_LABELS = {
    "creator_name": "博主名称", "creator_id": "博主ID", "creator_url": "博主主页",
    "follower_count": "粉丝数", "video_id": "作品ID", "url": "作品链接",
    "title": "作品标题", "tags": "作品标签", "kind": "作品类型",
    "upload_date": "发布时间", "duration": "时长", "play_count": "播放量", "recommend_count": "推荐数",
    "like_count": "点赞", "comment_count": "评论", "repost_count": "分享/转发",
    "save_count": "收藏", "captured_at": "来源采集时间",
}

ALIASES = {
    "creator_name": ("作者昵称", "博主昵称", "达人昵称", "账号昵称", "creator_name", "author_name", "nickname", "author", "用户名"),
    "creator_id": ("sec_uid", "作者sec_uid", "作者uid", "博主uid", "uid", "creator_id", "author_uid", "作者id", "达人id"),
    "creator_url": ("作者链接", "作者主页链接", "主页链接", "作者主页", "creator_url", "author_url", "profile_url"),
    "follower_count": ("作者粉丝数", "博主粉丝数", "粉丝数", "粉丝量", "粉丝", "follower_count", "followers", "author_followers"),
    "video_id": ("aweme_id", "awemeid", "作品id", "视频id", "作品编号", "video_id", "item_id", "awemeid"),
    "url": ("视频链接", "作品链接", "作品地址", "链接", "url", "video_url", "aweme_url", "share_url"),
    "title": ("视频标题", "作品标题", "标题", "title", "desc", "description"),
    "tags": ("视频标签", "作品标签", "话题标签", "标签", "tags", "hashtags"),
    "kind": ("作品类型", "视频类型", "类型", "kind", "type"),
    "upload_date": ("发布时间", "发布日期", "发布于", "发布时间文本", "upload_date", "publish_time", "create_time", "created_at"),
    "duration": ("视频时长秒", "时长秒", "视频时长", "作品时长", "时长", "duration", "duration_seconds", "duration_sec"),
    "play_count": ("播放量", "播放数", "播放次数", "作品播放量", "浏览量", "play_count", "view_count", "views", "play"),
    "recommend_count": ("推荐数", "推荐量", "推荐次数", "recommend_count"),
    "like_count": ("点赞数", "点赞", "喜欢数", "like_count", "digg_count", "likes"),
    "comment_count": ("评论数", "评论", "comment_count", "comments"),
    "repost_count": ("转发数", "分享数", "分享", "转发", "repost_count", "share_count", "shares"),
    "save_count": ("收藏数", "收藏", "收藏量", "收藏次数", "save_count", "collect_count", "collects"),
    "captured_at": ("数据采集时间", "作品采集时间", "抓取时间", "采集时间", "snapshot_time", "captured_at", "scraped_at"),
}

_HEADER_TO_FIELD = {}
for _field, _aliases in ALIASES.items():
    for _alias in _aliases:
        _HEADER_TO_FIELD[re.sub(r"[\s_\-（）():：./]+", "", _alias.strip().lower())] = _field

_UNKNOWN = {"", "-", "—", "–", "n/a", "na", "null", "none", "未知", "未采集", "未提供"}


class ImportErrorDetail(ValueError):
    pass


class DuplicateImportError(ImportErrorDetail):
    def __init__(self, batch_id: int):
        super().__init__(f"这份文件已经导入过（批次 {batch_id}），无需重复导入")
        self.batch_id = batch_id


def init_schema(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS metric_import_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_name TEXT NOT NULL,
        filename TEXT NOT NULL,
        content_sha256 TEXT NOT NULL UNIQUE,
        imported_at TEXT NOT NULL,
        row_count INTEGER NOT NULL,
        skipped_count INTEGER NOT NULL DEFAULT 0,
        warnings_json TEXT NOT NULL DEFAULT '{}',
        fields_json TEXT NOT NULL DEFAULT '[]'
    );
    CREATE TABLE IF NOT EXISTS metric_import_rows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id INTEGER NOT NULL REFERENCES metric_import_batches(id) ON DELETE CASCADE,
        source_row INTEGER NOT NULL,
        creator_key TEXT NOT NULL,
        creator_name TEXT NOT NULL,
        creator_id TEXT,
        creator_url TEXT,
        follower_count INTEGER,
        video_id TEXT,
        work_key TEXT NOT NULL,
        url TEXT,
        title TEXT,
        tags TEXT,
        kind TEXT,
        upload_date TEXT,
        duration INTEGER,
        play_count INTEGER,
        recommend_count INTEGER,
        like_count INTEGER,
        comment_count INTEGER,
        repost_count INTEGER,
        save_count INTEGER,
        captured_at TEXT,
        UNIQUE(batch_id, work_key)
    );
    CREATE TABLE IF NOT EXISTS metric_import_previews (
        preview_id TEXT PRIMARY KEY,
        source_name TEXT NOT NULL,
        filename TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL,
        row_count INTEGER NOT NULL,
        skipped_count INTEGER NOT NULL DEFAULT 0,
        warnings_json TEXT NOT NULL DEFAULT '{}',
        fields_json TEXT NOT NULL DEFAULT '[]'
    );
    CREATE TABLE IF NOT EXISTS metric_import_preview_rows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        preview_id TEXT NOT NULL REFERENCES metric_import_previews(preview_id) ON DELETE CASCADE,
        source_row INTEGER NOT NULL,
        creator_key TEXT NOT NULL,
        creator_name TEXT NOT NULL,
        creator_id TEXT,
        creator_url TEXT,
        follower_count INTEGER,
        video_id TEXT,
        work_key TEXT NOT NULL,
        url TEXT,
        title TEXT,
        tags TEXT,
        kind TEXT,
        upload_date TEXT,
        duration INTEGER,
        play_count INTEGER,
        recommend_count INTEGER,
        like_count INTEGER,
        comment_count INTEGER,
        repost_count INTEGER,
        save_count INTEGER,
        captured_at TEXT,
        UNIQUE(preview_id, work_key)
    );
    CREATE INDEX IF NOT EXISTS idx_metric_import_rows_batch_creator
        ON metric_import_rows(batch_id, creator_key);
    CREATE INDEX IF NOT EXISTS idx_metric_import_rows_batch_date
        ON metric_import_rows(batch_id, upload_date);
    CREATE INDEX IF NOT EXISTS idx_metric_import_preview_created
        ON metric_import_previews(created_at);
    """)
    for table in ("metric_import_rows", "metric_import_preview_rows"):
        columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
        if "recommend_count" not in columns:
            con.execute(f"ALTER TABLE {table} ADD COLUMN recommend_count INTEGER")
    con.commit()


def _safe_filename(filename: str) -> str:
    filename = (filename or "import.csv").replace("\\", "/")
    name = PurePosixPath(filename).name
    name = re.sub(r"[\x00-\x1f]", "", name).strip(" .")[:180]
    return name or "import.csv"


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportErrorDetail("无法识别文件编码，请另存为 UTF-8 CSV 后重试")


def _norm_header(value: str) -> str:
    return re.sub(r"[\s_\-（）():：./]+", "", (value or "").strip().lstrip("\ufeff").lower())


def _parse_count(value):
    raw = str(value or "").strip().replace(",", "").replace("，", "")
    if raw.lower() in _UNKNOWN:
        return None
    raw = raw.replace("人", "").replace("次", "").replace("个", "")
    match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*(亿|万|[kKmM])?", raw)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2)
    scale = {"亿": 100_000_000, "万": 10_000, "k": 1_000, "m": 1_000_000}.get(unit.lower() if unit else "", 1)
    result = int(number * scale)
    if result < 0 or result > 2_147_483_647:
        raise ImportErrorDetail("文件中有超出支持范围的互动数")
    return result


def _parse_duration(value):
    raw = str(value or "").strip().lower()
    if raw in _UNKNOWN:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", raw):
        number = float(raw)
        if number > 86400 * 1000:
            raise ImportErrorDetail("文件中有超出24小时的作品时长")
        return int(number)
    if re.fullmatch(r"\d{1,3}:\d{1,2}(?::\d{1,2})?", raw):
        parts = [int(p) for p in raw.split(":")]
        seconds = parts[-1] + parts[-2] * 60 + (parts[-3] * 3600 if len(parts) == 3 else 0)
        return seconds if seconds <= 86400 else None
    mins = re.search(r"(\d+(?:\.\d+)?)\s*(?:分|min|m)", raw)
    secs = re.search(r"(\d+(?:\.\d+)?)\s*(?:秒|sec|s)", raw)
    if mins or secs:
        result = int((float(mins.group(1)) * 60 if mins else 0) + (float(secs.group(1)) if secs else 0))
        return result if result <= 86400 else None
    return None


def _parse_date(value):
    raw = str(value or "").strip()
    if raw.lower() in _UNKNOWN:
        return None
    if re.fullmatch(r"\d{13,16}", raw):
        try:
            raw = datetime.fromtimestamp(int(raw[:13]) / 1000, tz=timezone.utc).astimezone().strftime("%Y%m%d")
            return raw
        except (OverflowError, OSError, ValueError):
            return None
    if re.fullmatch(r"\d{10}", raw):
        try:
            return datetime.fromtimestamp(int(raw), tz=timezone.utc).astimezone().strftime("%Y%m%d")
        except (OverflowError, OSError, ValueError):
            return None
    raw = raw[:19].replace("T", " ")
    for pattern in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, pattern).strftime("%Y%m%d")
        except ValueError:
            pass
    return None


def _hash_key(prefix: str, value: str) -> str:
    return hashlib.sha256((prefix + ":" + value).encode("utf-8")).hexdigest()[:32]


def _parse_csv(content: bytes, filename: str):
    filename = _safe_filename(filename)
    if not filename.lower().endswith((".csv", ".tsv")):
        raise ImportErrorDetail("请选择 CSV 或 TSV 数据文件")
    if not content:
        raise ImportErrorDetail("文件为空")
    if len(content) > MAX_FILE_BYTES:
        raise ImportErrorDetail("文件超过12MB，请先缩小数据范围后重试")
    text = _decode(content)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        headers = [str(x).strip().lstrip("\ufeff") for x in next(reader)]
    except StopIteration:
        raise ImportErrorDetail("文件没有表头")
    if not headers or len(headers) > 300:
        raise ImportErrorDetail("文件表头为空或列数超过300列")
    mapped = {}
    for index, header in enumerate(headers):
        field = _HEADER_TO_FIELD.get(_norm_header(header))
        if field and field not in mapped:
            mapped[field] = index
    if not any(k in mapped for k in ("creator_name", "creator_id", "creator_url")):
        raise ImportErrorDetail("没识别到博主字段；至少需要作者昵称、uid 或作者主页链接")
    if not any(k in mapped for k in ("video_id", "url", "title")):
        raise ImportErrorDetail("没识别到作品字段；至少需要作品ID、作品链接或标题")

    recognized = [{"field": key, "label": FIELD_LABELS[key], "header": headers[index]}
                  for key, index in mapped.items()]
    rows, skipped, reasons, dedup = [], 0, {}, set()
    creator_id_missing = 0
    for source_row, cells in enumerate(reader, start=2):
        if source_row > MAX_ROWS + 1:
            raise ImportErrorDetail(f"文件超过{MAX_ROWS}行上限，请拆成多个文件")
        if not any(str(x).strip() for x in cells):
            continue
        cells = (cells + [""] * len(headers))[:len(headers)]
        values = {key: cells[index].strip() if index < len(cells) else "" for key, index in mapped.items()}
        creator_id = values.get("creator_id", "")[:200]
        creator_name = values.get("creator_name", "")[:200]
        creator_url = values.get("creator_url", "")[:1000]
        identity = creator_id or creator_url.rstrip("/").lower() or creator_name.casefold()
        video_id = values.get("video_id", "")[:100]
        url = values.get("url", "")[:2000]
        title = values.get("title", "")[:1000]
        upload_date = _parse_date(values.get("upload_date"))
        if not identity or not (video_id or url or title):
            skipped += 1
            reason = "缺少博主身份或作品信息"
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        if not creator_id:
            creator_id_missing += 1
        if not creator_name:
            creator_name = creator_id or creator_url or "未命名博主"
        if not video_id:
            match = re.search(r"/(?:video|note)/(\d+)", url)
            video_id = match.group(1) if match else ""
        creator_prefix = "uid" if creator_id else "url" if creator_url else "name"
        creator_value = creator_id.casefold() if creator_id else creator_url.rstrip("/").lower() if creator_url else creator_name.casefold()
        creator_key = _hash_key(creator_prefix, creator_value)
        work_value = video_id or url.rstrip("/").lower() or (title.casefold() + "|" + (upload_date or ""))
        work_key = _hash_key("work", creator_key + ":" + work_value)
        if work_key in dedup:
            skipped += 1
            reason = "同一文件内重复作品"
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        dedup.add(work_key)
        kind_text = values.get("kind", "").strip().lower()
        kind = "图文" if any(x in kind_text for x in ("图文", "笔记", "image", "note")) or "/note/" in url.lower() else "视频"
        normalized = {
            "source_row": source_row, "creator_key": creator_key, "creator_name": creator_name,
            "creator_id": creator_id or None, "creator_url": creator_url or None,
            "follower_count": _parse_count(values.get("follower_count")),
            "video_id": video_id or None, "work_key": work_key, "url": url or None,
            "title": title or None, "tags": values.get("tags", "")[:2000] or None,
            "kind": kind, "upload_date": upload_date,
            "duration": _parse_duration(values.get("duration")),
            "play_count": _parse_count(values.get("play_count")),
            "recommend_count": _parse_count(values.get("recommend_count")),
            "like_count": _parse_count(values.get("like_count")),
            "comment_count": _parse_count(values.get("comment_count")),
            "repost_count": _parse_count(values.get("repost_count")),
            "save_count": _parse_count(values.get("save_count")),
            "captured_at": _parse_date(values.get("captured_at")),
        }
        rows.append(normalized)
    if not rows:
        raise ImportErrorDetail("没有可导入的有效作品行；请检查作者和作品列")
    warnings = dict(reasons)
    if creator_id_missing:
        warnings["部分记录没有唯一UID，已按主页链接或昵称识别博主"] = creator_id_missing
    for field in ("play_count", "follower_count", "like_count", "comment_count", "repost_count", "save_count", "duration", "upload_date"):
        if field not in mapped:
            warnings[FIELD_LABELS[field] + "列未提供"] = len(rows)
    return {
        "filename": filename, "content_sha256": hashlib.sha256(content).hexdigest(),
        "rows": rows, "skipped_count": skipped, "warnings": warnings,
        "fields": recognized, "delimiter": delimiter,
    }


def _cleanup_previews(con):
    cutoff = (datetime.now() - timedelta(hours=PREVIEW_TTL_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    con.execute("DELETE FROM metric_import_previews WHERE created_at < ?", (cutoff,))


def create_preview(con, content: bytes, filename: str, source_name: str = ""):
    parsed = _parse_csv(content, filename)
    duplicate = con.execute("SELECT id FROM metric_import_batches WHERE content_sha256=?", (parsed["content_sha256"],)).fetchone()
    if duplicate:
        raise DuplicateImportError(duplicate["id"])
    _cleanup_previews(con)
    preview_id = secrets.token_urlsafe(24)
    source_name = re.sub(r"[\x00-\x1f]", "", (source_name or "外部 CSV").strip())[:80] or "外部 CSV"
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con.execute("""INSERT INTO metric_import_previews(preview_id,source_name,filename,content_sha256,created_at,
                row_count,skipped_count,warnings_json,fields_json) VALUES(?,?,?,?,?,?,?,?,?)""",
                (preview_id, source_name, parsed["filename"], parsed["content_sha256"], created_at,
                 len(parsed["rows"]), parsed["skipped_count"], json.dumps(parsed["warnings"], ensure_ascii=False),
                 json.dumps(parsed["fields"], ensure_ascii=False)))
    columns = ("preview_id", "source_row", "creator_key", "creator_name", "creator_id", "creator_url", "follower_count",
               "video_id", "work_key", "url", "title", "tags", "kind", "upload_date", "duration", "play_count",
               "recommend_count", "like_count", "comment_count", "repost_count", "save_count", "captured_at")
    sql = "INSERT INTO metric_import_preview_rows(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")"
    con.executemany(sql, [[preview_id] + [row.get(field) for field in columns[1:]] for row in parsed["rows"]])
    con.commit()
    return {
        "preview_id": preview_id, "source_name": source_name, "filename": parsed["filename"],
        "row_count": len(parsed["rows"]), "skipped_count": parsed["skipped_count"],
        "warnings": parsed["warnings"], "fields": parsed["fields"], "delimiter": "Tab" if parsed["delimiter"] == "\t" else parsed["delimiter"],
        "preview": [{key: row.get(key) for key in ("creator_name", "creator_id", "title", "url", "upload_date", "duration", "play_count", "recommend_count", "like_count", "comment_count", "repost_count", "save_count")} for row in parsed["rows"][:5]],
    }


def confirm_preview(con, preview_id: str):
    _cleanup_previews(con)
    preview = con.execute("SELECT * FROM metric_import_previews WHERE preview_id=?", (preview_id,)).fetchone()
    if not preview:
        raise ImportErrorDetail("导入预览已过期或不存在，请重新选择文件")
    duplicate = con.execute("SELECT id FROM metric_import_batches WHERE content_sha256=?", (preview["content_sha256"],)).fetchone()
    if duplicate:
        raise DuplicateImportError(duplicate["id"])
    imported_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = con.execute("""INSERT INTO metric_import_batches(source_name,filename,content_sha256,imported_at,row_count,
                 skipped_count,warnings_json,fields_json) VALUES(?,?,?,?,?,?,?,?)""",
                 (preview["source_name"], preview["filename"], preview["content_sha256"], imported_at,
                  preview["row_count"], preview["skipped_count"], preview["warnings_json"], preview["fields_json"]))
    batch_id = cur.lastrowid
    columns = ("source_row", "creator_key", "creator_name", "creator_id", "creator_url", "follower_count", "video_id", "work_key", "url", "title", "tags", "kind", "upload_date", "duration", "play_count", "recommend_count", "like_count", "comment_count", "repost_count", "save_count", "captured_at")
    select = "SELECT " + ",".join(columns) + " FROM metric_import_preview_rows WHERE preview_id=?"
    staged = con.execute(select, (preview_id,)).fetchall()
    placeholders = ",".join("?" for _ in ("batch_id",) + columns)
    sql = "INSERT INTO metric_import_rows(batch_id," + ",".join(columns) + ") VALUES(" + placeholders + ")"
    con.executemany(sql, [[batch_id] + list(row) for row in staged])
    if len(staged) != preview["row_count"]:
        raise ImportErrorDetail("预览数据不完整，本次导入已取消，请重新选择文件")
    con.execute("DELETE FROM metric_import_previews WHERE preview_id=?", (preview_id,))
    con.commit()
    return {"id": batch_id, "source_name": preview["source_name"], "filename": preview["filename"],
            "imported_at": imported_at, "row_count": preview["row_count"], "skipped_count": preview["skipped_count"],
            "warnings": json.loads(preview["warnings_json"]), "fields": json.loads(preview["fields_json"])}


def list_batches(con):
    rows = con.execute("""SELECT id,source_name,filename,imported_at,row_count,skipped_count,warnings_json,fields_json
                          FROM metric_import_batches ORDER BY imported_at DESC,id DESC""").fetchall()
    return [{"id": row["id"], "source_name": row["source_name"], "filename": row["filename"],
             "imported_at": row["imported_at"], "row_count": row["row_count"],
             "skipped_count": row["skipped_count"], "warnings": json.loads(row["warnings_json"] or "{}"),
             "fields": json.loads(row["fields_json"] or "[]")} for row in rows]


def cancel_preview(con, preview_id: str):
    cur = con.execute("DELETE FROM metric_import_previews WHERE preview_id=?", (preview_id,))
    con.commit()
    return cur.rowcount > 0


def list_accounts(con, batch_id: int):
    if not con.execute("SELECT 1 FROM metric_import_batches WHERE id=?", (batch_id,)).fetchone():
        raise ImportErrorDetail("导入批次不存在")
    rows = con.execute("""SELECT creator_key,MAX(creator_name) AS creator_name,MAX(creator_id) AS creator_id,
                              MAX(creator_url) AS creator_url,COUNT(*) AS video_count
                       FROM metric_import_rows WHERE batch_id=? GROUP BY creator_key ORDER BY creator_name COLLATE NOCASE""",
                       (batch_id,)).fetchall()
    return [{"creator_key": row["creator_key"], "id": row["creator_key"], "name": row["creator_name"],
             "douyin_id": row["creator_id"] or "", "homepage_url": row["creator_url"] or "",
             "video_count": row["video_count"], "platform": "抖音"} for row in rows]


def delete_batch(con, batch_id: int):
    cur = con.execute("DELETE FROM metric_import_batches WHERE id=?", (batch_id,))
    if cur.rowcount == 0:
        raise ImportErrorDetail("导入批次不存在")
    con.commit()
