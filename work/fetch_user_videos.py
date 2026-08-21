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
import sys, os, json, re, random, string, time, urllib.request, http.cookiejar

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

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
    return "; ".join(f"{c.name}={c.value}" for c in cj)

def cookies_from_browser(browser):
    from yt_dlp.cookies import extract_cookies_from_browser
    jar = extract_cookies_from_browser(browser)
    return "; ".join(f"{c.name}={c.value}" for c in jar)

def cookies_from_file(path):
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        content = f.read()
    try:
        data = json.loads(content)
        items = data.get("cookies") or data if isinstance(data, dict) else data
        vals = {c.get("name"): c.get("value") for c in items if isinstance(c, dict) and c.get("name")}
        return "; ".join(f"{k}={v}" for k, v in vals.items())
    except json.JSONDecodeError:
        pass
    vals = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            vals[parts[5]] = parts[6]
    return "; ".join(f"{k}={v}" for k, v in vals.items())

def fetch(url, limit, cookie_str, all_mode=False, outfile=None):
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
    cursor, collected = "0", []
    nickname = None
    while True:
        api = base + f"&max_cursor={cursor}"
        req = urllib.request.Request(api, headers={
            "User-Agent": UA, "Referer": "https://www.douyin.com/", "Cookie": cookie_str})
        body = urllib.request.urlopen(req, timeout=30).read()
        if not body:
            print("[错误] API 返回空（风控/登录过期）"); break
        d = json.loads(body.decode("utf-8", "replace"))
        if d.get("status_code") != 0:
            print(f"[错误] status_code={d.get('status_code')} {str(d.get('status_msg'))[:60]}"); break
        lst = d.get("aweme_list") or []
        if lst and nickname is None:
            nickname = (lst[0].get("author") or {}).get("nickname")
        for a in lst:
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
        if not all_mode:
            print(f"[博主] {nickname}  本页 {len(lst)} 条（测试取前 {limit} 条）")
            for a in lst[:limit]:
                st = a.get("statistics") or {}
                print(f"  - {a.get('aweme_id')} | {(a.get('desc') or '')[:36]} | 赞 {st.get('digg_count')} 评 {st.get('comment_count')}")
                print(f"    https://www.douyin.com/video/{a.get('aweme_id')}")
            return
        if d.get("has_more"):
            cursor = str(d.get("max_cursor", "0"))
            time.sleep(1.2)
        else:
            break
    print(f"[博主] {nickname}  共抓取 {len(collected)} 条视频")
    if outfile:
        from datetime import datetime as _dt
        def ymd(ts):
            return _dt.fromtimestamp(ts).strftime("%Y%m%d") if ts else ""
        with open(outfile, "w", encoding="utf-8") as f:
            f.write("序号\t类型\t链接\t标题\t发布日期\t点赞\t评论\t时长\t转发\t收藏\t图片\n")
            for i, v in enumerate(collected, 1):
                f.write("\t".join([
                    str(i),
                    "图文" if v["is_image"] else "视频",
                    v["url"],
                    v["desc"].replace("\n", " "),
                    ymd(v["create_time"]),
                    str(v["likes"] or 0), str(v["comments"] or 0),
                    str(int(v["duration"] or 0) // 1000),
                    str(v["shares"] or 0), str(v["collects"] or 0),
                    ";".join(v["images"]),
                ]) + "\n")
        print(f"[保存] {outfile}")
    else:
        for v in collected:
            print(f"  - {v['id']} | {(v['desc'] or '')[:36]} | 赞 {v['likes']} | {v['url']}")
    print(f"[说明] 全量分页: max_cursor 翻页直到 has_more=false；用户作品总数见上方 作品= 字段")

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
    fetch(url, limit, cookie or "", all_mode=all_mode, outfile=outfile)