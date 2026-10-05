"""Filtering and comparison of records already stored in the local database.

This module is deliberately independent of online collection, media downloads,
transcription, and generative models.
"""
from datetime import datetime
import json
import re


SORTS = {
    "date_desc": "v.upload_date DESC, v.id DESC",
    "date_asc": "v.upload_date ASC, v.id ASC",
    "likes_desc": "v.like_count DESC, v.upload_date DESC, v.id DESC",
    "comments_desc": "v.comment_count DESC, v.upload_date DESC, v.id DESC",
    "shares_desc": "v.repost_count DESC, v.upload_date DESC, v.id DESC",
    "saves_desc": "v.save_count DESC, v.upload_date DESC, v.id DESC",
    "duration_desc": "v.duration DESC, v.upload_date DESC, v.id DESC",
}


def _integer(value, name, low=0, high=2_147_483_647):
    if value in (None, ""):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name}必须是整数")
    if result < low or result > high:
        raise ValueError(f"{name}超出可用范围")
    return result


def _day(value, name):
    value = (value or "").strip()
    if not value:
        return None
    for pattern in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, pattern).strftime("%Y%m%d")
        except ValueError:
            pass
    raise ValueError(f"{name}格式应为 YYYY-MM-DD")


def build_filters(query):
    """Build parameterized filters. Returns where, args, IDs, normalized filters."""
    where, args = ["b.platform=?"], ["抖音"]

    raw_ids = query.get("blogger_ids") or ""
    parts = ([part.strip() for part in raw_ids.split(",") if part.strip()]
             if isinstance(raw_ids, str) else list(raw_ids) if isinstance(raw_ids, (list, tuple)) else None)
    if parts is None:
        raise ValueError("博主列表格式错误")
    blogger_ids = []
    for part in parts:
        bid = _integer(part, "博主ID", 1)
        if bid not in blogger_ids:
            blogger_ids.append(bid)
    if len(blogger_ids) > 10:
        raise ValueError("一次最多筛选10位博主")
    if blogger_ids:
        where.append("v.blogger_id IN (" + ",".join("?" for _ in blogger_ids) + ")")
        args.extend(blogger_ids)

    q = str(query.get("q") or "").strip()[:120]
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        where.append("(v.title LIKE ? ESCAPE '\\' OR v.tags LIKE ? ESCAPE '\\' OR v.subtitle LIKE ? ESCAPE '\\')")
        args.extend([like] * 3)

    date_expr = "REPLACE(SUBSTR(COALESCE(v.upload_date,''),1,10),'-','')"
    date_from = _day(query.get("date_from"), "开始日期")
    date_to = _day(query.get("date_to"), "结束日期")
    if date_from and date_to and date_from > date_to:
        raise ValueError("开始日期不能晚于结束日期")
    if date_from:
        where.append(f"{date_expr} >= ?")
        args.append(date_from)
    if date_to:
        where.append(f"{date_expr} <= ?")
        args.append(date_to)

    min_duration = _integer(query.get("min_duration"), "最短时长", 0, 86400)
    max_duration = _integer(query.get("max_duration"), "最长时长", 0, 86400)
    if min_duration is not None and max_duration is not None and min_duration > max_duration:
        raise ValueError("最短时长不能大于最长时长")
    if min_duration is not None:
        where.append("v.duration >= ?")
        args.append(min_duration)
    if max_duration is not None:
        where.append("v.duration <= ?")
        args.append(max_duration)

    for key, label, column in (
        ("min_likes", "最低点赞", "like_count"),
        ("min_comments", "最低评论", "comment_count"),
        ("min_shares", "最低分享", "repost_count"),
        ("min_saves", "最低收藏", "save_count"),
    ):
        value = _integer(query.get(key), label)
        if value is not None:
            where.append(f"v.{column} >= ?")
            args.append(value)

    kind = str(query.get("kind") or "").strip()
    if kind:
        if kind not in ("视频", "图文"):
            raise ValueError("作品类型只能选择视频或图文")
        where.append("v.kind=?")
        args.append(kind)

    raw_videos = query.get("video_ids") or ""
    parts = ([part.strip() for part in raw_videos.split(",") if part.strip()]
             if isinstance(raw_videos, str) else list(raw_videos) if isinstance(raw_videos, (list, tuple)) else None)
    if parts is None:
        raise ValueError("作品列表格式错误")
    video_ids = []
    for part in parts:
        vid = _integer(part, "作品记录ID", 1)
        if vid not in video_ids:
            video_ids.append(vid)
    if len(video_ids) > 5:
        raise ValueError("一次最多比较5条作品")
    if video_ids:
        where.append("v.id IN (" + ",".join("?" for _ in video_ids) + ")")
        args.extend(video_ids)

    normalized = {
        "q": q, "date_from": date_from, "date_to": date_to,
        "min_duration": min_duration, "max_duration": max_duration,
        "kind": kind, "video_ids": video_ids,
    }
    return " AND ".join(where), args, blogger_ids, normalized


