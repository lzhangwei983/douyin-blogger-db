"""User-triggered profile collection: durable jobs, scoped login, atomic DB import."""
from pathlib import Path
from urllib.parse import urlsplit, unquote, parse_qs
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
import uuid

from _paths import BASE_DIR, APP_STATE_DIR, CODE_CORE_DIR, CODE_WORK_DIR, get_python
from storage import atomic_text, backup_database, read_json, resource_lock, write_json
from task_store import active, create, get, update

WORK_CODE = CODE_WORK_DIR
if str(WORK_CODE) not in sys.path:
    sys.path.insert(0, str(WORK_CODE))
from fetch_user_videos import CollectionError
from collection_range import CollectionRange, publication_day, pagination_flag


class RangeConflictError(CollectionError):
    pass

PROFILE_HOSTS = {'douyin.com', 'www.douyin.com', 'iesdouyin.com', 'www.iesdouyin.com'}


def normalize_url(value):
    if not isinstance(value, str) or len(value) > 2000:
        raise CollectionError('请粘贴有效的抖音博主主页链接')
    match = re.search(r'https?://[^\s<>"\u3000]+', value.strip())
    raw = match.group(0).rstrip('，。！；）)]}') if match else value.strip()
    try:
        url = urlsplit(raw)
        if url.scheme not in ('http', 'https') or url.username or url.password or url.port not in (None, 80, 443):
            raise ValueError()
        host = (url.hostname or '').lower()
        path = unquote(url.path)
        if host in PROFILE_HOSTS:
            m = re.fullmatch(r'/(?:share/)?user/([A-Za-z0-9_-]{3,200})/?', path)
            if m:
                return 'https://www.douyin.com/user/' + m.group(1)
        if host == 'v.douyin.com' and re.fullmatch(r'/[A-Za-z0-9]{3,100}/?', path):
            return 'https://v.douyin.com/' + path.strip('/') + '/'
    except ValueError:
        pass
    raise CollectionError('请使用抖音博主主页链接或主页分享短链；作品链接和其他网站不支持')


def resolve_profile(url):
    url = normalize_url(url)
    if urlsplit(url).hostname != 'v.douyin.com':
        return url
    class ResolvedProfile(Exception):
        def __init__(self, profile):self.profile = profile
    def validated_destination(value):
        parsed = urlsplit(value)
        if parsed.scheme not in ('http','https') or parsed.hostname not in PROFILE_HOSTS | {'v.douyin.com'} or parsed.username or parsed.password or parsed.port not in (None,80,443):
            raise CollectionError('主页短链跳转到不支持的地址，请使用完整抖音主页链接')
        return normalize_url(value)
    class SafeRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            destination = validated_destination(newurl)
            if urlsplit(destination).hostname != 'v.douyin.com':
                raise ResolvedProfile(destination)
            return super().redirect_request(req, fp, code, msg, headers, destination)
    opener = urllib.request.build_opener(SafeRedirect())
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with opener.open(request, timeout=25) as response:
            resolved = validated_destination(response.geturl())
    except ResolvedProfile as profile:
        return profile.profile
    except CollectionError:
        raise
    except Exception:
        raise CollectionError('主页短链解析失败，请粘贴浏览器中的完整博主主页链接') from None
    if urlsplit(resolved).hostname == 'v.douyin.com':
        raise CollectionError('短链没有返回博主主页，请使用完整主页链接')
    return resolved


def cookie_file():
    from cookie_scope import cookie_file_header
    from fetch_user_videos import DOUYIN_POST_COOKIE_URL
    for filename in ('douyin_cookies.json', 'douyin_cookies.txt'):
        path = BASE_DIR / 'work' / filename
        if path.is_file():
            try:
                if cookie_file_header(path, DOUYIN_POST_COOKIE_URL):
                    return path
            except (ValueError, OSError):
                continue
    raise CollectionError('未找到有效的抖音登录状态。请打开设置，上传自己的抖音 Cookie 后重新采集')


def record_path(tid):
    if not re.fullmatch(r'[a-f0-9]{32}', str(tid)):
        raise CollectionError('采集任务编号无效')
    return APP_STATE_DIR / 'collection' / (tid + '.json')


