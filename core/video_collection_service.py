"""Collect exactly the submitted works; each row has its own result and transaction."""
import hashlib
import json
import os
import re
import time
import urllib.request
from urllib.parse import urlsplit, unquote, parse_qs

import collection_service as collection
from fetch_user_videos import CollectionError
from storage import read_json, write_json, resource_lock
from task_store import active, create, get, update

HOSTS=collection.PROFILE_HOSTS | {'v.douyin.com'}


def normalize_video_url(raw):
    if not isinstance(raw,str) or len(raw)>2000:
        raise CollectionError('视频链接格式无效')
    try:
        parsed=urlsplit(raw.strip().rstrip('，。！；）)]}'))
        if parsed.scheme not in ('http','https') or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None,80,443):
            raise ValueError()
        path=unquote(parsed.path)
        if parsed.hostname=='v.douyin.com' and re.fullmatch(r'/[A-Za-z0-9]{3,100}/?',path):
            return 'https://v.douyin.com/'+path.strip('/')+'/'
        match=re.fullmatch(r'/(?:share/)?(?:video|note)/(\d{1,30})/?',path)
        if match:return 'https://www.douyin.com/video/'+match[1]
    except ValueError:pass
    raise CollectionError('请使用抖音视频链接或作品分享短链，博主主页和其他网站不支持')


def parse_links(text):
    if not isinstance(text,str) or len(text)>50000:
        raise CollectionError('请粘贴单条或多条视频链接，内容不能超过5万字')
    rows=[];seen={}
    for line in text.splitlines():
        if not line.strip():continue
        found=re.findall(r'https?://[^\s<>"\u3000]+',line)
        for candidate in found or [None]:
            index=len(rows)+1
            entry={'index':index,'url':None,'status':'invalid','message':'这一项没有有效的抖音视频链接'}
            if candidate:
                try:
                    url=normalize_video_url(candidate)
                    entry.update(url=url,status='queued',message='等待采集')
                    if url in seen:entry.update(status='duplicate',message=f'重复链接，已在第{seen[url]}项处理')
                    else:seen[url]=index
                except CollectionError as error:entry['message']=str(error)
            rows.append(entry)
            if len(rows)>100:raise CollectionError('单批最多100个视频链接，请拆成多批采集')
    if not any(row['status']=='queued' for row in rows):
        raise CollectionError('没有找到有效的抖音视频链接，请粘贴作品链接，每行一条')
    return rows


def summary(rows):
    return {'total':len(rows),'success':sum(row['status']=='done' for row in rows),
            'failed':sum(row['status']=='failed' for row in rows),
            'duplicate':sum(row['status']=='duplicate' for row in rows),
            'invalid':sum(row['status']=='invalid' for row in rows),
            'finished':sum(row['status'] not in ('queued','running') for row in rows)}


def start(text):
    rows=parse_links(text)
    collection.cookie_file()
    key=hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    with resource_lock('video-start-'+key[:20]):
        for old in active('video_collect',key):
            if (not old['pid'] and time.time()-old['updated']<30) or (old['pid'] and collection.worker_alive(old['pid'])):
                return {'ok':True,'task_id':old['id'],'status':old['status'],'reused':True}
        task,fresh=create('video_collect',key)
        if fresh:
            try:
                write_json(collection.record_path(task['id']),{'phase':'queued','rows':rows,'summary':summary(rows)})
                update(task['id'],pid=collection.spawn_worker(task['id']),message=f'已建立视频采集任务，共{len(rows)}项')
            except Exception:
                update(task['id'],status='failed',message='视频任务启动失败，请检查应用环境和数据目录权限')
                raise CollectionError('视频任务启动失败，请检查应用环境和数据目录权限') from None
        return {'ok':True,'task_id':task['id'],'status':task['status'],'reused':not fresh}


def status(tid):
    task=get(tid)
    if not task or task['kind']!='video_collect':raise FileNotFoundError('视频采集任务不存在')
    if task['status'] in ('queued','running') and time.time()-task['updated']>30:
        if not task['pid'] or not collection.worker_alive(task['pid']):
            update(tid,status='failed',message='视频采集任务已中断，可重试未完成的链接')
            task=get(tid)
    record=read_json(collection.record_path(tid),{}) or {}
    return {**task,'phase':record.get('phase',''),'rows':record.get('rows',[]),'summary':summary(record.get('rows',[]))}


def retry(tid):
    current=status(tid)
    if current['status'] in ('queued','running'):raise CollectionError('该任务仍在运行，请等待结束后重试')
    links=[row['url'] for row in current['rows'] if row['status'] in ('failed','queued','running') and row.get('url')]
    if not links:raise CollectionError('没有需要重试的视频链接')
    return start('\n'.join(links))


