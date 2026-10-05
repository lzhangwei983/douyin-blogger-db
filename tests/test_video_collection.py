import json
import pytest
import task_store
from test_collection import collection_client, cookies, service


def videos(root,monkeypatch):
    service(root,monkeypatch)
    import video_collection_service as module
    return module


def metadata(vid,uid='sec-alpha',name='甲作者',likes=10):
    return {'profile':{'sec_uid':uid,'nickname':name,'unique_id':uid,'signature':'公开简介'},
            'items':[{'id':vid,'desc':'视频 '+vid,'url':'https://www.douyin.com/video/'+vid,
                      'likes':likes,'comments':2,'shares':3,'collects':4,'duration':45000,
                      'create_time':1780000000,'is_image':False,'images':[]}]}


def test_video_input_rejects_profile_or_private_url_without_creating_task(collection_client):
    _,client=collection_client
    response=client.post('/api/collection/videos',json={'urls':'http://127.0.0.1/private\nhttps://www.douyin.com/user/sec-alpha'})
    assert response.status_code==400 and '视频' in response.json()['detail']
    assert task_store.active('video_collect')==[]


def test_direct_video_collection_guides_first_login(collection_client):
    _,client=collection_client
    response=client.post('/api/collection/videos',json={'urls':'https://www.douyin.com/video/901'})
    assert response.status_code==400 and '登录' in response.json()['detail']


def test_batch_imports_multiple_authors_and_continues_after_one_failure(collection_client,monkeypatch):
    root,client=collection_client;cookies(root);module=videos(root,monkeypatch)
    def capture(url):
        if url.endswith('/903'):raise module.CollectionError('该作品暂不可访问，请检查登录状态')
        return metadata(url.rsplit('/',1)[-1],*({'901':('sec-alpha','甲作者'),'902':('sec-beta','乙作者')}[url.rsplit('/',1)[-1]]))
    monkeypatch.setattr(module,'capture_video',capture)
    text='分享视频 https://www.douyin.com/video/901?from=share\nhttps://www.douyin.com/video/902\nhttps://www.douyin.com/video/903\nhttps://www.douyin.com/video/901'
    response=client.post('/api/collection/videos',json={'urls':text})
    assert response.status_code==200
    tid=response.json()['task_id'];module.run(tid)
    state=client.get('/api/collection/jobs/'+tid).json()
    assert state['status']=='partial'
    assert state['summary']=={'total':4,'success':2,'failed':1,'duplicate':1,'invalid':0,'finished':4}
    assert [row['status'] for row in state['rows']]==['done','done','failed','duplicate']
    names=client.get('/api/bloggers').json()
    assert {row['name'] for row in names}=={'甲作者','乙作者'}
    assert sum(row['video_count'] for row in names)==2
    assert 'synthetic-private' not in json.dumps(state)


def test_single_video_refresh_preserves_user_edits(collection_client,monkeypatch):
    root,client=collection_client;cookies(root);module=videos(root,monkeypatch)
    monkeypatch.setattr(module,'capture_video',lambda url:metadata('901'))
    tid=client.post('/api/collection/videos',json={'urls':'https://www.douyin.com/video/901'}).json()['task_id']
    module.run(tid)
    state=client.get('/api/collection/jobs/'+tid).json();bid=state['rows'][0]['blogger_id']
    vid=client.get(f'/api/bloggers/{bid}/videos').json()['items'][0]['id']
    client.put(f'/api/videos/{vid}',json={'title':'手工标题','notes':'私人笔记','subtitle':'校对字幕'})
    monkeypatch.setattr(module,'capture_video',lambda url:metadata('901',likes=99))
    tid=client.post('/api/collection/videos',json={'urls':'https://www.douyin.com/video/901'}).json()['task_id']
    module.run(tid)
    item=client.get(f'/api/videos/{vid}').json()['video']
    assert item['title']=='手工标题' and item['notes']=='私人笔记' and item['subtitle']=='校对字幕' and item['like_count']==99
    assert client.get(f'/api/bloggers/{bid}/videos').json()['total']==1


def test_detail_must_match_requested_video_and_include_stable_author(collection_client,monkeypatch):
    root,_=collection_client;module=videos(root,monkeypatch)
    payload={'aweme_id':'902','desc':'其他作品','author':{'sec_uid':'sec-alpha','nickname':'甲'},'statistics':{}}
    with pytest.raises(module.CollectionError):module.parse_detail(payload,'901')
    payload['aweme_id']='901';payload['author']={'nickname':'只有昵称'}
    with pytest.raises(module.CollectionError):module.parse_detail(payload,'901')


def test_invalid_lines_are_reported_but_valid_lines_still_import(collection_client,monkeypatch):
    root,client=collection_client;cookies(root);module=videos(root,monkeypatch)
    monkeypatch.setattr(module,'capture_video',lambda url:metadata('901'))
    response=client.post('/api/collection/videos',json={'urls':'not a URL\nhttps://www.douyin.com/video/901\nhttps://untrusted.example/?token=do-not-store'})
    assert response.status_code==200
    tid=response.json()['task_id'];module.run(tid)
    state=client.get('/api/collection/jobs/'+tid).json()
    assert state['summary']['success']==1 and state['summary']['invalid']==2 and state['status']=='partial'
    assert 'do-not-store' not in json.dumps(state)