def worker_alive(pid):
    from process_identity import process_matches_pid
    if getattr(sys,'frozen',False):
        import psutil
        try:
            process=psutil.Process(int(pid))
            if '--worker-job' in process.cmdline():
                return (process.is_running() and process.status()!=psutil.STATUS_ZOMBIE
                        and Path(process.exe()).resolve()==Path(sys.executable).resolve())
        except (psutil.Error,ValueError,OSError):return False
    return process_matches_pid(pid, CODE_CORE_DIR / 'job_runner.py')


def spawn_worker(tid):
    env = os.environ.copy()
    env['DYDB_HOME'] = str(BASE_DIR)
    env['PYTHONIOENCODING'] = 'utf-8'
    if getattr(sys,'frozen',False):env['PYINSTALLER_RESET_ENVIRONMENT']='1'
    command = ([sys.executable,'--worker-job',tid] if getattr(sys,'frozen',False)
               else [get_python(),str(CODE_CORE_DIR/'job_runner.py'),tid])
    process = subprocess.Popen(command,
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=0x08000000 if os.name == 'nt' else 0)
    return process.pid


def start(url, limit=50, date_from=None, date_to=None):
    url = normalize_url(url)
    try:options=CollectionRange.parse(limit,date_from,date_to)
    except ValueError as error:raise CollectionError(str(error)) from None
    cookie_file()
    with resource_lock('collect-start-' + hashlib.sha256(url.encode()).hexdigest()[:20]):
        for old in active('collect', url):
            recent = not old['pid'] and time.time() - old['updated'] < 30
            if recent or (old['pid'] and worker_alive(old['pid'])):
                previous=read_json(record_path(old['id']),{}) or {}
                old_range=CollectionRange.parse(previous.get('limit',50),previous.get('date_from'),previous.get('date_to'))
                if old_range!=options:
                    raise RangeConflictError('该博主已有不同采集范围的任务正在执行，请等待完成后再修改范围')
                return {'ok': True, 'task_id': old['id'], 'status': old['status'], 'reused': True}
        task, fresh = create('collect', url)
        if fresh:
            try:
                write_json(record_path(task['id']), {'url': url, **options.payload(), 'collected': 0, 'phase': 'queued'})
                pid = spawn_worker(task['id'])
                update(task['id'], pid=pid, message='采集任务已排队，正在启动')
            except Exception:
                update(task['id'], status='failed', message='采集任务启动失败，请检查 Python 环境和数据目录权限')
                raise CollectionError('采集任务启动失败，请检查 Python 环境和数据目录权限') from None
        return {'ok': True, 'task_id': task['id'], 'status': task['status'], 'reused': not fresh}


def start_login():
    with resource_lock('douyin-login-start'):
        for task in active('douyin_login', 'douyin'):
            if (not task['pid'] and time.time()-task['updated']<30) or (task['pid'] and worker_alive(task['pid'])):
                return {'ok':True,'task_id':task['id'],'status':task['status']}
        task,fresh=create('douyin_login','douyin')
        if fresh:
            try:
                write_json(record_path(task['id']),{'url':'','phase':'login','collected':0})
                update(task['id'],pid=spawn_worker(task['id']),message='正在打开抖音登录窗口，请完成扫码或验证')
            except Exception:
                update(task['id'],status='failed',message='登录窗口启动失败，请检查 Python 和浏览器环境')
                raise CollectionError('登录窗口启动失败，请检查 Python 和浏览器环境') from None
        return {'ok':True,'task_id':task['id'],'status':task['status']}