def _import_filter(query, batch_id):
    if batch_id < 1:
        raise ValueError("导入批次编号无效")
    where, args = ["v.batch_id=?"], [batch_id]
    q = str(query.get("q") or "").strip()[:120]
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        where.append("(v.title LIKE ? ESCAPE '\\' OR v.tags LIKE ? ESCAPE '\\')")
        args.extend([like, like])

    raw_creators = query.get("creator_keys") or ""
    creator_keys = ([part.strip() for part in raw_creators.split(",") if part.strip()]
                    if isinstance(raw_creators, str) else None)
    if creator_keys is None or len(creator_keys) > 10 or any(not re.fullmatch(r"[a-f0-9]{32}", key) for key in creator_keys):
        raise ValueError("博主选择无效；一次最多比较10位博主")
    if creator_keys:
        where.append("v.creator_key IN (" + ",".join("?" for _ in creator_keys) + ")")
        args.extend(creator_keys)

    date_expr = "REPLACE(SUBSTR(COALESCE(v.upload_date,''),1,10),'-','')"
    date_from = _day(query.get("date_from"), "开始日期")
    date_to = _day(query.get("date_to"), "结束日期")
    if date_from and date_to and date_from > date_to:
        raise ValueError("开始日期不能晚于结束日期")
    if date_from:
        where.append(f"{date_expr} >= ?"); args.append(date_from)
    if date_to:
        where.append(f"{date_expr} <= ?"); args.append(date_to)

    min_duration = _integer(query.get("min_duration"), "最短时长", 0, 86400)
    max_duration = _integer(query.get("max_duration"), "最长时长", 0, 86400)
    if min_duration is not None and max_duration is not None and min_duration > max_duration:
        raise ValueError("最短时长不能大于最长时长")
    if min_duration is not None:
        where.append("v.duration >= ?"); args.append(min_duration)
    if max_duration is not None:
        where.append("v.duration <= ?"); args.append(max_duration)
    for key, label, column in (("min_likes", "最低点赞", "like_count"),
                               ("min_comments", "最低评论", "comment_count"),
                               ("min_shares", "最低分享", "repost_count"),
                               ("min_saves", "最低收藏", "save_count")):
        value = _integer(query.get(key), label)
        if value is not None:
            where.append(f"v.{column} >= ?"); args.append(value)
    kind = str(query.get("kind") or "").strip()
    if kind:
        if kind not in ("视频", "图文"):
            raise ValueError("作品类型只能选择视频或图文")
        where.append("v.kind=?"); args.append(kind)

    raw_videos = query.get("video_ids") or ""
    parts = ([part.strip() for part in raw_videos.split(",") if part.strip()]
             if isinstance(raw_videos, str) else list(raw_videos) if isinstance(raw_videos, (list, tuple)) else None)
    if parts is None:
        raise ValueError("作品列表格式错误")
    video_ids = []
    for part in parts:
        vid = _integer(part, "作品记录ID", 1)
        if vid not in video_ids:
            video_ids.append(vid)
    if len(video_ids) > 5:
        raise ValueError("一次最多比较5条作品")
    if video_ids:
        where.append("v.id IN (" + ",".join("?" for _ in video_ids) + ")")
        args.extend(video_ids)
    return " AND ".join(where), args, creator_keys


