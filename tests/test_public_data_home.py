"""Data storage uses user-writable paths and keeps legacy databases."""
import os
import subprocess
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
import _paths


def test_app_uses_configured_user_data_home(tmp_path):
    target=tmp_path/'中文 data folder'
    env=os.environ.copy()
    env['DYDB_HOME']=str(target)
    code="import sys; sys.platform='linux'; import app; print(app.DATA_DIR)"
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],
                          env=env,capture_output=True,text=True,check=True)
    assert Path(result.stdout.strip().splitlines()[-1])==target.resolve()
    assert target.is_dir()


def test_new_install_default_paths_follow_the_platform(tmp_path):
    env=os.environ.copy()
    env['XDG_DATA_HOME']=str(tmp_path/'xdg')
    env['LOCALAPPDATA']=str(tmp_path/'local')
    env['DYDB_HOME']=str(tmp_path/'bootstrap')
    code=("import sys; sys.platform='linux'; sys.path.insert(0,'core'); import _paths; "
          "print(_paths._default_user_data_dir('linux')); "
          "print(_paths._default_user_data_dir('win32'))")
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],
                          env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    lines=result.stdout.strip().splitlines()
    assert Path(lines[-2])==(tmp_path/'xdg'/'DouyinBlogDB').resolve()
    assert Path(lines[-1])==(tmp_path/'local'/'DouyinBlogDB').resolve()


def test_app_reuses_database_next_to_legacy_install(tmp_path,monkeypatch):
    old_home=tmp_path/'legacy-install'
    old_home.mkdir()
    (old_home/'douyin_blog.db').write_bytes(b'existing-user-data')
    monkeypatch.setattr(_paths,'APP_HOME',old_home)
    monkeypatch.delenv('DYDB_HOME',raising=False)
    monkeypatch.delenv('DOUYIN_BLOG_DB_HOME',raising=False)
    assert _paths.get_data_dir()==old_home.resolve()
