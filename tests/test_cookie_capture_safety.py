import json
from pathlib import Path

from fastapi.testclient import TestClient

import app


def test_cookie_upload_discards_cookies_outside_douyin_domains(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'DATA_DIR', tmp_path / 'application-data')
    cookies = [
        {'name': 'sessionid', 'value': 'synthetic-douyin-cookie', 'domain': '.douyin.com', 'path': '/'},
        {'name': 'tracker', 'value': 'synthetic-other-site-cookie', 'domain': '.other.test', 'path': '/'},
    ]
    with TestClient(app.app, base_url='http://127.0.0.1') as client:
        response = client.post('/api/cookies/upload', files={
            'file': ('cookies.json', json.dumps(cookies), 'application/json')
        })

    assert response.status_code == 200
    saved = json.loads((tmp_path / 'application-data' / 'work' / 'douyin_cookies.json').read_text(encoding='utf-8'))
    assert [cookie['name'] for cookie in saved] == ['sessionid']
    assert 'synthetic-other-site-cookie' not in (tmp_path / 'application-data' / 'work' / 'douyin_cookies.txt').read_text(encoding='utf-8')


def test_cookie_upload_rejects_files_without_douyin_domain_cookie(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'DATA_DIR', tmp_path / 'application-data')
    folder = tmp_path / 'application-data' / 'work'
    folder.mkdir(parents=True)
    (folder / 'douyin_cookies.json').write_text('[{"name":"sessionid","value":"synthetic-old","domain":".douyin.com"}]', encoding='utf-8')
    cookies = [{'name': 'tracker', 'value': 'synthetic-other-site-cookie', 'domain': '.other.test', 'path': '/'}]

    with TestClient(app.app, base_url='http://127.0.0.1') as client:
        response = client.post('/api/cookies/upload', files={
            'file': ('cookies.json', json.dumps(cookies), 'application/json')
        })

    assert response.status_code == 400
    assert '抖音' in response.json()['detail']
    assert 'synthetic-old' in (folder / 'douyin_cookies.json').read_text(encoding='utf-8')


def test_download_cookie_file_contains_only_douyin_domain_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'DATA_DIR', tmp_path / 'application-data')
    source = app.DATA_DIR / 'work' / 'douyin_cookies.txt'
    source.parent.mkdir(parents=True)
    source.write_text(
        '# Netscape HTTP Cookie File\n'
        '.douyin.com\tTRUE\t/\tTRUE\t0\tsessionid\tsynthetic-douyin-cookie\n'
        '.other.test\tTRUE\t/\tTRUE\t0\ttracker\tsynthetic-other-site-cookie\n',
        encoding='utf-8',
    )

    scoped = Path(app.find_cookie())

    assert scoped != source
    assert 'synthetic-douyin-cookie' in scoped.read_text(encoding='utf-8')
    assert 'synthetic-other-site-cookie' not in scoped.read_text(encoding='utf-8')
    assert source.exists()
    scoped.unlink()