def query_imported_metrics(con, query, batch_id):
    where, args, creator_keys = _import_filter(query, batch_id)
    batch = con.execute("SELECT source_name,filename,imported_at,fields_json FROM metric_import_batches WHERE id=?", (batch_id,)).fetchone()
    if not batch:
        raise ValueError("导入批次不存在或已删除")
    try:
        limit = max(1, min(int(query.get("limit", 100)), 200))
        offset = max(0, min(int(query.get("offset", 0)), 50_000))
    except (TypeError, ValueError):
        raise ValueError("分页参数必须是整数")
    sort = str(query.get("sort") or "date_desc")
    if sort not in SORTS:
        sort = "date_desc"
    base = "FROM metric_import_rows v WHERE " + where
    total = con.execute("SELECT COUNT(*) " + base, args).fetchone()[0]
    rows = con.execute(
        """SELECT v.id,v.creator_key,v.creator_name AS blogger_name,'外部数据' AS blogger_tags,
                  v.video_id,v.url,v.kind,v.title,v.tags,v.upload_date,v.duration,v.play_count,v.recommend_count,
                  v.follower_count,v.like_count,v.comment_count,v.repost_count,v.save_count,
                  'ok' AS status,NULL AS local_record_updated_at,
                  b.source_name,b.filename,b.imported_at,v.captured_at
           FROM metric_import_rows v JOIN metric_import_batches b ON b.id=v.batch_id
           WHERE """ + where + f" ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?",
        args + [limit, offset],
    ).fetchall()

    summary = []
    if creator_keys:
        summary = [dict(row) for row in con.execute(
            """SELECT v.creator_key,v.creator_name AS blogger_name,'外部数据' AS blogger_tags,
                      COUNT(*) AS sample_count,
                      SUM(CASE WHEN v.like_count IS NOT NULL THEN 1 ELSE 0 END) AS likes_positive_rows,
                      SUM(CASE WHEN v.like_count=0 THEN 1 ELSE 0 END) AS likes_zero_rows,
                      SUM(CASE WHEN v.like_count IS NULL THEN 1 ELSE 0 END) AS likes_missing_rows,
                      AVG(v.like_count) AS likes_mean_positive,MAX(v.like_count) AS likes_max_positive,
                      SUM(CASE WHEN v.comment_count IS NOT NULL THEN 1 ELSE 0 END) AS comments_positive_rows,
                      SUM(CASE WHEN v.comment_count=0 THEN 1 ELSE 0 END) AS comments_zero_rows,
                      SUM(CASE WHEN v.comment_count IS NULL THEN 1 ELSE 0 END) AS comments_missing_rows,
                      AVG(v.comment_count) AS comments_mean_positive,MAX(v.comment_count) AS comments_max_positive,
                      SUM(CASE WHEN v.repost_count IS NOT NULL THEN 1 ELSE 0 END) AS shares_positive_rows,
                      SUM(CASE WHEN v.repost_count=0 THEN 1 ELSE 0 END) AS shares_zero_rows,
                      SUM(CASE WHEN v.repost_count IS NULL THEN 1 ELSE 0 END) AS shares_missing_rows,
                      AVG(v.repost_count) AS shares_mean_positive,MAX(v.repost_count) AS shares_max_positive,
                      SUM(CASE WHEN v.save_count IS NOT NULL THEN 1 ELSE 0 END) AS saves_positive_rows,
                      SUM(CASE WHEN v.save_count=0 THEN 1 ELSE 0 END) AS saves_zero_rows,
                      SUM(CASE WHEN v.save_count IS NULL THEN 1 ELSE 0 END) AS saves_missing_rows,
                      AVG(v.save_count) AS saves_mean_positive,MAX(v.save_count) AS saves_max_positive
               FROM metric_import_rows v WHERE """ + where +
            " GROUP BY v.creator_key,v.creator_name ORDER BY v.creator_name COLLATE NOCASE", args
        ).fetchall()]

    available_fields = [item.get("field") for item in json.loads(batch["fields_json"] or "[]")]
    return {
        "items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset,
        "summary": summary, "filters": {"q": str(query.get("q") or "").strip()[:120], "creator_keys": creator_keys,
                    "batch_id": batch_id}, "source_mode": "imported", "nullable_metrics": True,
        "available_fields": available_fields,
        "sample_scope": f"仅批次“{batch['source_name']} / {batch['filename']}”中的有效抖音作品",
        "quality_note": "空白或未提供字段保留为缺失，明确的0保留为真实0。导入时间不等于平台采集时间；只有文件含明确采集时间列时才显示来源采集时间。",
    }