def test_all_failed_links_do_not_create_empty_bloggers(collection_client,monkeypatch):
    root,client=collection_client;cookies(root);module=videos(root,monkeypatch)
    def reject(url):raise module.CollectionError('平台未返回该作品')
    monkeypatch.setattr(module,'capture_video',reject)
    tid=client.post('/api/collection/videos',json={'urls':'https://www.douyin.com/video/901'}).json()['task_id']
    module.run(tid)
    assert client.get('/api/collection/jobs/'+tid).json()['status']=='failed'
    assert client.get('/api/bloggers').json()==[]


def test_retry_only_failed_links_and_shortlink_alias_is_not_captured_twice(collection_client,monkeypatch):
    root,client=collection_client;cookies(root);module=videos(root,monkeypatch);calls=[]
    monkeypatch.setattr(module,'resolve_video',lambda url:'https://www.douyin.com/video/901' if 'v.douyin.com' in url else url)
    def capture(url):
        calls.append(url)
        if url.endswith('/903'):raise module.CollectionError('该作品暂不可访问')
        return metadata(url.rsplit('/',1)[-1])
    monkeypatch.setattr(module,'capture_video',capture)
    tid=client.post('/api/collection/videos',json={'urls':'https://www.douyin.com/video/901\nhttps://v.douyin.com/abcde/\nhttps://www.douyin.com/video/903\ninvalid'}).json()['task_id']
    assert client.post('/api/collection/videos/'+tid+'/retry').status_code==400
    module.run(tid)
    state=client.get('/api/collection/jobs/'+tid).json()
    assert state['summary']['duplicate']==1 and len(calls)==2
    retried=client.post('/api/collection/videos/'+tid+'/retry').json()['task_id']
    assert [row['url'] for row in module.status(retried)['rows']]==['https://www.douyin.com/video/903']
    monkeypatch.setattr(module,'capture_video',lambda url:metadata('903'))
    module.run(retried)
    assert module.status(retried)['status']=='done'
    assert client.post('/api/collection/videos/'+retried+'/retry').status_code==400
    assert sum(row['video_count'] for row in client.get('/api/bloggers').json())==2


def test_non_object_api_response_falls_back_to_browser(collection_client,monkeypatch):
    import io
    root,_=collection_client;cookies(root);module=videos(root,monkeypatch)
    monkeypatch.setattr(module.urllib.request,'urlopen',lambda *args,**kwargs:io.BytesIO(b'[]'))
    monkeypatch.setattr(module.urllib.request,'build_opener',lambda *args:type('Opener',(),{'open':lambda *args,**kwargs:io.BytesIO(b'[]')})())
    monkeypatch.setattr(module,'capture_with_browser',lambda url,vid:metadata(vid))
    assert module.capture_video('https://www.douyin.com/video/901')['items'][0]['id']=='901'


def test_malformed_author_is_rejected_with_chinese_guidance(collection_client,monkeypatch):
    root,_=collection_client;module=videos(root,monkeypatch)
    with pytest.raises(module.CollectionError,match='作者'):
        module.parse_detail({'aweme_id':'901','author':['bad'],'statistics':{}},'901')


def test_video_detail_request_does_not_forward_cookie_on_redirect(collection_client,monkeypatch):
    import io
    root,_=collection_client;cookies(root);module=videos(root,monkeypatch);checked=[]
    def opener(*handlers):
        assert handlers,'详情请求必须安装重定向防护'
        request=module.urllib.request.Request('https://www.douyin.com/aweme/v1/web/aweme/detail/',headers={'Cookie':'synthetic-private'})
        with pytest.raises(module.CollectionError):
            handlers[0].redirect_request(request,None,302,'redirect',{},'https://untrusted.example/')
        checked.append(True)
        return type('Opener',(),{'open':lambda *args,**kwargs:io.BytesIO(b'{}')})()
    monkeypatch.setattr(module.urllib.request,'build_opener',opener)
    monkeypatch.setattr(module.urllib.request,'urlopen',lambda *args,**kwargs:io.BytesIO(b'{}'))
    monkeypatch.setattr(module,'capture_with_browser',lambda url,vid:metadata(vid))
    module.capture_video('https://www.douyin.com/video/901')
    assert checked


def test_browser_fallback_reads_only_requested_public_work(collection_client,monkeypatch):
    import browser_runtime
    root,_=collection_client;cookies(root);module=videos(root,monkeypatch)
    payload={'aweme_id':'901','desc':'指定视频','author':{'sec_uid':'sec-alpha','nickname':'甲作者','session':'do-not-store'},
             'statistics':{'digg_count':0,'comment_count':7},'video':{'duration':45000},'create_time':1780000000}
    html='<script>window._ROUTER_DATA='+json.dumps({'other':{'aweme_id':'902','author':{},'statistics':{}},'selected':payload})+'</script>'
    def launch(runtime):
        browser=runtime.chromium.launch(channel='chrome',headless=True);new_context=browser.new_context
        def isolated(**kwargs):
            context=new_context(**kwargs);new_page=context.new_page
            def offline_page():
                page=new_page();goto=page.goto
                def offline_goto(*args,**options):
                    page.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body=html))
                    return goto(*args,**options)
                monkeypatch.setattr(page,'goto',offline_goto)
                return page
            monkeypatch.setattr(context,'new_page',offline_page)
            return context
        monkeypatch.setattr(browser,'new_context',isolated)
        return browser
    monkeypatch.setattr(browser_runtime,'launch_browser',launch)
    result=module.capture_with_browser('https://www.douyin.com/video/901','901')
    assert result['items'][0]['id']=='901' and result['items'][0]['likes']==0
    assert result['items'][0]['shares'] is None and result['items'][0]['duration']==45000
    assert result['profile']['sec_uid']=='sec-alpha' and 'do-not-store' not in json.dumps(result)
