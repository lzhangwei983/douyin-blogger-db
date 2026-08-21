#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按关键词搜索抖音博主（用户），打印昵称/粉丝数/简介/主页链接
用法:
  python fetch_user_info.py <关键词> [--cookies cookies.txt|json]
说明:
  调用 aweme/v1/web/discover/search 用户搜索接口（同 fetch_user_videos.py 的 cookie 方案）
"""
import sys, json, re, urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

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

def search_users(keyword, cookie_str):
    base = ("https://www.douyin.com/aweme/v1/web/discover/search/"
            "?device_platform=webapp&aid=6383&channel=channel_pc_web&count=15"
            f"&keyword={urllib.parse.quote(keyword)}&offset=0&search_channel=aweme_user"
            "&search_source=switch_tab")
    req = urllib.request.Request(base, headers={
        "User-Agent": UA, "Referer": "https://www.douyin.com/", "Cookie": cookie_str})
    body = urllib.request.urlopen(req, timeout=30).read()
    d = json.loads(body.decode("utf-8", "replace"))
    if d.get("status_code") != 0:
        print(f"[错误] status_code={d.get('status_code')} {str(d.get('status_msg'))[:60]}")
        return []
    out = []
    for u in d.get("user_list") or []:
        info = u.get("user_info") or {}
        out.append({
            "nickname": info.get("nickname"), "sec_uid": info.get("sec_uid"),
            "followers": info.get("follower_count"), "signature": info.get("signature") or "",
            "aweme_count": info.get("aweme_count"),
            "url": f"https://www.douyin.com/user/{info.get('sec_uid')}",
        })
    return out

if __name__ == "__main__":
    args = sys.argv[1:]
    kw = args[0] if args else ""
    cookie = ""
    for i, a in enumerate(args):
        if a == "--cookies" and i + 1 < len(args):
            cookie = cookies_from_file(args[i + 1])
    import urllib.parse
    for u in search_users(kw, cookie):
        print(f"{u['nickname']} | 粉丝 {u['followers']} | 作品 {u['aweme_count']} | {u['signature'][:40]}")
        print(f"  {u['url']}")