def query_metrics(con, query, batch_id=0):
    if batch_id:
        return query_imported_metrics(con, query, batch_id)
    if str(query.get("creator_keys") or "").strip():
        raise ValueError("请选择导入批次后再筛选该来源的博主")
    where, args, blogger_ids, normalized = build_filters(query)
    try:
        limit = max(1, min(int(query.get("limit", 100)), 200))
        offset = max(0, min(int(query.get("offset", 0)), 50_000))
    except (TypeError, ValueError):
        raise ValueError("分页参数必须是整数")
    sort = str(query.get("sort") or "date_desc")
    if sort not in SORTS:
        sort = "date_desc"

    total = con.execute(
        "SELECT COUNT(*) FROM videos v JOIN bloggers b ON b.id=v.blogger_id WHERE " + where,
        args,
    ).fetchone()[0]
    rows = con.execute(
        """SELECT v.id, v.blogger_id, b.name AS blogger_name, b.tags AS blogger_tags,
                  v.video_id, v.url, v.kind, v.title, v.tags, v.upload_date, v.duration,
                  v.like_count, v.comment_count, v.repost_count, v.save_count, v.status,
                  v.updated_at AS local_record_updated_at
           FROM videos v JOIN bloggers b ON b.id=v.blogger_id
           WHERE """ + where + f" ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?",
        args + [limit, offset],
    ).fetchall()

    summaries = []
    if blogger_ids:
        summaries = [dict(row) for row in con.execute(
            """SELECT b.id AS blogger_id, b.name AS blogger_name, b.tags AS blogger_tags,
                      COUNT(*) AS sample_count,
                      SUM(CASE WHEN v.like_count>0 THEN 1 ELSE 0 END) AS likes_positive_rows,
                      SUM(CASE WHEN v.like_count=0 THEN 1 ELSE 0 END) AS likes_zero_rows,
                      AVG(NULLIF(v.like_count,0)) AS likes_mean_positive,
                      MAX(NULLIF(v.like_count,0)) AS likes_max_positive,
                      SUM(CASE WHEN v.comment_count>0 THEN 1 ELSE 0 END) AS comments_positive_rows,
                      SUM(CASE WHEN v.comment_count=0 THEN 1 ELSE 0 END) AS comments_zero_rows,
                      AVG(NULLIF(v.comment_count,0)) AS comments_mean_positive,
                      MAX(NULLIF(v.comment_count,0)) AS comments_max_positive,
                      SUM(CASE WHEN v.repost_count>0 THEN 1 ELSE 0 END) AS shares_positive_rows,
                      SUM(CASE WHEN v.repost_count=0 THEN 1 ELSE 0 END) AS shares_zero_rows,
                      AVG(NULLIF(v.repost_count,0)) AS shares_mean_positive,
                      MAX(NULLIF(v.repost_count,0)) AS shares_max_positive,
                      SUM(CASE WHEN v.save_count>0 THEN 1 ELSE 0 END) AS saves_positive_rows,
                      SUM(CASE WHEN v.save_count=0 THEN 1 ELSE 0 END) AS saves_zero_rows,
                      AVG(NULLIF(v.save_count,0)) AS saves_mean_positive,
                      MAX(NULLIF(v.save_count,0)) AS saves_max_positive
               FROM videos v JOIN bloggers b ON b.id=v.blogger_id
               WHERE """ + where + " GROUP BY b.id,b.name,b.tags ORDER BY b.name COLLATE NOCASE",
            args,
        ).fetchall()]

    return {
        "items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset,
        "summary": summaries, "filters": normalized,
        "sample_scope": "仅本机已入库且符合筛选条件的抖音作品",
        "quality_note": "计数为本机保存值；旧数据中的0无法区分真实0和采集未返回。记录更新时间不保证等于抖音计数的采集时间。播放量尚未入库，不能据此计算播放互动率。",
    }


