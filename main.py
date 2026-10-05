#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音博主数据库 - 桌面 App 启动器"""
import os, socket, sys, threading, time, urllib.request
from pathlib import Path

if '--worker-job' in sys.argv:
    # The packaged collector uses its own runtime and its own extraction lifetime.
    try:task_id=sys.argv[sys.argv.index('--worker-job')+1]
    except IndexError:raise SystemExit(2)
    code_root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
    sys.path.insert(0,str(code_root/'core'))
    sys.path.insert(0,str(code_root/'work'))
    if sys.stdout is None:sys.stdout=open(os.devnull,'w',encoding='utf-8')
    if sys.stderr is None:sys.stderr=open(os.devnull,'w',encoding='utf-8')
    from job_runner import main as run_worker
    sys.argv=[str(code_root/'core/job_runner.py'),task_id]
    raise SystemExit(run_worker())


def show_startup_error(message):
    try:
        if sys.stderr is not None:
            print(message, file=sys.stderr)
            return
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, "抖音博主数据库启动失败", 0x10)
    except Exception:
        pass


def dependency_help(exc):
    module = getattr(exc, "name", None) or "未识别模块"
    app_dir=Path(__file__).resolve().parent
    requirements=app_dir/'requirements.txt'
    launcher='start.bat' if os.name=='nt' else 'start.command'
    return (f"启动失败：缺少 Python 依赖 {module}。\n"
            f"请双击 {launcher} 自动准备运行环境；也可以运行：python -m pip install -r requirements.txt\n"
            f"依赖清单位置：{requirements}")


try:
    import uvicorn
except ModuleNotFoundError as exc:
    show_startup_error(dependency_help(exc))
    raise SystemExit(2)

try:
    import app
except Exception as exc:
    # The windowed PyInstaller build has no console. Preserve a small, sanitized
    # diagnostic for smoke tests instead of opening an unhandled-exception box.
    if '--smoke-test' not in sys.argv:
        if isinstance(exc, ModuleNotFoundError):
            show_startup_error(dependency_help(exc))
        elif isinstance(exc,OSError):
            data_path=(os.getenv('DYDB_HOME') or os.getenv('DOUYIN_BLOG_DB_HOME'))
            if not data_path:
                data_path=str(Path(os.getenv('LOCALAPPDATA') or (Path.home()/'AppData'/'Local'))/'DouyinBlogDB')
            show_startup_error(f"无法创建或访问应用数据目录：{data_path}\n请检查目录权限和磁盘空间，或将 DYDB_HOME 设置为一个可写目录后重新启动。")
        else:
            show_startup_error(f"启动失败：{type(exc).__name__}。请检查项目数据目录权限和配置文件。")
        raise SystemExit(2)
    try:
        report = Path(os.getenv('DYDB_SMOKE_REPORT') or (Path(sys.executable).parent/'smoke_result.json'))
        report.parent.mkdir(parents=True,exist_ok=True)
        import json,traceback
        report.write_text(json.dumps({'ready':False,'phase':'import','error_type':type(exc).__name__,
                                      'traceback':traceback.format_exc()},ensure_ascii=False,indent=2),encoding='utf-8')
    except Exception:
        pass
    raise SystemExit(1)

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def icon_path():
    if getattr(sys, "frozen", False):
        return str(Path(getattr(sys, "_MEIPASS", ".")) / "icon.ico")
    return str(Path(__file__).resolve().parent / "icon.ico")


def start_desktop_window(webview, url, icon):
    try:
        webview.create_window(
            "抖音博主数据库",
            url,
            width=1280, height=860, min_size=(1024, 700),
        )
        webview.start(icon=icon)
        return True
    except Exception as exc:
        message = "桌面窗口启动失败。"
        if os.name == "nt":
            message += ("\n请确认已安装 Microsoft Edge WebView2 Runtime；Windows 11 和多数已更新的 Windows 10 已自带。"
                        "\n官方说明：https://developer.microsoft.com/microsoft-edge/webview2/")
        else:
            message += "\n请检查 macOS WebKit 组件、桌面权限和当前 Python 环境。"
        message += f"\n错误类型：{type(exc).__name__}。"
        show_startup_error(message)
        return False

def wait_server_ready(port, timeout=120):
    url = f"http://127.0.0.1:{port}/"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.2)
    return False

def main():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    print("=" * 52)
    print(" DouyinBlogDB - Local creator and video library")
    print(" Personal, non-commercial license. Your collection stays on this device.")
    print("=" * 52)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    port = free_port()
    threading.Thread(
        target=lambda: uvicorn.run(app.app, host="127.0.0.1", port=port,
                                   log_level="warning", log_config=None),
        daemon=True,
    ).start()
    ready=wait_server_ready(port,timeout=20 if '--smoke-test' in sys.argv else 120)
    if '--smoke-test' in sys.argv:
        import json
        code_root=Path(app.CODE_ROOT)
        result={'ready':ready,'version':app.__version__,'data_dir':str(app.DATA_DIR),'code_dir':str(code_root),
                'runtime_resources':{
                    'public_job_runner':(Path(app.CODE_CORE_DIR)/'job_runner.py').is_file(),
                    'video_pipeline':(code_root/'work'/'pipeline.py').is_file(),
                },'statuses':{}}
        try:
            if ready:
                for path in ('/','/api/bloggers','/api/search?q=AI','/api/metrics/imports','/api/metrics?limit=10','/api/metrics/export.csv?limit=10','/api/settings','/api/cookies/status','/api/pipeline/status'):
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}'+path,timeout=10) as response:result['statuses'][path]=response.status
                if not app._cookie_status('douyin')['exists']:
                    # Exercise the bundled collection service without starting a real capture.
                    request=urllib.request.Request(f'http://127.0.0.1:{port}/api/collection',
                        data=json.dumps({'url':'https://www.douyin.com/user/smoke-test'}).encode(),
                        headers={'Content-Type':'application/json'},method='POST')
                    try:
                        with urllib.request.urlopen(request,timeout=10) as response:
                            result['collection_start']={'status':response.status}
                    except urllib.error.HTTPError as response:
                        result['collection_start']={'status':response.code,**json.loads(response.read().decode('utf-8'))}
                    request=urllib.request.Request(f'http://127.0.0.1:{port}/api/collection/videos',
                        data=json.dumps({'urls':'https://www.douyin.com/video/901'}).encode(),
                        headers={'Content-Type':'application/json'},method='POST')
                    try:
                        with urllib.request.urlopen(request,timeout=10) as response:
                            result['video_collection_start']={'status':response.status}
                    except urllib.error.HTTPError as response:
                        result['video_collection_start']={'status':response.code,**json.loads(response.read().decode('utf-8'))}
                else:
                    result['collection_start']={'skipped':'已有登录状态，诊断不自动发起真实采集'}
        except Exception as exc:
            result.update({'error_type':type(exc).__name__,'error':str(exc)[:300]})
        output=Path(os.getenv('DYDB_SMOKE_REPORT',str(app.DATA_DIR/'smoke_result.json')))
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return
    if not ready:raise RuntimeError('本地服务启动失败，请检查数据目录和依赖')
    try:
        import webview
    except ModuleNotFoundError as exc:
        show_startup_error(dependency_help(exc))
        raise SystemExit(2)
    if not start_desktop_window(webview, f"http://127.0.0.1:{port}", icon_path()):
        raise SystemExit(2)

if __name__ == "__main__":
    main()
