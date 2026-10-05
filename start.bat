@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if exist "%~dp0dist\DouyinBlogDB.exe" (
    start "DouyinBlogDB" "%~dp0dist\DouyinBlogDB.exe"
    exit /b 0
)
if exist "%~dp0DouyinBlogDB.exe" (
    start "DouyinBlogDB" "%~dp0DouyinBlogDB.exe"
    exit /b 0
)

where py >nul 2>nul
if not errorlevel 1 (
    set "PY_CMD=py -3"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo 未找到 Python 3.10 或更高版本。请安装 Python 后重新双击 start.bat。
        pause
        exit /b 2
    )
    set "PY_CMD=python"
)

if not exist ".venv\Scripts\python.exe" (
    %PY_CMD% -m venv .venv
    if errorlevel 1 goto setup_failed
)
set "APP_PY=%~dp0.venv\Scripts\python.exe"
"%APP_PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
    echo 当前 Python 版本低于 3.10。请安装 Python 3.10 或更高版本并重新双击。
    pause
    exit /b 2
)

if not exist ".venv\.douyinblogdb-1.1.0-ready" (
    echo 正在准备运行环境，首次运行需要联网下载 Python 依赖和浏览器组件……
    "%APP_PY%" -m pip install -r requirements.txt
    if errorlevel 1 goto setup_failed
    "%APP_PY%" -m playwright install chromium
    if errorlevel 1 goto setup_failed
    type nul > ".venv\.douyinblogdb-1.1.0-ready"
)

"%APP_PY%" main.py
if errorlevel 1 (
    echo 软件已退出。若窗口提示依赖或权限错误，请按提示修复后再次运行。
    pause
)
exit /b 0

:setup_failed
echo 自动准备失败。请检查网络连接，并确认当前目录可写，然后再次运行 start.bat。
pause
exit /b 2