def save_login_cookies(cookies):
    from _cookie_utils import playwright_to_netscape
    chosen=[]
    for cookie in cookies:
        domain=str(cookie.get('domain') or '').lower().lstrip('.')
        expires=cookie.get('expires',-1)
        if (domain=='douyin.com' or domain.endswith('.douyin.com')) and cookie.get('name') and cookie.get('value') is not None:
            if isinstance(expires,(int,float)) and expires>0 and expires<=time.time():continue
            chosen.append(cookie)
    if not any(c['name'] in ('sessionid','sessionid_ss') and c.get('value') for c in chosen):
        raise CollectionError('尚未完成抖音登录，请在打开的浏览器中完成扫码或验证')
    folder=BASE_DIR/'work'/'.login-cookie-tmp'/uuid.uuid4().hex
    folder.mkdir(parents=True,exist_ok=True)
    pending_json=folder/'cookies.json';pending_txt=folder/'cookies.txt';rollback=folder/'rollback.json'
    destination_json=BASE_DIR/'work/douyin_cookies.json';destination_txt=BASE_DIR/'work/douyin_cookies.txt'
    with resource_lock('cookie-douyin'):
        original=destination_json.read_bytes() if destination_json.exists() else None
        promoted=False
        try:
            atomic_text(pending_json,json.dumps(chosen,ensure_ascii=False,indent=2))
            playwright_to_netscape(chosen,pending_txt)
            os.replace(pending_json,destination_json);promoted=True
            os.replace(pending_txt,destination_txt)
        except OSError:
            if promoted:
                if original is None:destination_json.unlink(missing_ok=True)
                else:
                    rollback.write_bytes(original)
                    os.replace(rollback,destination_json)
            raise
        finally:
            pending_json.unlink(missing_ok=True);pending_txt.unlink(missing_ok=True);rollback.unlink(missing_ok=True)
            folder.rmdir()
        flag=read_json(BASE_DIR/'work/cookie_status.json',{}) or {}
        flag.pop('douyin',None)
        try:write_json(BASE_DIR/'work/cookie_status.json',flag)
        except OSError:pass
    return {'logged_in':True,'cookie_count':len(chosen)}


def run_login(tid):
    update(tid,status='running',pid=os.getpid(),message='请在打开的抖音窗口完成登录；成功后会自动保存')
    try:
        from playwright.sync_api import sync_playwright
        from browser_runtime import launch_browser
        # User explicitly requested a visible login window; collection stays background by default.
        with sync_playwright() as runtime:
            browser=launch_browser(runtime,headless=False)
            try:
                context=browser.new_context();page=context.new_page()
                page.goto('https://www.douyin.com/',wait_until='domcontentloaded',timeout=40000)
                deadline=time.monotonic()+180
                while time.monotonic()<deadline:
                    if not browser.is_connected():raise CollectionError('登录窗口已关闭，未保存新登录状态')
                    snapshot=context.cookies(['https://www.douyin.com/'])
                    if any(c.get('name') in ('sessionid','sessionid_ss') and c.get('value') for c in snapshot):
                        result=save_login_cookies(snapshot)
                        write_json(record_path(tid),{'phase':'done','result':result,'collected':0})
                        update(tid,status='done',message='抖音登录状态已保存，现在可以开始采集')
                        return result
                    page.wait_for_timeout(1000)
                    update(tid,message='等待扫码或登录验证，请保持抖音窗口打开')
                raise CollectionError('登录等待超时，请重新点击登录抖音')
            finally:
                try:browser.close()
                except Exception:pass
    except Exception as error:
        message=str(error) if isinstance(error,CollectionError) else '登录未完成，请检查浏览器安装、网络和数据目录权限后重试'
        update(tid,status='failed',message=message)
        return None


def status(tid):
    task = get(tid)
    if not task or task['kind'] not in ('collect','douyin_login'):
        raise FileNotFoundError('采集任务不存在')
    if task['status'] in ('queued', 'running'):
        stale = time.time() - task['updated'] > 30
        if stale and (not task['pid'] or not worker_alive(task['pid'])):
            update(tid, status='failed', message='采集任务已中断，请重新开始')
            task = get(tid)
    record = read_json(record_path(tid), {}) or {}
    selected_range=CollectionRange.parse(record.get('limit',50),record.get('date_from'),record.get('date_to')).payload()
    return {**task, 'url': record.get('url', task['date']), 'phase': record.get('phase', ''),
            'collected': record.get('collected', 0), 'result': record.get('result'),
            'range':selected_range,
            'action': 'settings' if task['status'] == 'failed' and '登录' in task['message'] else 'retry'}


def capture_profile(url, limit, progress, date_from=None, date_to=None):
    options=CollectionRange.parse(limit,date_from,date_to)
    from fetch_user_videos import fetch, cookies_from_file
    try:
        result = fetch(url, 3, cookies_from_file(cookie_file()), all_mode=True,
                       max_items=limit or None, on_progress=progress,date_from=date_from,date_to=date_to)
        if result and (result['items'] or (options.has_dates and result.get('range_complete') and result.get('scanned'))):
            return result
    except CollectionError:
        pass
    progress(0)
    return capture_with_browser(url, limit, progress,date_from,date_to)


