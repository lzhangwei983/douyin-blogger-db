"""Load browser-exported cookies and let CookieJar enforce request scope."""
from __future__ import annotations

import json
import re
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from urllib.request import Request


def _make_cookie(name, value, domain, path="/", secure=False, expires=None,
                 domain_specified=True, http_only=False, same_site=None):
    if not name or not domain:
        return None
    rest = {}
    if http_only:
        rest["HttpOnly"] = None
    if same_site:
        rest["SameSite"] = str(same_site)
    return Cookie(
        version=0, name=str(name), value=str(value), port=None, port_specified=False,
        domain=str(domain), domain_specified=bool(domain_specified),
        domain_initial_dot=str(domain).startswith("."), path=str(path or "/"),
        path_specified=True, secure=bool(secure), expires=expires,
        discard=expires is None, comment=None, comment_url=None, rest=rest,
        rfc2109=False,
    )


def _json_cookie_jar(items):
    jar = CookieJar()
    for item in items:
        if not isinstance(item, dict):
            continue
        name, domain = item.get("name"), item.get("domain")
        if not name or not domain:
            continue
        raw_expiry = item.get("expirationDate", item.get("expires"))
        try:
            expiry = int(float(raw_expiry)) if raw_expiry not in (None, "", 0, "0", -1, "-1") else None
        except (TypeError, ValueError, OverflowError):
            expiry = None
        if expiry is not None and expiry <= 0:
            expiry = None
        cookie = _make_cookie(
            name, item.get("value", ""), domain, item.get("path", "/"),
            item.get("secure", False), expiry,
            domain_specified=bool(item.get("domainSpecified", True)),
            http_only=item.get("httpOnly", False), same_site=item.get("sameSite"),
        )
        if cookie:
            jar.set_cookie(cookie)
    return jar


def load_cookie_jar(path):
    """Read a Cookie Editor JSON array or Netscape export without flattening domains."""
    content = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = None
    if data is not None:
        items = data.get("cookies", []) if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ValueError("Cookie JSON 应为数组或包含 cookies 数组的对象")
        jar = _json_cookie_jar(items)
        if not list(jar):
            raise ValueError("Cookie 文件中没有带域名的有效条目")
        return jar

    jar = CookieJar()
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        http_only = line.startswith("#HttpOnly_")
        if http_only:
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            parts = re.split(r"\s+", line, maxsplit=6)
        if len(parts) < 7:
            continue
        domain, include_subdomains, path, secure, raw_expiry, name, value = parts[:7]
        try:
            expiry = int(raw_expiry) if int(raw_expiry) > 0 else None
        except (TypeError, ValueError, OverflowError):
            expiry = None
        cookie = _make_cookie(
            name, value, domain, path, secure.upper() == "TRUE", expiry,
            domain_specified=include_subdomains.upper() == "TRUE" or domain.startswith("."),
            http_only=http_only,
        )
        if cookie:
            jar.set_cookie(cookie)
    if not list(jar):
        raise ValueError("Cookie 文件不是可识别的 Netscape 格式")
    return jar


def cookie_header_for_url(jar, url):
    """Return only cookies that CookieJar deems valid for this exact request URL."""
    request = Request(url)
    jar.add_cookie_header(request)
    return request.get_header("Cookie") or ""


def cookie_file_header(path, url):
    return cookie_header_for_url(load_cookie_jar(path), url)
