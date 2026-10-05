"""Date bounds must be applied before quantity and must survive real pagination."""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pytest
import task_store
from test_collection import collection_client, cookies, service
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'work'))


def ts(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone(timedelta(hours=8))).timestamp())


def row(vid, published):
    return {'aweme_id':str(vid),'desc':'公开作品','create_time':published,'video':{},'statistics':{},
            'author':{'sec_uid':'sec-test','nickname':'甲博主'}}


def test_custom_quantity_and_date_bounds_are_saved_in_job(collection_client, monkeypatch):
    root,client=collection_client
    cookies(root);svc=service(root,monkeypatch)
    response=client.post('/api/collection',json={'url':'https://www.douyin.com/user/sec-test','limit':37,
                                               'date_from':'2026-09-01','date_to':'2026-10-04'})
    assert response.status_code==200
    record=svc.read_json(svc.record_path(response.json()['task_id']))
    assert record['limit']==37 and record['date_from']=='2026-09-01' and record['date_to']=='2026-10-04'


@pytest.mark.parametrize('payload',[
    {'limit':-1},{'limit':2.5},{'limit':True},{'limit':'37'},
    {'date_from':'2026-02-30'},{'date_to':'not-a-date'},
    {'date_from':'2026-10-04','date_to':'2026-09-01'},
])
def test_invalid_range_does_not_create_job(collection_client, monkeypatch, payload):
    root,client=collection_client
    cookies(root);service(root,monkeypatch)
    response=client.post('/api/collection',json={'url':'https://www.douyin.com/user/sec-test',**payload})
    assert response.status_code==400
    assert task_store.active('collect')==[]


def test_blank_quantity_means_unlimited_and_single_date_is_valid(collection_client, monkeypatch):
    root,client=collection_client
    cookies(root);svc=service(root,monkeypatch)
    response=client.post('/api/collection',json={'url':'https://www.douyin.com/user/sec-test','limit':None,'date_to':'2026-10-04'})
    assert response.status_code==200
    record=svc.read_json(svc.record_path(response.json()['task_id']))
    assert record['limit']==0 and record['date_from'] is None and record['date_to']=='2026-10-04'


def test_pinned_old_posts_do_not_cut_off_matching_dates_or_consume_limit(collection_client, monkeypatch):
    import fetch_user_videos as fetcher
    pages=[{'status_code':0,'has_more':True,'max_cursor':18,'aweme_list':[
                row(900,ts('2025-01-01T12:00:00')),row(901,ts('2026-10-05T12:00:00'))]},
           {'status_code':0,'has_more':True,'max_cursor':36,'aweme_list':[
                row(902,ts('2026-10-03T00:00:00')),row(902,ts('2026-10-03T00:00:00'))]},
           {'status_code':0,'has_more':False,'aweme_list':[
                row(903,ts('2026-10-04T23:59:59')),row(904,ts('2026-10-03T15:00:00'))]}]
    class Response:
        def read(self):return json.dumps(pages.pop(0)).encode()
    monkeypatch.setattr(fetcher.urllib.request,'urlopen',lambda *a,**k:Response())
    monkeypatch.setattr(fetcher.time,'sleep',lambda *a:None)
    result=fetcher.fetch('https://www.douyin.com/user/sec-test',3,'ttwid=synthetic',all_mode=True,max_items=2,
                         date_from='2026-10-03',date_to='2026-10-04')
    assert [str(item['id']) for item in result['items']]==['903','904']
    assert pages==[] and result['range_complete']


def test_no_date_matches_finishes_without_creating_empty_blogger(collection_client, monkeypatch):
    root,client=collection_client
    cookies(root);svc=service(root,monkeypatch)
    import fetch_user_videos as fetcher
    class Response:
        def read(self):return json.dumps({'status_code':0,'has_more':False,'aweme_list':[row(901,ts('2025-01-01T12:00:00'))]}).encode()
    monkeypatch.setattr(fetcher.urllib.request,'urlopen',lambda *a,**k:Response())
    response=client.post('/api/collection',json={'url':'https://www.douyin.com/user/sec-test','limit':7,'date_from':'2026-10-01'})
    assert response.status_code==200
    tid=response.json()['task_id'];svc.run(tid)
    status=client.get('/api/collection/jobs/'+tid).json()
    assert status['status']=='done' and status['result']['no_matches']
    assert status['result']['collected']==0 and client.get('/api/bloggers').json()==[]