def capture_with_browser(url, limit, progress, date_from=None, date_to=None):
    options=CollectionRange.parse(limit,date_from,date_to)
    try:
        from playwright.sync_api import sync_playwright
        from browser_runtime import launch_browser
        from _cookie_utils import load_playwright_cookies
    except ImportError:
        raise CollectionError('浏览器采集依赖未安装，请通过启动器准备采集环境后重试') from None
    source = cookie_file()  # Recheck login before the slower browser fallback.
    if source.suffix == '.json':
        cookies = load_playwright_cookies(source)
    else:
        from cookie_scope import load_cookie_jar
        cookies = [{'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path,
                    'secure': c.secure, 'httpOnly': c.has_nonstandard_attr('HttpOnly'),
                    'expires': c.expires if c.expires is not None else -1}
                   for c in load_cookie_jar(source) if not c.is_expired()]
    cookies = [c for c in cookies if c.get('domain', '').lstrip('.').lower() == 'douyin.com'
               or c.get('domain', '').lower().endswith('.douyin.com')]
    collected, seen = [], set()
    profile = {'sec_uid': url.rsplit('/', 1)[-1]}
    finished = False
    try:
        with sync_playwright() as runtime:
            browser = launch_browser(runtime)
            try:
                context = browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36')
                context.add_cookies(cookies)
                page = context.new_page()
                def receive(response):
                    nonlocal finished
                    parsed = urlsplit(response.url)
                    if parsed.hostname not in PROFILE_HOSTS or '/aweme/v1/web/aweme/post/' not in parsed.path:
                        return
                    requested_uid = parse_qs(parsed.query).get('sec_user_id', [profile['sec_uid']])[0]
                    if requested_uid != profile['sec_uid']:
                        return
                    try:
                        payload = response.json()
                        if payload.get('status_code') != 0:
                            return
                        if options.has_dates:
                            try:pagination_flag(payload.get('has_more'))
                            except ValueError:return
                        for item in payload.get('aweme_list') or []:
                            author = item.get('author') or {}
                            if author.get('sec_uid') and author['sec_uid'] != profile['sec_uid']:
                                continue
                            vid = str(item.get('aweme_id') or '')
                            if not vid.isdigit() or vid in seen:
                                continue
                            if limit and not options.has_dates and len(collected) >= limit:
                                break
                            seen.add(vid)
                            profile.update({k: author.get(k) or profile.get(k) for k in ('nickname', 'unique_id', 'signature')})
                            if not options.matches(item.get('create_time')):continue
                            stats = item.get('statistics') or {}
                            images = item.get('images') or []
                            collected.append({'id': vid, 'url': 'https://www.douyin.com/' + ('note/' if images else 'video/') + vid,
                                              'desc': item.get('desc') or '', 'likes': stats.get('digg_count'),
                                              'comments': stats.get('comment_count'), 'shares': stats.get('share_count'),
                                              'collects': stats.get('collect_count'), 'duration': (item.get('video') or {}).get('duration'),
                                              'create_time': item.get('create_time'), 'is_image': bool(images),
                                              'images': [link for image in images for link in image.get('url_list') or [] if link.startswith(('http://', 'https://'))]})
                        progress(min(len(collected),limit) if limit else len(collected))
                        finished = not payload.get('has_more') or bool(limit and not options.has_dates and len(collected) >= limit)
                    except (ValueError, TypeError, AttributeError):
                        return
                page.on('response', receive)
                page.goto(url, wait_until='domcontentloaded', timeout=40000)
                deadline = time.monotonic() + 120
                idle = 0
                while not finished and time.monotonic() < deadline:
                    before = len(seen)
                    page.mouse.wheel(0, 2200)
                    page.wait_for_timeout(1800)
                    idle = idle + 1 if len(seen) == before else 0
                    if idle >= 12:
                        break
                if (not collected and not (options.has_dates and seen)) or not finished:
                    raise CollectionError('未能完整取得作品，可能需要登录验证或平台限流。请在设置中检查登录状态后重试；原有数据未改变')
                reached=bool(limit and len(collected)>=limit)
                return {'profile': profile, 'items': options.select(collected), 'limit_reached': reached,
                        'range_complete':bool(options.has_dates),'scanned':len(seen)}
            finally:
                browser.close()
    except CollectionError:
        raise
    except Exception:
        raise CollectionError('浏览器采集未完成，请检查浏览器是否安装、网络连接和抖音登录状态后重试') from None


