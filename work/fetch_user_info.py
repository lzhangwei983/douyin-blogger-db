#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按关键词搜索抖音博主（用户），打印昵称/粉丝数/简介/主页链接
用法:
  python fetch_user_info.py <关键词> [--cookies cookies.txt|json]
说明:
  调用 aweme/v1/web/discover/search 用户搜索接口（同 fetch_user_videos.py 的 cookie 方案）
"""
import sys, json, re, urllib.request
from cookie_scope import cookie_file_header

if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
DOUYIN_USER_SEARCH_URL = "https://www.douyin.com/aweme/v1/web/discover/search/"

def cookies_from_file(path, url=DOUYIN_USER_SEARCH_URL):
    return cookie_file_header(path, url)

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
