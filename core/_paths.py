"""Portable source/resource paths and per-user app data."""
from pathlib import Path
import json
import os
import shutil
import sys

CODE_ROOT = (Path(getattr(sys, "_MEIPASS")).resolve()
             if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None)
             else Path(__file__).resolve().parent.parent)
CODE_CORE_DIR = CODE_ROOT / "core"
CODE_WORK_DIR = CODE_ROOT / "work"
CODE_APP_DIR = CODE_ROOT
APP_HOME = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else CODE_ROOT


def _default_user_data_dir(platform=None) -> Path:
    platform=platform or sys.platform
    if platform == "win32":
        root = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return root / "DouyinBlogDB"


def get_data_dir() -> Path:
    configured = os.getenv("DYDB_HOME") or os.getenv("DOUYIN_BLOG_DB_HOME")
    if configured and configured.strip():
        return Path(configured).expanduser().resolve()
    for candidate in (APP_HOME, *APP_HOME.parents):
        if any((candidate / name).is_file() for name in ("douyin_blog.db", "douyin_blog.sqlite3")):
            return candidate.resolve()
    return _default_user_data_dir().resolve()


BASE_DIR = get_data_dir()
APP_STATE_DIR = BASE_DIR / "runtime"
WORK_DIR = BASE_DIR / "work"
MODELS_DIR = BASE_DIR / "models"
DOWNLOADS_DIR = BASE_DIR / "downloads"
CONFIG_JSON = APP_STATE_DIR / "config.json"
# Read-only compatibility source for settings used by earlier desktop versions.
LEGACY_CONFIG_JSON = BASE_DIR / "daily" / "config.json"


def read_public_config():
    """Load only reusable runtime settings; never copy old private integrations."""
    for path in (CONFIG_JSON, LEGACY_CONFIG_JSON):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return {key: value[key] for key in ("browser", "whisper", "yt_proxy", "proxy", "http_proxy",
                                                "https_proxy", "python", "python_path")
                    if key in value}
    return {}


def get_proxy(default=None):
    if "DYDB_PROXY" in os.environ:
        value = os.environ["DYDB_PROXY"].strip()
        return None if value.lower() in ("", "direct", "none", "off", "false") else value
    config = read_public_config()
    for key in ("yt_proxy", "proxy", "http_proxy", "https_proxy"):
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def proxy_cli_args(proxy):
    return ["--proxy", proxy.strip()] if isinstance(proxy, str) and proxy.strip() else []


def get_python():
    if not getattr(sys, "frozen", False) and sys.executable and Path(sys.executable).is_file():
        return sys.executable
    configured = os.getenv("DYDB_PYTHON")
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())
    for key in ("python", "python_path"):
        value = read_public_config().get(key)
        if value and Path(value).is_file():
            return str(Path(value).resolve())
    candidates = (
        APP_HOME / ".venv" / "Scripts" / "python.exe",
        APP_HOME.parent / ".venv" / "Scripts" / "python.exe",
        CODE_ROOT / ".venv" / "Scripts" / "python.exe",
        APP_HOME / ".venv" / "bin" / "python",
        CODE_ROOT / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    for name in ("python", "py"):
        value = shutil.which(name)
        if value:
            return value
    raise RuntimeError("未找到Python解释器。请准备Python 3.10+，或通过DYDB_PYTHON指定路径。")