def import_items(url, captured, aliases=(), backup=True):
    from datetime import datetime
    profile = captured.get('profile') or {}
    uid = url.rsplit('/', 1)[-1]
    if profile.get('sec_uid') and profile['sec_uid'] != uid:
        raise CollectionError('平台返回的博主与提交主页不一致，本次没有写入数据库')
    slug = 'dy-' + hashlib.sha256(uid.encode()).hexdigest()[:16]
    items = captured.get('items') or []
    if not items:
        raise CollectionError('平台没有返回公开作品，请检查登录状态或博主主页；原有数据未改变')
    for item in items:
        if not isinstance(item, dict) or not str(item.get('id') or '').isdigit():
            raise CollectionError('作品数据不完整，本次没有写入数据库')
    unique = {}
    for item in items:
        unique.setdefault(str(item['id']), item)
    items = list(unique.values())
    database = BASE_DIR / 'douyin_blog.db'
    with resource_lock('collection-import'):
        if backup:backup_database(database, 'before-collection')
        con = sqlite3.connect(database, timeout=5)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        try:
            con.execute('BEGIN IMMEDIATE')
            blogger = None
            bloggers = list(con.execute("SELECT * FROM bloggers WHERE platform='抖音'"))
            identities = {url, *aliases}
            for row in bloggers:
                try:
                    same = normalize_url(row['homepage_url'] or '') in identities
                except CollectionError:
                    same = False
                if row['slug'] == slug or same:
                    blogger = row
                    break
            if blogger is None and profile.get('unique_id'):
                matches = [row for row in bloggers if row['douyin_id'] == profile['unique_id']]
                if len(matches) == 1:blogger = matches[0]
            if blogger is None:
                owners = set()
                for item in items:
                    row = con.execute('SELECT blogger_id FROM videos WHERE video_id=?', (str(item['id']),)).fetchone()
                    if row:owners.add(row['blogger_id'])
                if len(owners) == 1:
                    for row in bloggers:
                        if row['id'] not in owners:continue
                        try:
                            legacy = normalize_url(row['homepage_url'] or '')
                            is_share = urlsplit(legacy).hostname == 'v.douyin.com'
                        except CollectionError:
                            is_share = not row['homepage_url']
                        if is_share:
                            blogger = row
                            break
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            name = profile.get('nickname') or '抖音博主 ' + uid[:8]
            if blogger:
                bid = blogger['id']
                manual = set(json.loads(blogger['manual_fields'] or '[]'))
                changes = {'name': name, 'homepage_url': url, 'bio': profile.get('signature'), 'douyin_id': profile.get('unique_id'), 'updated_at': now}
                changes = {k: v for k, v in changes.items() if k not in manual and v is not None}
                con.execute('UPDATE bloggers SET ' + ','.join(k + '=?' for k in changes) + ' WHERE id=?', [*changes.values(), bid])
            else:
                bid = con.execute('INSERT INTO bloggers(slug,name,platform,douyin_id,homepage_url,bio,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                                  (slug, name, '抖音', profile.get('unique_id'), url, profile.get('signature'), now, now)).lastrowid
            added = refreshed = skipped = 0
            sequence = con.execute('SELECT COALESCE(MAX(seq),0) FROM videos WHERE blogger_id=?', (bid,)).fetchone()[0]
            for item in items:
                vid = str(item['id'])
                old = con.execute('SELECT * FROM videos WHERE video_id=?', (vid,)).fetchone()
                if old and old['blogger_id'] != bid:
                    skipped += 1
                    continue
                kind = '图文' if item.get('is_image') else '视频'
                published_day=publication_day(item.get('create_time'))
                published=published_day.strftime('%Y%m%d') if published_day else None
                fields = {'url': 'https://www.douyin.com/' + ('note/' if kind == '图文' else 'video/') + vid,
                          'kind': kind, 'title': item.get('desc') or '', 'upload_date': published,
                          'duration': int(item['duration']) // 1000 if item.get('duration') is not None else None,
                          'like_count': item.get('likes'), 'comment_count': item.get('comments'),
                          'repost_count': item.get('shares'), 'save_count': item.get('collects'),
                          'images': json.dumps(item.get('images') or [], ensure_ascii=False), 'updated_at': now}
                if old:
                    manual = set(json.loads(old['manual_fields'] or '[]'))
                    fields = {k: v for k, v in fields.items() if k not in manual and v is not None}
                    con.execute('UPDATE videos SET ' + ','.join(k + '=?' for k in fields) + ' WHERE id=?', [*fields.values(), old['id']])
                    refreshed += 1
                else:
                    sequence += 1
                    fields.update(blogger_id=bid, seq=sequence, video_id=vid, status='ok', created_at=now)
                    con.execute('INSERT INTO videos(' + ','.join(fields) + ') VALUES(' + ','.join('?' for _ in fields) + ')', list(fields.values()))
                    added += 1
            if not added and not refreshed:
                raise CollectionError('返回作品已关联到其他博主，请核对主页链接；本次没有写入新博主')
            con.commit()
            name = con.execute('SELECT name FROM bloggers WHERE id=?', (bid,)).fetchone()[0]
            rows = con.execute('SELECT * FROM videos WHERE blogger_id=? ORDER BY seq', (bid,)).fetchall()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
        result = {'blogger_id': bid, 'name': name, 'collected': len(items), 'added': added, 'updated': refreshed, 'skipped': skipped,
                  'limit_reached': bool(captured.get('limit_reached'))}
        try:
            output = io.StringIO(newline='')
            writer = csv.writer(output, delimiter='\t', lineterminator='\n')
            writer.writerow(['序号','类型','链接','标题','发布日期','点赞','评论','时长','转发','收藏','图片'])
            for row in rows:
                writer.writerow([row['seq'], row['kind'], row['url'], row['title'], row['upload_date'], row['like_count'], row['comment_count'], row['duration'], row['repost_count'], row['save_count'], ';'.join(json.loads(row['images'] or '[]'))])
            actual_slug = blogger['slug'] if blogger else slug
            atomic_text(BASE_DIR / 'work' / (actual_slug + '_videos.tsv'), output.getvalue())
        except (OSError, ValueError):
            result['warning'] = '数据已入库，但转写清单缓存未能更新，请检查数据目录权限'
        return result


def run(tid):
    task = get(tid)
    if not task or task['kind'] != 'collect':
        raise CollectionError('采集任务不存在')
    record = read_json(record_path(tid))
    update(tid, status='running', pid=os.getpid(), message='正在解析博主主页')
    try:
        options=CollectionRange.parse(record.get('limit',50),record.get('date_from'),record.get('date_to'))
        url = resolve_profile(record['url'])
        with resource_lock('collect-profile-' + hashlib.sha256(url.encode()).hexdigest()[:20], timeout=120):
            def progress(count):
                record.update(phase='collecting', collected=count)
                write_json(record_path(tid), record)
                message=(f'已匹配 {count} 条候选，正在翻页核实日期范围' if options.has_dates else f'正在采集公开作品，已取得 {count} 条')
                update(tid, message=message)
            if options.has_dates:
                captured=capture_profile(url,options.limit,progress,record.get('date_from'),record.get('date_to'))
            else:captured=capture_profile(url,options.limit,progress)
            if options.has_dates and not captured.get('items') and captured.get('range_complete') and captured.get('scanned'):
                result={'no_matches':True,'collected':0,'added':0,'updated':0,'blogger_id':None,**options.payload()}
                record.update(phase='done',result=result,collected=0)
                write_json(record_path(tid),record)
                update(tid,status='done',message='指定日期范围内未找到可收录的公开作品，数据库没有改变')
                return result
            update(tid, message='采集结束，正在安全合并到数据库')
            result = import_items(url, captured, aliases=(record['url'],))
            record.update(phase='done', result=result, collected=result['collected'])
            write_json(record_path(tid), record)
            update(tid, status='done', message=f"采集完成：{result['name']} · 新增 {result['added']} 条，更新 {result['updated']} 条")
            return result
    except Exception as error:
        if isinstance(error, CollectionError):
            message = str(error)
        elif isinstance(error, sqlite3.OperationalError):
            message = '数据库正在使用或不可写，请稍后重试并检查数据目录权限'
        elif isinstance(error, OSError):
            message = '采集数据无法保存，请检查数据目录权限和磁盘空间'
        else:
            message = '采集数据处理失败，请重试；已有作品和人工笔记仍保留'
        update(tid, status='failed', message=message)
        return None