def resolve_video(url):
    url=normalize_video_url(url)
    if urlsplit(url).hostname!='v.douyin.com':return url
    class Located(Exception):
        def __init__(self,url):self.url=url
    class SafeRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            destination=normalize_video_url(newurl)
            if urlsplit(destination).hostname!='v.douyin.com':raise Located(destination)
            return super().redirect_request(req,fp,code,msg,headers,destination)
    try:
        request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.build_opener(SafeRedirect()).open(request,timeout=25) as response:
            destination=normalize_video_url(response.geturl())
        if urlsplit(destination).hostname=='v.douyin.com':raise CollectionError('短链没有返回视频，请改用完整作品链接')
        return destination
    except Located as found:return found.url
    except CollectionError:raise
    except Exception:raise CollectionError('作品短链解析失败，请尝试粘贴完整视频链接') from None


def parse_detail(detail,expected_id):
    if not isinstance(detail,dict) or str(detail.get('aweme_id') or '')!=expected_id:
        raise CollectionError('平台返回内容与视频链接不一致，本次未入库')
    author=detail.get('author') or {}
    if not isinstance(author,dict):raise CollectionError('作品作者数据格式无效，本次未入库')
    uid=author.get('sec_uid')
    if not isinstance(uid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{3,200}',uid):
        raise CollectionError('平台未返回可识别的作者身份，本次未入库')
    stats=detail.get('statistics') or {}
    if not isinstance(stats,dict):raise CollectionError('作品互动数据格式无效，本次未入库')
    def count(key):
        value=stats.get(key)
        return value if type(value) is int and value>=0 else None
    image_list=detail.get('images') or []
    if not isinstance(image_list,list):raise CollectionError('作品图片数据格式无效，本次未入库')
    images=[link for image in image_list if isinstance(image,dict) for link in image.get('url_list') or []
            if isinstance(link,str) and link.startswith(('http://','https://'))]
    video=detail.get('video') or {}
    if not isinstance(video,dict):raise CollectionError('作品时长数据格式无效，本次未入库')
    duration=video.get('duration')
    duration=duration if type(duration) is int and duration>=0 else None
    return {'profile':{'sec_uid':uid,'nickname':author.get('nickname'),'unique_id':author.get('unique_id'),
                       'signature':author.get('signature')},
            'items':[{'id':expected_id,'url':'https://www.douyin.com/video/'+expected_id,'desc':detail.get('desc') or '',
                      'create_time':detail.get('create_time'),'duration':duration,'likes':count('digg_count'),
                      'comments':count('comment_count'),'shares':count('share_count'),'collects':count('collect_count'),
                      'is_image':bool(image_list),'images':images}]}


def capture_video(url):
    video_id=url.rsplit('/',1)[-1]
    from cookie_scope import cookie_file_header
    api='https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id='+video_id
    header=cookie_file_header(collection.cookie_file(),api)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):
            raise CollectionError('详情接口发生跳转，改用浏览器读取作品')
    try:
        request=urllib.request.Request(api,headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36',
                                                   'Referer':url,'Cookie':header})
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=25) as response:payload=json.loads(response.read().decode('utf-8'))
        if isinstance(payload,dict) and payload.get('status_code',0)==0 and payload.get('aweme_detail'):
            return parse_detail(payload['aweme_detail'],video_id)
    except (OSError,ValueError,TypeError,CollectionError):pass
    return capture_with_browser(url,video_id)


