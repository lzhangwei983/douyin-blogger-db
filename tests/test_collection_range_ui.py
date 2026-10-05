import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
from test_collection import collection_client, cookies, service


def test_custom_range_form_submits_count_and_dates(collection_client, monkeypatch, tmp_path):
    root,client=collection_client
    cookies(root);svc=service(root,monkeypatch)
    html=(Path(__file__).resolve().parents[1]/'static/index.html').read_text(encoding='utf-8')
    submitted=[]
    def respond(route):
        request=route.request;url=urlsplit(request.url)
        if url.path=='/':
            route.fulfill(status=200,content_type='text/html',body=html);return
        if url.path=='/api/version':
            route.fulfill(status=200,content_type='application/json',body='{"current":"1.1.2","latest":"1.1.2"}');return
        if url.path=='/api/collection':submitted.append(json.loads(request.post_data))
        response=client.request(request.method,url.path,content=request.post_data_buffer,
                                headers={'Content-Type':request.headers.get('content-type','application/json')})
        route.fulfill(status=response.status_code,content_type='application/json',body=response.content)
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1280,'height':900});page.route('**/*',respond)
        page.goto('http://127.0.0.1:8321/#/collect')
        page.get_by_label('博主主页链接').fill('https://www.douyin.com/user/sec-test')
        page.get_by_label('采集条数').fill('37',timeout=2500)
        page.get_by_label('开始日期').fill('2026-09-01')
        page.get_by_label('结束日期').fill('2026-10-04')
        folder=tmp_path/'custom-range'/'screenshots';folder.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(folder/'desktop.png'),full_page=True)
        page.set_viewport_size({'width':375,'height':812})
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        page.screenshot(path=str(folder/'mobile.png'),full_page=True)
        page.get_by_role('button',name='开始采集',exact=True).click()
        page.locator('#collectionStatus').get_by_text('正在采集',exact=True).wait_for()
        assert submitted==[{'url':'https://www.douyin.com/user/sec-test','limit':37,'date_from':'2026-09-01','date_to':'2026-10-04'}]
        task=svc.read_json(svc.record_path(__import__('task_store').active('collect')[0]['id']))
        assert task['limit']==37 and task['date_to']=='2026-10-04'
        browser.close()