def test_different_range_cannot_silently_reuse_an_active_job(collection_client, monkeypatch):
    root,client=collection_client
    cookies(root);svc=service(root,monkeypatch)
    monkeypatch.setattr(svc,'worker_alive',lambda pid:True)
    first=client.post('/api/collection',json={'url':'https://www.douyin.com/user/sec-test','limit':37,'date_from':'2026-09-01'})
    assert first.status_code==200
    second=client.post('/api/collection',json={'url':'https://www.douyin.com/user/sec-test','limit':50,'date_from':'2026-10-01'})
    assert second.status_code==409
    assert len(task_store.active('collect'))==1


def test_date_boundaries_use_beijing_time_and_exclude_unknown_publication():
    from collection_range import CollectionRange
    window=CollectionRange.parse(0,'2026-10-03','2026-10-04')
    assert window.matches(ts('2026-10-03T00:00:00'))
    assert window.matches(ts('2026-10-04T23:59:59'))
    assert not window.matches(ts('2026-10-02T23:59:59'))
    assert not window.matches(ts('2026-10-05T00:00:00'))
    assert not window.matches(None) and not window.matches(float('nan')) and not window.matches(0)


def test_browser_date_range_matches_api_and_scans_past_unmatched_pages(collection_client, monkeypatch):
    root,_=collection_client
    cookies(root);svc=service(root,monkeypatch)
    import playwright.sync_api
    import browser_runtime
    pages=[{'status_code':0,'has_more':True,'aweme_list':[row(800+i,ts('2025-01-01T12:00:00'))]} for i in range(15)]
    pages.extend([
        {'status_code':0,'has_more':True,'aweme_list':[row(902,ts('2026-10-03T00:00:00'))]},
        {'status_code':0,'has_more':False,'aweme_list':[row(903,ts('2026-10-04T23:59:59'))]},
    ])
    class Response:
        url='https://www.douyin.com/aweme/v1/web/aweme/post/?sec_user_id=sec-test'
        def json(self):return pages.pop(0)
    class Browser:
        def new_context(self,**kwargs):return self
        def add_cookies(self,items):pass
        def new_page(self):return self
        def on(self,event,handler):self.receive=handler
        def goto(self,*args,**kwargs):self.receive(Response())
        @property
        def mouse(self):return self
        def wheel(self,*args):pass
        def wait_for_timeout(self,*args):self.receive(Response())
        def close(self):pass
    class Runtime:
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr(playwright.sync_api,'sync_playwright',lambda:Runtime())
    monkeypatch.setattr(browser_runtime,'launch_browser',lambda runtime:Browser())
    result=svc.capture_with_browser('https://www.douyin.com/user/sec-test',1,lambda count:None,'2026-10-03','2026-10-04')
    assert [item['id'] for item in result['items']]==['903']
    assert result['scanned']==17 and result['range_complete'] and pages==[]


@pytest.mark.parametrize('state',[None,'0',2,0.0])
def test_unknown_pagination_cannot_be_reported_as_complete_date_range(collection_client, monkeypatch, state):
    import fetch_user_videos as fetcher
    class Response:
        def read(self):return json.dumps({'status_code':0,'has_more':state,'aweme_list':[row(901,ts('2025-01-01T12:00:00'))]}).encode()
    monkeypatch.setattr(fetcher.urllib.request,'urlopen',lambda *a,**k:Response())
    with pytest.raises(fetcher.CollectionError,match='分页状态'):
        fetcher.fetch('https://www.douyin.com/user/sec-test',3,'ttwid=synthetic',all_mode=True,
                      date_from='2026-10-01')
