"""Background browser collection; visible mode is an explicit user choice."""
from _paths import read_public_config


class BrowserUnavailableError(RuntimeError):
    pass

def launch_options(**extra):
    cfg=read_public_config()
    mode=(cfg.get('browser') or {}).get('mode','background')
    import os
    if os.getenv('DYDB_BROWSER_MODE'):mode=os.environ['DYDB_BROWSER_MODE']
    return {'headless':mode!='visible',**extra}

def launch_browser(playwright, **extra):
    # Never fall back to a visible window after a failed background attempt.
    opts=launch_options(args=['--disable-blink-features=AutomationControlled'],**extra)
    last_error=None
    for channel in ('chrome','msedge',None):
        try:
            if channel is None:return playwright.chromium.launch(**opts)
            return playwright.chromium.launch(channel=channel,**opts)
        except Exception as error:
            last_error=error
    raise BrowserUnavailableError(
        '未检测到可用浏览器。请启动 start.bat 完成浏览器组件安装，或安装 Chrome/Edge 后重试。'
    ) from last_error
