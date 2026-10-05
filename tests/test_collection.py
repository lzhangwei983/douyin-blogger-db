"""Collection must create real records, keep edits, and fail without losing data."""
import json
import sys
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import storage
import task_store


@pytest.fixture
def collection_client(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(app, 'DB_PATH', tmp_path / 'douyin_blog.db')
    monkeypatch.setattr(storage, 'BASE_DIR', tmp_path)
    monkeypatch.setattr(app, 'APP_STATE_DIR', tmp_path / 'runtime')
    monkeypatch.setattr(storage, 'APP_STATE_DIR', tmp_path / 'runtime')
    monkeypatch.setattr(task_store, 'APP_STATE_DIR', tmp_path / 'runtime')
    app.init_db()
    with TestClient(app.app, base_url='http://127.0.0.1', raise_server_exceptions=False) as client:
        yield tmp_path, client


def cookies(root):
    storage.write_json(root / 'work/douyin_cookies.json', [
        {'name': 'sessionid', 'value': 'synthetic-private', 'domain': '.douyin.com', 'path': '/', 'expires': -1},
        {'name': 'ttwid', 'value': 'synthetic', 'domain': '.douyin.com', 'path': '/', 'expires': -1},
    ])


def service(root, monkeypatch):
    import collection_service as svc
    monkeypatch.setattr(svc, 'BASE_DIR', root)
    monkeypatch.setattr(svc, 'APP_STATE_DIR', root / 'runtime')
    monkeypatch.setattr(svc, 'spawn_worker', lambda tid: 987654321)
    return svc


def test_collection_route_rejects_non_douyin_target_without_creating_job(collection_client):
    root, client = collection_client
    response = client.post('/api/collection', json={'url': 'http://127.0.0.1/private'})
    assert response.status_code == 400
    assert '抖音' in response.json()['detail']
    assert task_store.active('collect') == []


def test_collection_route_explains_missing_login_before_starting(collection_client):
    root, client = collection_client
    response = client.post('/api/collection', json={'url': 'https://www.douyin.com/user/sec-test'})
    assert response.status_code == 400
    assert '登录' in response.json()['detail']
    assert task_store.active('collect') == []


def test_repeated_click_reuses_one_persistent_collection_job(collection_client, monkeypatch):
    root, client = collection_client
    cookies(root)
    svc = service(root, monkeypatch)
    monkeypatch.setattr(svc, 'worker_alive', lambda pid: True)
    first = client.post('/api/collection', json={'url': 'https://www.douyin.com/user/sec-test?from=share'})
    second = client.post('/api/collection', json={'url': 'https://www.douyin.com/user/sec-test'})
    assert first.status_code == second.status_code == 200
    assert first.json()['task_id'] == second.json()['task_id']
    assert len(task_store.active('collect')) == 1
    public = client.get('/api/collection/jobs/' + first.json()['task_id']).text
    assert 'synthetic-private' not in public


def capture_result():
    return {'profile': {'sec_uid': 'sec-test', 'nickname': '甲博主', 'unique_id': 'creator-test', 'signature': '公开简介'},
            'items': [{'id': '901', 'url': 'https://www.douyin.com/video/901', 'desc': 'AI\t教程\n第二行',
                       'likes': 10, 'comments': 2, 'shares': 3, 'collects': 4, 'create_time': 1780000000,
                       'duration': 45000, 'is_image': False, 'images': []}], 'limit_reached': False}


def test_collection_worker_imports_records_and_repeated_collection_preserves_notes(collection_client, monkeypatch):
    root, client = collection_client
    cookies(root)
    svc = service(root, monkeypatch)
    monkeypatch.setattr(svc, 'capture_profile', lambda url, limit, progress: capture_result())
    response = client.post('/api/collection', json={'url': 'https://www.douyin.com/user/sec-test'})
    tid = response.json()['task_id']
    svc.run(tid)
    status = client.get('/api/collection/jobs/' + tid).json()
    assert status['status'] == 'done'
    bid = status['result']['blogger_id']
    items = client.get(f'/api/bloggers/{bid}/videos').json()['items']
    assert len(items) == 1 and items[0]['like_count'] == 10
    assert items[0]['title'] == 'AI\t教程\n第二行'
    vid = items[0]['id']
    client.put(f'/api/videos/{vid}', json={'title': '人工标题', 'notes': '我的笔记', 'subtitle': '校对字幕'})
    response = client.post('/api/collection', json={'url': 'https://www.douyin.com/user/sec-test'})
    svc.run(response.json()['task_id'])
    items = client.get(f'/api/bloggers/{bid}/videos').json()['items']
    assert len(items) == 1 and items[0]['id'] == vid
    assert items[0]['title'] == '人工标题' and items[0]['notes'] == '我的笔记' and items[0]['subtitle'] == '校对字幕'


def test_failed_capture_keeps_existing_library_and_reports_failure(collection_client, monkeypatch):
    root, client = collection_client
    cookies(root)
    svc = service(root, monkeypatch)
    bid = client.post('/api/bloggers', json={'name': '保留博主', 'slug': 'keep'}).json()['id']
    client.post(f'/api/bloggers/{bid}/videos', json={'url': 'https://www.douyin.com/video/902', 'title': '原有作品'})
    def blocked(*args):
        raise svc.CollectionError('平台暂未返回作品，请检查登录状态后重试')
    monkeypatch.setattr(svc, 'capture_profile', blocked)
    tid = client.post('/api/collection', json={'url': 'https://www.douyin.com/user/sec-test'}).json()['task_id']
    svc.run(tid)
    state = client.get('/api/collection/jobs/' + tid).json()
    assert state['status'] == 'failed' and '登录' in state['message']
    assert client.get(f'/api/bloggers/{bid}/videos').json()['total'] == 1


def test_foreign_job_cannot_be_returned_as_a_collection_job(collection_client):
    _, client = collection_client
    job, _ = task_store.create('unrelated_task', '2099-01-01')
    assert client.get('/api/collection/jobs/' + job['id']).status_code == 404


def test_malformed_platform_row_rolls_back_whole_import(collection_client, monkeypatch):
    root, client = collection_client
    svc = service(root, monkeypatch)
    data = capture_result()
    data['items'].append({**data['items'][0], 'id': '903', 'duration': 'invalid'})
    with pytest.raises((ValueError, svc.CollectionError)):
        svc.import_items('https://www.douyin.com/user/sec-test', data)
    assert client.get('/api/bloggers').json() == []


def test_collection_reports_preserved_blogger_name_and_deduplicated_records(collection_client, monkeypatch):
    root, client = collection_client
    svc = service(root, monkeypatch)
    data = capture_result()
    result = svc.import_items('https://www.douyin.com/user/sec-test', data)
    bid = result['blogger_id']
    client.put(f'/api/bloggers/{bid}', json={'name':'我的称呼'})
    data['items'].append(dict(data['items'][0]))
    result = svc.import_items('https://www.douyin.com/user/sec-test', data)
    assert result['name'] == '我的称呼'
    assert result['collected'] == 1 and result['updated'] == 1


def test_collection_never_imports_foreign_author_result(collection_client, monkeypatch):
    root, client = collection_client
    svc = service(root, monkeypatch)
    data = capture_result()
    data['profile']['sec_uid'] = 'some-other-author'
    with pytest.raises(svc.CollectionError):
        svc.import_items('https://www.douyin.com/user/sec-test', data)
    assert client.get('/api/bloggers').json() == []


def test_share_redirect_cannot_hide_private_target_behind_a_public_url(collection_client, monkeypatch):
    root, _ = collection_client
    svc = service(root, monkeypatch)
    followed = []
    class Opener:
        def __init__(self, handler):self.handler = handler
        def open(self, request, **kwargs):
            destination = 'ftp://127.0.0.1/private/https://www.douyin.com/user/sec-test'
            next_request=self.handler.redirect_request(request,None,302,'',{},destination)
            followed.append(next_request.full_url)
            raise OSError('synthetic transport failure')
    monkeypatch.setattr(svc.urllib.request,'build_opener',lambda handler:Opener(handler))
    with pytest.raises(svc.CollectionError):
        svc.resolve_profile('https://v.douyin.com/abcDEF/')
    assert followed == []


def test_existing_blogger_share_link_is_merged_without_an_empty_duplicate(collection_client, monkeypatch):
    root, client = collection_client
    svc = service(root, monkeypatch)
    bid=client.post('/api/bloggers',json={'name':'旧博主','slug':'custom_slug','homepage_url':'https://v.douyin.com/abcDEF/'}).json()['id']
    client.post(f'/api/bloggers/{bid}/videos',json={'url':'https://www.douyin.com/video/901','like_count':10,'notes':'保留'})
    data=capture_result();data['items'][0]['likes']=99
    result=svc.import_items('https://www.douyin.com/user/sec-test',data)
    assert result['blogger_id']==bid
    assert len(client.get('/api/bloggers').json())==1
    items=client.get(f'/api/bloggers/{bid}/videos').json()['items']
    assert items[0]['like_count']==99 and items[0]['notes']=='保留'


def test_recent_collection_limit_counts_unique_works_not_duplicate_page_rows(collection_client, monkeypatch):
    root, _=collection_client
    service(root,monkeypatch)
    import fetch_user_videos as fetcher
    def item(vid):return {'aweme_id':str(vid),'author':{'sec_uid':'sec-test','nickname':'甲'},'video':{},'statistics':{}}
    pages=[{'status_code':0,'aweme_list':[item(901),item(901)],'has_more':True,'max_cursor':18},
           {'status_code':0,'aweme_list':[item(902)],'has_more':False}]
    class Response:
        def read(self):return json.dumps(pages.pop(0)).encode()
    monkeypatch.setattr(fetcher.urllib.request,'urlopen',lambda *a,**k:Response())
    monkeypatch.setattr(fetcher.time,'sleep',lambda *args:None)
    result=fetcher.fetch('https://www.douyin.com/user/sec-test',3,'ttwid=synthetic',all_mode=True,max_items=2)
    assert [row['id'] for row in result['items']]==['901','902']


def test_launcher_smoke_exercises_collection_endpoint_without_login(tmp_path):
    report=tmp_path/'smoke.json'
    env={**os.environ,'DYDB_HOME':str(tmp_path/'data'),'DYDB_SMOKE_REPORT':str(report),'PYTHONDONTWRITEBYTECODE':'1'}
    main=Path(app.__file__).resolve().parent/'main.py'
    result=subprocess.run([sys.executable,str(main),'--smoke-test'],env=env,capture_output=True,text=True,timeout=35)
    assert result.returncode==0,result.stderr
    content=json.loads(report.read_text(encoding='utf-8'))
    assert content['ready']
    assert content.get('collection_start',{}).get('status')==400
    assert '登录' in content['collection_start']['detail']


def test_software_login_can_start_without_cookie_files(collection_client, monkeypatch):
    root, client=collection_client
    svc=service(root,monkeypatch)
    response=client.post('/api/collection/login')
    assert response.status_code==200
    task=client.get('/api/collection/jobs/'+response.json()['task_id']).json()
    assert task['kind']=='douyin_login' and task['status']=='queued'
    assert not (root/'work/douyin_cookies.json').exists()


def test_login_snapshot_rejects_anonymous_state_and_preserves_old_files(collection_client, monkeypatch):
    root, _=collection_client
    svc=service(root,monkeypatch)
    cookies(root)
    before=(root/'work/douyin_cookies.json').read_bytes()
    with pytest.raises(svc.CollectionError):
        svc.save_login_cookies([{'name':'ttwid','value':'anonymous','domain':'.douyin.com','path':'/'}])
    assert (root/'work/douyin_cookies.json').read_bytes()==before


def test_cookie_pair_rolls_back_when_second_promotion_fails(collection_client, monkeypatch):
    root, _=collection_client
    svc=service(root,monkeypatch)
    cookies(root)
    txt=root/'work/douyin_cookies.txt';txt.write_text('previous cookie txt',encoding='utf-8')
    before=(root/'work/douyin_cookies.json').read_bytes()
    real_replace=svc.os.replace
    def promote(source,destination):
        if Path(destination)==txt and '.login-cookie-tmp' in str(source):
            raise PermissionError('synthetic file lock')
        return real_replace(source,destination)
    monkeypatch.setattr(svc.os,'replace',promote)
    with pytest.raises(OSError):
        svc.save_login_cookies([{'name':'sessionid','value':'new-synthetic','domain':'.douyin.com','path':'/','expires':-1}])
    assert (root/'work/douyin_cookies.json').read_bytes()==before
    assert txt.read_text()=='previous cookie txt'


def test_login_worker_saves_only_douyin_session_before_closing_context(collection_client, monkeypatch):
    root, client=collection_client
    svc=service(root,monkeypatch)
    from types import SimpleNamespace
    import playwright.sync_api
    headed=[]
    class Browser:
        connected=True
        def new_context(self):return self
        def new_page(self):return self
        def goto(self,url,**kwargs):assert url=='https://www.douyin.com/'
        def is_connected(self):return self.connected
        def cookies(self,urls):
            assert self.connected
            return [{'name':'sessionid','value':'synthetic-login','domain':'.douyin.com','path':'/','expires':-1,'httpOnly':True},
                    {'name':'other_session','value':'synthetic-foreign','domain':'.example.test','path':'/','expires':-1}]
        def close(self):self.connected=False
    browser=Browser()
    def launch(**kwargs):headed.append(kwargs['headless']);return browser
    class Runtime:
        def __enter__(self):return SimpleNamespace(chromium=SimpleNamespace(launch=launch))
        def __exit__(self,*args):pass
    monkeypatch.setattr(playwright.sync_api,'sync_playwright',lambda:Runtime())
    tid=client.post('/api/collection/login').json()['task_id']
    svc.run_login(tid)
    state=client.get('/api/collection/jobs/'+tid).json()
    assert state['status']=='done' and state['result']['logged_in']
    assert headed==[False] and not browser.connected
    persisted=json.loads((root/'work/douyin_cookies.json').read_text())
    assert [cookie['name'] for cookie in persisted]==['sessionid']


def test_frozen_collection_spawns_its_own_runtime_not_system_python(collection_client, monkeypatch):
    root,_=collection_client
    import collection_service as svc
    monkeypatch.setattr(svc,'BASE_DIR',root)
    from types import SimpleNamespace
    commands=[]
    environments=[]
    monkeypatch.setattr(svc.sys,'frozen',True,raising=False)
    monkeypatch.setattr(svc.sys,'executable',str(root/'DouyinBlogDB-1.1.1.exe'))
    monkeypatch.setattr(svc,'get_python',lambda:(_ for _ in ()).throw(RuntimeError('No system Python')))
    def spawn(command,**kwargs):
        commands.append(command);environments.append(kwargs['env']);return SimpleNamespace(pid=9911)
    monkeypatch.setattr(svc.subprocess,'Popen',spawn)
    assert svc.spawn_worker('a'*32)==9911
    assert commands==[[str(root/'DouyinBlogDB-1.1.1.exe'),'--worker-job','a'*32]]
    reset=environments[0].get('PYINSTALLER_RESET_ENVIRONMENT')
    assert reset=='1'