def export_csv(con, query, max_rows=20_000, batch_id=0):
    import csv
    import io

    if batch_id:
        where, args, _ = _import_filter(query, batch_id)
        if not con.execute("SELECT 1 FROM metric_import_batches WHERE id=?", (batch_id,)).fetchone():
            raise ValueError("导入批次不存在或已删除")
        total = con.execute("SELECT COUNT(*) FROM metric_import_rows v WHERE " + where, args).fetchone()[0]
        if total > max_rows:
            raise ValueError(f"本次结果有{total}条，超过单次导出上限{max_rows}；请增加筛选条件")
        rows = con.execute(
            """SELECT b.source_name,b.filename,b.imported_at,v.captured_at,
                      v.creator_name,v.creator_id,v.video_id,v.title,v.url,v.kind,v.upload_date,v.duration,
                      v.follower_count,v.play_count,v.recommend_count,v.like_count,v.comment_count,v.repost_count,v.save_count
               FROM metric_import_rows v JOIN metric_import_batches b ON b.id=v.batch_id
               WHERE """ + where + " ORDER BY v.upload_date DESC,v.id DESC", args
        ).fetchall()
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["数据来源", "来源文件", "导入时间", "来源采集时间", "博主", "博主ID", "作品ID", "标题", "作品链接", "类型", "发布日期", "时长秒", "粉丝数", "播放量", "推荐数", "点赞", "评论", "分享/转发", "收藏"])
        for row in rows:
            writer.writerow([safe_csv_value(row[key]) for key in (
                "source_name", "filename", "imported_at", "captured_at", "creator_name", "creator_id", "video_id",
                "title", "url", "kind", "upload_date", "duration", "follower_count", "play_count", "recommend_count", "like_count",
                "comment_count", "repost_count", "save_count")])
        return output.getvalue(), total

    where, args, _, _ = build_filters(query)
    total = con.execute(
        "SELECT COUNT(*) FROM videos v JOIN bloggers b ON b.id=v.blogger_id WHERE " + where,
        args,
    ).fetchone()[0]
    if total > max_rows:
        raise ValueError(f"本次结果有{total}条，超过单次导出上限{max_rows}；请增加筛选条件")
    rows = con.execute(
        """SELECT b.name AS blogger_name, v.title, v.url, v.kind, v.upload_date, v.duration,
                  v.like_count, v.comment_count, v.repost_count, v.save_count,
                  v.status, v.updated_at AS local_record_updated_at
           FROM videos v JOIN bloggers b ON b.id=v.blogger_id
           WHERE """ + where + " ORDER BY v.upload_date DESC,v.id DESC",
        args,
    ).fetchall()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["博主", "标题", "作品链接", "类型", "发布日期", "时长秒", "点赞", "评论", "分享/转发", "收藏", "记录状态", "本地记录更新时间"])
    for row in rows:
        writer.writerow([safe_csv_value(row[key]) for key in (
            "blogger_name", "title", "url", "kind", "upload_date", "duration", "like_count",
            "comment_count", "repost_count", "save_count", "status", "local_record_updated_at",
        )])
    return output.getvalue(), total


def safe_csv_value(value):
    if value is None:
        return ""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


_safe_csv_value = safe_csv_value