def capture_with_browser(url,video_id):
    try:
        from playwright.sync_api import sync_playwright
        from browser_runtime import launch_browser
        from cookie_scope import load_cookie_jar
    except ImportError:raise CollectionError('浏览器采集环境未准备好，请检查浏览器安装后重试') from None
    jar=load_cookie_jar(collection.cookie_file())
    cookies=[{'name':c.name,'value':c.value,'domain':c.domain,'path':c.path,'secure':c.secure,
              'httpOnly':c.has_nonstandard_attr('HttpOnly'),'expires':c.expires if c.expires else -1}
             for c in jar if not c.is_expired() and (c.domain.lstrip('.').lower()=='douyin.com' or c.domain.lower().endswith('.douyin.com'))]
    found=None
    try:
        with sync_playwright() as runtime:
            browser=launch_browser(runtime)
            try:
                context=browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36')
                context.add_cookies(cookies);page=context.new_page()
                def guard(route):
                    request=route.request
                    if request.is_navigation_request() and urlsplit(request.url).hostname not in HOSTS:route.abort()
                    else:route.continue_()
                page.route('**/*',guard)
                def receive(response):
                    nonlocal found
                    parsed=urlsplit(response.url)
                    if parsed.hostname not in collection.PROFILE_HOSTS or '/aweme/v1/web/aweme/detail/' not in parsed.path:return
                    requested=parse_qs(parsed.query).get('aweme_id',[video_id])[0]
                    if requested!=video_id:return
                    try:
                        payload=response.json()
                        if payload.get('status_code',0)==0 and payload.get('aweme_detail'):
                            found=parse_detail(payload['aweme_detail'],video_id)
                    except (ValueError,TypeError,CollectionError,AttributeError):return
                page.on('response',receive)
                page.goto(url,wait_until='domcontentloaded',timeout=35000)
                for _ in range(12):
                    if found:break
                    detail=page.evaluate('''id => {
                      const seen=new WeakSet();let visited=0;
                      function scan(value,depth){
                        if(!value||typeof value!=='object'||depth>8||visited++>5000||seen.has(value))return null;
                        seen.add(value);
                        if(String(value.aweme_id||'')===id&&value.author&&value.statistics){
                          const a=value.author,s=value.statistics;
                          return {aweme_id:value.aweme_id,desc:value.desc,create_time:value.create_time,
                            author:{sec_uid:a.sec_uid,nickname:a.nickname,unique_id:a.unique_id,signature:a.signature},
                            statistics:{digg_count:s.digg_count,comment_count:s.comment_count,share_count:s.share_count,collect_count:s.collect_count},
                            video:{duration:value.video?.duration},images:value.images||[]};
                        }
                        for(const [key,child] of Object.entries(value)){
                          if(/token|cookie|session|auth|password/i.test(key))continue;
                          const result=scan(child,depth+1);if(result)return result;
                        }return null;
                      }return scan(window._ROUTER_DATA,0);
                    }''',video_id)
                    if detail:
                        try:found=parse_detail(detail,video_id)
                        except CollectionError:pass
                    if not found:page.wait_for_timeout(1000)
                if found:return found
                raise CollectionError('该作品暂未取得详情，可能需要登录验证、已删除或限制访问；未写入数据库')
            finally:browser.close()
    except CollectionError:raise
    except Exception:raise CollectionError('该作品采集失败，请检查浏览器、网络和登录状态后重试') from None


def run(tid):
    job=get(tid)
    if not job or job['kind']!='video_collect':raise CollectionError('视频采集任务不存在')
    record=read_json(collection.record_path(tid))
    rows=record['rows'];seen=set();backed_up=False
    update(tid,status='running',pid=os.getpid(),message='正在逐条采集视频资料')
    try:
        for row in rows:
            if row['status'] not in ('queued','running'):continue
            row.update(status='running',message='正在读取作品详情')
            record.update(phase='collecting',summary=summary(rows));write_json(collection.record_path(tid),record)
            update(tid,message=f"正在处理第{row['index']}/{len(rows)}项")
            try:
                resolved=resolve_video(row['url']);vid=resolved.rsplit('/',1)[-1]
                if vid in seen:
                    row.update(status='duplicate',message='同一作品已在本批次处理');continue
                captured=capture_video(resolved)
                item=captured['items'][0]
                if str(item.get('id'))!=vid:raise CollectionError('作品身份与链接不一致，本次未入库')
                uid=captured['profile'].get('sec_uid')
                profile_url=collection.normalize_url('https://www.douyin.com/user/'+str(uid or ''))
                result=collection.import_items(profile_url,captured,backup=not backed_up)
                backed_up=True;seen.add(vid)
                row.update(status='done',message='已更新已有作品' if result['updated'] else '已入库',
                           video_id=vid,blogger_id=result['blogger_id'],title=item.get('desc') or '无标题',author=result['name'],resolved_url=resolved)
                if result.get('warning'):row['message']+='；'+result['warning']
            except Exception as error:
                if isinstance(error,CollectionError):message=str(error)
                elif isinstance(error,OSError):message='保存失败，请检查本机数据目录权限'
                else:message='作品数据处理失败，本条未完成；其他链接继续处理'
                row.update(status='failed',message=message)
            finally:
                record['summary']=summary(rows);write_json(collection.record_path(tid),record)
        counts=summary(rows)
        final='done' if counts['success'] and not counts['failed'] and not counts['invalid'] else 'partial' if counts['success'] else 'failed'
        record.update(phase='done',summary=counts);write_json(collection.record_path(tid),record)
        update(tid,status=final,message=f"视频采集结束：成功{counts['success']}项，失败{counts['failed']}项，重复{counts['duplicate']}项，无效{counts['invalid']}项")
        return counts if counts['success'] else None
    except Exception:
        update(tid,status='failed',message='批次记录保存失败，已入库的作品仍保留；请检查磁盘和目录权限')
        return None
