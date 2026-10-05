import csv
import http.cookiejar
import json
import sys
import time
from http.cookiejar import Cookie
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "work"))
import fetch_user_videos as fetcher
import fetch_user_info as user_info


def cookie(name, value, domain, path="/", *, secure=False, expires=None):
    return Cookie(
        version=0, name=name, value=value, port=None, port_specified=False,
        domain=domain, domain_specified=True, domain_initial_dot=domain.startswith("."),
        path=path, path_specified=True, secure=secure, expires=expires,
        discard=expires is None, comment=None, comment_url=None, rest={}, rfc2109=False,
    )


def sample_aweme(aweme_id="v1", desc="demo", likes=10):
    return {
        "aweme_id": aweme_id, "desc": desc, "create_time": 1780000000,
        "statistics": {"digg_count": likes, "comment_count": 2, "share_count": 1, "collect_count": 3},
        "video": {"duration": 45000}, "images": [],
        "author": {"nickname": "演示博主"},
    }


def test_netscape_cookie_file_only_sends_matching_domain_path_and_live_cookies(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        ".douyin.com\tTRUE\t/\tTRUE\t0\tsessionid\tok\n"
        ".example.com\tTRUE\t/\tTRUE\t0\tother_session\tsecret\n"
        ".douyin.com\tTRUE\t/user\tTRUE\t0\twrong_path\tno\n"
        ".douyin.com\tTRUE\t/\tTRUE\t1\texpired\tno\n",
        encoding="utf-8",
    )
    header = fetcher.cookies_from_file(path)
    assert "sessionid=ok" in header
    assert "other_session=secret" not in header
    assert "wrong_path=no" not in header
    assert "expired=no" not in header


def test_user_search_cookie_parser_does_not_flatten_unrelated_sites(tmp_path):
    path = tmp_path / "browser-export.txt"
    path.write_text(
        ".douyin.com\tTRUE\t/\tTRUE\t0\tsearch_session\tok\n"
        ".example.com\tTRUE\t/\tTRUE\t0\tmail_session\tsecret\n",
        encoding="utf-8",
    )
    header = user_info.cookies_from_file(path)
    assert "search_session=ok" in header
    assert "mail_session=secret" not in header


def test_browser_cookie_jar_is_scoped_to_douyin_request(monkeypatch):
    import yt_dlp.cookies

    jar = http.cookiejar.CookieJar()
    jar.set_cookie(cookie("sessionid", "ok", ".douyin.com"))
    jar.set_cookie(cookie("other_session", "secret", ".example.com"))
    jar.set_cookie(cookie("wrong_path", "no", ".douyin.com", "/user"))
    monkeypatch.setattr(yt_dlp.cookies, "extract_cookies_from_browser", lambda browser: jar)

    header = fetcher.cookies_from_browser("chrome")
    assert "sessionid=ok" in header
    assert "other_session=secret" not in header
    assert "wrong_path=no" not in header


def test_failed_second_page_keeps_existing_complete_output(tmp_path, monkeypatch):
    target = tmp_path / "works.tsv"
    previous = "previous complete export\n"
    target.write_text(previous, encoding="utf-8")
    responses = [
        {"status_code": 0, "aweme_list": [sample_aweme("v1")], "has_more": 1, "max_cursor": 18},
        {"status_code": 2190008, "status_msg": "verify", "aweme_list": [], "has_more": 0},
    ]

    class Response:
        def __init__(self, body): self.body = json.dumps(body).encode("utf-8")
        def read(self): return self.body

    monkeypatch.setattr(fetcher, "resolve_share", lambda url: url)
    monkeypatch.setattr(fetcher, "register_ttwid", lambda: "ttwid=fake")
    monkeypatch.setattr(fetcher.urllib.request, "urlopen", lambda *a, **k: Response(responses.pop(0)))
    with pytest.raises(RuntimeError, match="status_code"):
        fetcher.fetch("https://www.douyin.com/user/sec-demo", 3, "ttwid=fake", all_mode=True, outfile=target)
    assert target.read_text(encoding="utf-8") == previous


def test_successful_export_round_trips_tabs_newlines_and_quotes(tmp_path, monkeypatch):
    target = tmp_path / "works.tsv"
    source_title = '标题\t含tab "引号"\n第二行'

    class Response:
        def read(self):
            return json.dumps({"status_code": 0, "aweme_list": [sample_aweme(desc=source_title)], "has_more": 0}).encode("utf-8")

    monkeypatch.setattr(fetcher, "resolve_share", lambda url: url)
    monkeypatch.setattr(fetcher, "register_ttwid", lambda: "ttwid=fake")
    monkeypatch.setattr(fetcher.urllib.request, "urlopen", lambda *a, **k: Response())
    fetcher.fetch("https://www.douyin.com/user/sec-demo", 3, "ttwid=fake", all_mode=True, outfile=target)

    with target.open(encoding="utf-8", newline="") as source:
        row = next(csv.DictReader(source, delimiter="\t"))
    assert row["标题"] == source_title
    assert row["点赞"] == "10"
