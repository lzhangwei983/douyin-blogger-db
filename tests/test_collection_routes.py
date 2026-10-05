"""Ordinary users can request collection in the desktop app itself."""
import sys
from pathlib import Path
import pytest
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
