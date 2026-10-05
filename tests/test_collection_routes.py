"""Ordinary users can request collection in the desktop app itself."""
import sys
from pathlib import Path
import pytest
import sys
import time
from types import SimpleNamespace
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(app,'DB_PATH',tmp_path/'collection.db')
    app.init_db()
    with TestClient(app.app,base_url='http://127.0.0.1') as test_client:
        yield test_client


def test_video_link_collection_starts_in_app_and_guides_missing_login(client):
    result=client.post('/api/collection/videos',
                       json={'urls':'https://www.douyin.com/video/901'})
    assert result.status_code==400
    assert '登录' in result.json()['detail']


def test_video_collection_rejects_profiles_and_private_hosts_without_network(client):
    result=client.post('/api/collection/videos',
                       json={'urls':'http://127.0.0.1/private\nhttps://www.douyin.com/user/sec-creator'})
    assert result.status_code==400
    assert '抖音' in result.json()['detail']
    assert not client.get('/api/jobs').json().get('items')


def test_profile_collection_in_app_explains_missing_login(client):
    result=client.post('/api/collection',
                       json={'url':'https://www.douyin.com/user/sec-creator','limit':20})
    assert result.status_code==400
    assert '登录' in result.json()['detail']


def test_manual_video_creation_rejects_non_douyin_urls(client):
    blogger=client.post('/api/bloggers',json={'name':'安全测试','slug':'safe-test','platform':'抖音'}).json()

    response=client.post(f"/api/bloggers/{blogger['id']}/videos",json={
        'url':'https://attacker.example/video/123','title':'外站链接'
    })

    assert response.status_code==400
    assert '抖音' in response.json()['detail']
    assert client.get(f"/api/bloggers/{blogger['id']}/videos").json()['total']==0


def test_download_revalidates_legacy_external_urls_before_using_cookies(client,monkeypatch):
    blogger=client.post('/api/bloggers',json={'name':'旧博主','slug':'legacy-url-test','platform':'抖音'}).json()
    video=client.post(f"/api/bloggers/{blogger['id']}/videos",json={
        'url':'https://www.douyin.com/video/901','title':'旧作品'
    }).json()
    video_id=video['id']
    with app.get_db() as con:
        con.execute('UPDATE videos SET url=? WHERE id=?',('https://attacker.example/video/123',video_id))
    calls=[]

    class FakeYtDlp:
        def __init__(self,options): pass
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def extract_info(self,url,download):
            calls.append(url)
            return {'id':'123'}
        def prepare_filename(self,info): return 'unused'

    monkeypatch.setattr(app,'find_cookie',lambda:'synthetic-cookie-file')
    monkeypatch.setitem(sys.modules,'yt_dlp',SimpleNamespace(YoutubeDL=FakeYtDlp))

    response=client.get(f'/api/videos/{video_id}/download')

    assert response.status_code==400
    assert '抖音' in response.json()['detail']
    time.sleep(0.1)
    assert calls==[]
