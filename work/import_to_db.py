#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量导入：抓取结果(TSV) + 字幕txt + 分析md -> 抖音博主数据库

用法:
  python work/import_to_db.py --name "博主名" --slug slug --platform 抖音 \
      --douyin-id 123 --homepage https://... --tags "标签" --bio "简介" \
      --tsv work/videos.tsv --dir outputs/xxx

目录约定（--dir 下）:
  {seq:02d}_video_{video_id}_subtitle.txt   转写字幕（建议为 AI 校对版）
  {seq:02d}_video_{video_id}_analysis.md    AI 分析（可选）

analysis.md 格式（## 标题 为节）:
  ## 基本信息
  - **标题**: xxx
  - **发布日期**: 20260819
  - **点赞**: 12345
  ## 内容摘要
  ## 核心观点
  ## 可执行建议
  ## 适合的行业或工作方向（归纳博主建议，非事实）
  ## 风险和可疑之处
  ## 对博主可信度的判断
  ## 哪些建议值得执行/需谨慎
"""
import sys, re, csv, json, sqlite3
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))
import app  # noqa: E402  导入即初始化数据库 schema

FIELD_MAP = {
    "内容摘要": "summary",
    "核心观点": "key_points",
    "可执行建议": "advice",
    "适合的行业或工作方向（归纳博主建议，非事实）": "industries",
    "风险和可疑之处": "risks",
    "对博主可信度的判断": "credibility",
    "哪些建议值得执行/需谨慎": "actionable",
}

def parse_md(path):
    text = Path(path).read_text(encoding="utf-8")
    sections, cur = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip()
            sections[cur] = []
        elif cur is not None:
            sections[cur].append(line)
    for k in sections:
        sections[k] = "\n".join(sections[k]).strip()
    info = {}
    for m in re.finditer(r"^- \*\*(.+?)\*\*:\s*(.*)$", sections.get("基本信息", ""), re.M):
        info[m.group(1)] = m.group(2)
    return sections, info

def main():
    args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
    need = ("--name", "--slug", "--tsv", "--dir")
    if not all(k in args for k in need):
        print(__doc__)
        sys.exit(1)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(app.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")

    slug = args["--slug"].strip()
    row = con.execute("SELECT id FROM bloggers WHERE slug=?", (slug,)).fetchone()
    if row:
        con.execute("DELETE FROM videos WHERE blogger_id=?", (row["id"],))
        con.execute("DELETE FROM bloggers WHERE id=?", (row["id"],))
        print(f"已删除旧数据(slug={slug})，重新导入")
    cur = con.execute(
        "INSERT INTO bloggers(slug,name,platform,douyin_id,homepage_url,bio,notes,tags,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (slug, args["--name"], args.get("--platform", "抖音"), args.get("--douyin-id", ""),
         args.get("--homepage", ""), args.get("--bio", ""), "", args.get("--tags", ""), now, now))
    bid = cur.lastrowid

    out = Path(args["--dir"])
    rows = list(csv.DictReader(open(args["--tsv"], encoding="utf-8"), delimiter="\t"))
    n_video = n_sub = n_ana = 0
    for r in rows:
        seq = int(r.get("序号") or 0)
        url = r.get("链接", "").strip()
        if not url:
            continue
        m = re.search(r"(?:/video/|/note/)(\d+)", url)
        vid_id = m.group(1) if m else None
        kind = r.get("类型", "视频")
        title = r.get("标题", "") or ""
        upload = r.get("发布日期", "") or None
        likes = int(r.get("点赞", 0) or 0)
        comments = int(r.get("评论", 0) or 0)
        images = ""
        imgs = [u for u in (r.get("图片", "") or "").split(";") if u.strip()]
        if imgs:
            images = json.dumps(imgs, ensure_ascii=False)
        subtitle = ""
        sub_f = out / f"{seq:02d}_video_{vid_id}_subtitle.txt"
        if not sub_f.exists():
            sub_f = out / "txt" / sub_f.name
        if sub_f.exists():
            subtitle = sub_f.read_text(encoding="utf-8")
        ana_md, has_ana = None, 0
        ana_f = out / f"{seq:02d}_video_{vid_id}_analysis.md"
        if kind == "视频":
            if not ana_f.exists():
                ana_f = out / "txt" / ana_f.name
            if ana_f.exists():
                ana_md = ana_f.read_text(encoding="utf-8")
                has_ana = 1
        cur = con.execute(
            """INSERT INTO videos(blogger_id,seq,video_id,url,kind,title,upload_date,duration,
               like_count,comment_count,repost_count,save_count,status,subtitle,subtitle_chars,has_analysis,images,notes,tags,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (bid, seq, vid_id, url, kind, title or None, upload, int(r.get("时长", 0) or 0),
             likes, comments, int(r.get("转发", 0) or 0), int(r.get("收藏", 0) or 0),
             "ok", subtitle or None, len(subtitle or ""), has_ana, images or None, "", "", now, now))
        vid = cur.lastrowid
        if kind == "视频":
            n_video += 1
            if subtitle:
                n_sub += 1
        if ana_md:
            sections, _ = parse_md(ana_f)
            vals = {f: sections.get(k, "") for k, f in FIELD_MAP.items()}
            con.execute(
                """INSERT INTO analyses(video_id,full_md,summary,key_points,advice,industries,risks,credibility,actionable,parsed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (vid, ana_md, vals["summary"], vals["key_points"], vals["advice"],
                 vals["industries"], vals["risks"], vals["credibility"], vals["actionable"], now))
            n_ana += 1
    con.commit()
    con.close()
    print(f"导入完成: {args['--name']}(id={bid}), 视频 {n_video} 条(含字幕 {n_sub}), 分析 {n_ana} 篇")

if __name__ == "__main__":
    main()