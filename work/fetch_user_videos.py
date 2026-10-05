#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从抖音博主主页抓取视频链接（最终版）
用法:
  python fetch_user_videos.py <主页链接或短链> [条数] [--cookies cookies.txt | --browser edge]
说明:
  抖音 web 接口需要有效 Cookie（ttwid+msToken）才能返回作品列表。
  --browser: 从浏览器读取（Edge/Chrome 须已登录抖音且浏览器未运行）
  --cookies: Netscape 格式 cookie 文件（浏览器扩展导出，如 Get cookies.txt LOCALLY）
流程: 短链解析 -> 提取 sec_uid -> 取 Cookie -> 调 aweme/post 分页
"""
import sys, os, json, re, random, string, time, urllib.request, http.cookiejar, csv, io, shutil, uuid
from datetime import datetime
from pathlib import Path
from cookie_scope import cookie_file_header, cookie_header_for_url
from collection_range import CollectionRange, publication_day, pagination_flag

_core_code = str(Path(__file__).resolve().parents[1] / "core")
if _core_code not in sys.path:
    sys.path.insert(0, _core_code)

if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
DOUYIN_POST_COOKIE_URL = "https://www.douyin.com/aweme/v1/web/aweme/post/"


class CollectionError(RuntimeError):
    """A collection run that did not finish successfully and must not replace output."""


def _backup_existing_output(path):
    path = Path(path)
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.{stamp}-{uuid.uuid4().hex[:8]}.bak")
    shutil.copy2(path, backup)
    return backup

def resolve_share(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    r = urllib.request.urlopen(req, timeout=30)
    return r.geturl()

def register_ttwid():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", UA), ("Content-Type", "application/json")]
    body = json.dumps({"region": "cn", "aid": 6383, "needFid": False, "service": "www.douyin.com",
                       "migrate_info": {"ticket": "", "source": "node"}, "cbUrlProtocol": "https", "union": True}).encode()
    d = json.loads(op.open(urllib.request.Request(
        "https://ttwid.bytedance.com/ttwid/union/register/", data=body), timeout=30).read().decode())
    redir = d.get("redirect_url", "")
    if redir:
        op.open(urllib.request.Request(redir), timeout=30).read()
    return cookie_header_for_url(cj, DOUYIN_POST_COOKIE_URL)

def cookies_from_browser(browser):
    from yt_dlp.cookies import extract_cookies_from_browser
    jar = extract_cookies_from_browser(browser)
    return cookie_header_for_url(jar, DOUYIN_POST_COOKIE_URL)

def cookies_from_file(path, url=DOUYIN_POST_COOKIE_URL):
    return cookie_file_header(path, url)

def fetch(url, limit, cookie_str, all_mode=False, outfile=None, max_items=None, on_progress=None,
          date_from=None, date_to=None):
    options=CollectionRange.parse(max_items,date_from,date_to)
    real = resolve_share(url) if "v.douyin.com" in url else url
    m = re.search(r"(?:sec_uid|user)/([^?&/]+)", real)
    if not m:
        print(f"[错误] 无法从 {real} 提取 sec_uid")
        return
    sec_uid = m.group(1)
    if "ttwid" not in cookie_str:
        cookie_str += "; " + register_ttwid()
    print(f"[Cookie] ttwid={'有' if 'ttwid' in cookie_str else '有' if 'ttwid' in cookie_str else '无'}  msToken={'有' if 'msToken' in cookie_str else '无'}")
    base = ("https://www.douyin.com/aweme/v1/web/aweme/post/?device_platform=webapp&aid=6383"
            "&channel=channel_pc_web&sec_user_id=" + sec_uid + "&count=18&publish_video_strategy_type=2")
    cursor, collected, seen_cursors = "0", [], set()
    nickname = None
    profile = {'sec_uid': sec_uid}
    limit_reached = False
    seen_video_ids = set()
    while True:
        if cursor in seen_cursors:
            raise CollectionError("分页游标重复，采集已停止；原有文件未更改")
        seen_cursors.add(cursor)
        api = base + f"&max_cursor={cursor}"
        req = urllib.request.Request(api, headers={
            "User-Agent": UA, "Referer": "https://www.douyin.com/", "Cookie": cookie_str})
        try:
            body = urllib.request.urlopen(req, timeout=30).read()
        except Exception as exc:
            raise CollectionError(f"请求失败（{type(exc).__name__}）；原有文件未更改") from exc
        if not body:
            raise CollectionError("API 返回空（可能是风控或登录状态失效）；原有文件未更改")
        try:
            d = json.loads(body.decode("utf-8", "replace"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CollectionError("API 响应不是有效 JSON；原有文件未更改") from exc
        if not isinstance(d, dict):
            raise CollectionError("API 响应格式无效；原有文件未更改")
        if d.get("status_code") != 0:
            raise CollectionError(f"status_code={d.get('status_code')} {str(d.get('status_msg'))[:60]}；原有文件未更改")
        if options.has_dates:
            try:pagination_flag(d.get('has_more'))
            except ValueError as error:raise CollectionError(str(error)) from None
        lst = d.get("aweme_list") or []
        if not isinstance(lst, list):
            raise CollectionError("API 作品列表格式无效；原有文件未更改")
        if lst and nickname is None:
            author = lst[0].get("author") or {}
            nickname = author.get("nickname")
            profile.update({k: author.get(k) for k in ('nickname', 'unique_id', 'signature')})
        for a in lst:
            video_id = str(a.get('aweme_id') or '')
            if video_id in seen_video_ids:
                continue
            if video_id:
                seen_video_ids.add(video_id)
            author_uid = (a.get('author') or {}).get('sec_uid')
            if on_progress and author_uid and author_uid != sec_uid:
                raise CollectionError('平台返回了其他博主的作品，请重试；原有文件未更改')
            if not options.matches(a.get('create_time')):
                continue
            st = a.get("statistics") or {}
            imgs = a.get("images") or []
            collected.append({
                "id": a.get("aweme_id"), "desc": a.get("desc") or "",
                "likes": st.get("digg_count"), "comments": st.get("comment_count"),
                "shares": st.get("share_count"), "collects": st.get("collect_count"),
                "duration": (a.get("video") or {}).get("duration"),
                "create_time": a.get("create_time"),
                "is_image": bool(imgs),
                "images": [u for img in imgs for u in (img.get("url_list") or []) if u.startswith("http")],
                "url": f"https://www.douyin.com/video/{a.get('aweme_id')}",
            })
        if max_items and not options.has_dates and len(collected) >= max_items:
            collected = collected[:max_items]
            limit_reached = True
        if on_progress:
            on_progress(min(len(collected),max_items) if max_items else len(collected))
        if not all_mode:
            print(f"[博主] {nickname}  本页 {len(lst)} 条（测试取前 {limit} 条）")
            for a in lst[:limit]:
                st = a.get("statistics") or {}
                print(f"  - {a.get('aweme_id')} | {(a.get('desc') or '')[:36]} | 赞 {st.get('digg_count')} 评 {st.get('comment_count')}")
                print(f"    https://www.douyin.com/video/{a.get('aweme_id')}")
            return
        if limit_reached:
            break
        if d.get("has_more"):
            next_cursor = str(d.get("max_cursor", "0"))
            if next_cursor == cursor:
                raise CollectionError("分页游标没有前进，采集已停止；原有文件未更改")
            cursor = next_cursor
            time.sleep(1.2)
        else:
            break
    if options.has_dates:
        limit_reached=bool(max_items and len(collected)>=max_items)
        collected=options.select(collected)
    print(f"[博主] {nickname}  共抓取 {len(collected)} 条视频")
    if outfile:
        if not collected:
            raise CollectionError("采集结果为0条，不替换已有文件")
        from storage import atomic_text
        from datetime import timezone
        def ymd(ts):
            published=publication_day(ts)
            return published.strftime('%Y%m%d') if published else ''
        out_path = Path(outfile)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, delimiter="\t", lineterminator="\n")
        writer.writerow(["序号", "类型", "链接", "标题", "发布日期", "点赞", "评论", "时长", "转发", "收藏", "图片"])
        for i, v in enumerate(collected, 1):
            writer.writerow([
                i, "图文" if v["is_image"] else "视频", v["url"], v["desc"], ymd(v["create_time"]),
                v["likes"] if v["likes"] is not None else 0,
                v["comments"] if v["comments"] is not None else 0,
                int(v["duration"] or 0) // 1000,
                v["shares"] if v["shares"] is not None else 0,
                v["collects"] if v["collects"] is not None else 0,
                ";".join(v["images"]),
            ])
        backup = _backup_existing_output(out_path)
        atomic_text(out_path, buffer.getvalue())
        if backup:
            print(f"[备份] {backup}")
        print(f"[保存] {outfile}")
    else:
        for v in collected:
            print(f"  - {v['id']} | {(v['desc'] or '')[:36]} | 赞 {v['likes']} | {v['url']}")
    print(f"[说明] 全量分页: max_cursor 翻页直到 has_more=false；用户作品总数见上方 作品= 字段")
    return {'profile': profile, 'items': collected, 'limit_reached': limit_reached,
            'range_complete':bool(options.has_dates),'scanned':len(seen_video_ids)}

if __name__ == "__main__":
    args = sys.argv[1:]
    url = args[0] if args else "https://v.douyin.com/o5P068OlKkc/"
    limit, all_mode, outfile, cookie = 3, False, None, None
    i = 1
    while i < len(args):
        if args[i] == "--cookies" and i + 1 < len(args):
            cookie = cookies_from_file(args[i + 1]); i += 2
        elif args[i] == "--browser" and i + 1 < len(args):
            cookie = cookies_from_browser(args[i + 1]); i += 2
        elif args[i] == "--all":
            all_mode = True; i += 1
        elif args[i] == "--out" and i + 1 < len(args):
            outfile = args[i + 1]; all_mode = True; i += 2
        elif args[i].isdigit():
            limit = int(args[i]); i += 1
        else:
            i += 1
    try:
        fetch(url, limit, cookie or "", all_mode=all_mode, outfile=outfile)
    except CollectionError as exc:
        print(f"[采集失败] {exc}", file=sys.stderr)
        raise SystemExit(1)
