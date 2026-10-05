import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
from test_collection import collection_client,cookies
from test_video_collection import videos,metadata


def test_single_and_batch_video_screen_imports_and_shows_each_result(collection_client,monkeypatch):
    root,client=collection_client;cookies(root);module=videos(root,monkeypatch)
    unavailable=[True]
    def capture(url):
        if url.endswith('/903') and unavailable[0]:raise module.CollectionError('该视频暂不可访问')
        return metadata(url.rsplit('/',1)[-1])
    monkeypatch.setattr(module,'capture_video',capture)
    html=(Path(__file__).resolve().parents[1]/'static/index.html').read_text(encoding='utf-8')
    processed=[]
    def respond(route):
        request=route.request;parsed=urlsplit(request.url)
        if parsed.path=='/':route.fulfill(status=200,content_type='text/html',body=html);return
        if parsed.path=='/api/version':route.fulfill(status=200,content_type='application/json',body='{"current":"1.1.0","latest":"1.1.0"}');return
        if parsed.path.startswith('/api/collection/jobs/'):
            tid=parsed.path.rsplit('/',1)[-1]
            if tid not in processed:module.run(tid);processed.append(tid)
        response=client.request(request.method,parsed.path,content=request.post_data_buffer,
                                headers={'Content-Type':request.headers.get('content-type','application/json')})
        route.fulfill(status=response.status_code,content_type=response.headers.get('content-type','application/json'),body=response.content)
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1280,'height':900});page.route('**/*',respond)
        page.goto('http://127.0.0.1:8321/')
        page.get_by_role('button',name='采集视频',exact=True).click(timeout=2500)
        page.get_by_label('视频链接').fill('https://www.douyin.com/video/901\nhttps://www.douyin.com/video/903')
        page.get_by_role('button',name='开始采集视频',exact=True).click()
        page.get_by_text('成功1项，失败1项',exact=False).wait_for(timeout=5000)
        assert page.get_by_role('button',name='重试失败项',exact=True).is_enabled()
        assert page.get_by_role('link',name='查看作者作品',exact=True).count()==1
        assert sum(row['video_count'] for row in client.get('/api/bloggers').json())==1
        page.set_viewport_size({'width':375,'height':900})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        unavailable[0]=False
        page.get_by_role('button',name='重试失败项',exact=True).click()
        page.get_by_text('成功1项，失败0项',exact=False).wait_for(timeout=5000)
        assert sum(row['video_count'] for row in client.get('/api/bloggers').json())==2
        browser.close()


def test_late_request_cannot_replace_newer_batch_and_login_keeps_retry():
    html=(Path(__file__).resolve().parents[1]/'static/index.html').read_text(encoding='utf-8')
    code=html[html.index('let videoCollectionTimer='):html.index('function localDate()')]
    setup='''const nodes=new Map(),storage=new Map(),posts=[],responses=new Map();
      document.body.innerHTML='<main id="main"></main><div id="lastUpdated"></div>';
      const $=s=>document.querySelector(s),esc=s=>String(s??''),setStatus=()=>{},toast=()=>{};
      const localStorage={getItem:k=>storage.get(k)||'',setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)};
      const location={hash:'#/collect-videos'};
      async function api(path,options={}){if(options.method==='POST')return new Promise(resolve=>posts.push(resolve));if(path==='/bloggers')return [];return responses.get(path.split('/').pop());}
    '''
    scenario='''renderVideoCollection();
      const first=startVideoRequest('/collection/videos',{urls:'901'});
      location.hash='#/settings';location.hash='#/collect-videos';renderVideoCollection();
      const second=startVideoRequest('/collection/videos',{urls:'903'});
      responses.set('B',{kind:'video_collect',status:'partial',rows:[{index:1,url:'https://www.douyin.com/video/903',status:'failed',message:'login expired'}],summary:{},message:'newer batch B'});
      responses.set('A',{kind:'video_collect',status:'done',rows:[],summary:{},message:'older batch A'});
      posts[1]({task_id:'B'});await second;await Promise.resolve();
      posts[0]({task_id:'A'});await first;await Promise.resolve();
      const raceSafe=localStorage.getItem('dydb-video-collection-task')==='B'&&$('#videoStatus').textContent.includes('newer batch B');
      const login=startVideoRequest('/collection/login');
      responses.set('login',{kind:'douyin_login',status:'done',rows:[],message:'login saved'});
      posts[2]({task_id:'login'});await login;await Promise.resolve();await Promise.resolve();
      return {raceSafe,retryKept:!!$('#retryVideoCollection'),batchKept:localStorage.getItem('dydb-video-collection-task')==='B',loginCleared:!localStorage.getItem('dydb-video-login-task')};
    '''
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page()
        result=page.evaluate('async()=>{'+setup+code+scenario+'}')
        assert result=={'raceSafe':True,'retryKept':True,'batchKept':True,'loginCleared':True}
        browser.close()
