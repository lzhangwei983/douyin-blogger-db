# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from pathlib import Path

SPEC_DIR = Path(SPECPATH).resolve()
PROJECT_ROOT = SPEC_DIR
datas = [(str(SPEC_DIR / 'static'), 'static'), (str(SPEC_DIR / 'icon.ico'), '.')]
for source_dir, relative_target in ((SPEC_DIR / 'core', 'core'), (SPEC_DIR / 'work', 'work')):
    for source_file in source_dir.glob('*.py'):
        datas.append((str(source_file), relative_target))
binaries = []
hiddenimports = ['storage','_paths','task_store','job_runner','collection_service','video_collection_service',
                 'collection_range','metrics_service','metrics_import_service','browser_runtime',
                 'process_identity','asr_runtime','cookie_scope','python_multipart','psutil',
                 'playwright.sync_api']
for pkg in ('yt_dlp', 'webview', 'uvicorn', 'playwright', 'psutil', 'faster_whisper', 'av'):
    try:
        tmp_ret = collect_all(pkg)
        datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
    except Exception:
        pass


a = Analysis(
    ['main.py'],
    pathex=[str(SPEC_DIR), str(SPEC_DIR / 'core'), str(SPEC_DIR / 'work')],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DouyinBlogDB',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)
