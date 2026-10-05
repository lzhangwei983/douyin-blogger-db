"""Portable package code and user data path behavior."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CORE=ROOT/'core'


def child(code,extra_env):
    env={**os.environ,**extra_env}
    env['PYTHONPATH']=str(CORE)+os.pathsep+env.get('PYTHONPATH','')
    return subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,
                          capture_output=True,text=True,check=True)


def test_dydb_home_selects_user_data_but_not_installed_code(tmp_path):
    data=tmp_path/'用户数据'
    result=child("import json,_paths;print(json.dumps({'data':str(_paths.get_data_dir()),'code':str(_paths.CODE_ROOT)}))",
                 {'DYDB_HOME':str(data)})
    paths=json.loads(result.stdout.strip().splitlines()[-1])
    assert Path(paths['data'])==data.resolve()
    assert Path(paths['code'])==ROOT.resolve()


def test_first_run_defaults_to_platform_user_writable_location(tmp_path):
    env={'LOCALAPPDATA':str(tmp_path/'local'),'XDG_DATA_HOME':str(tmp_path/'xdg'),
         'DYDB_HOME':str(tmp_path/'bootstrap')}
    code=("import _paths;print(_paths._default_user_data_dir('win32'));"
          "print(_paths._default_user_data_dir('linux'))")
    result=child(code,env)
    lines=result.stdout.strip().splitlines()
    assert Path(lines[-2])==(tmp_path/'local'/'DouyinBlogDB').resolve()
    assert Path(lines[-1])==(tmp_path/'xdg'/'DouyinBlogDB').resolve()


def test_existing_database_next_to_install_is_reused(tmp_path):
    installed=tmp_path/'existing-install'
    core=installed/'core'
    core.mkdir(parents=True)
    shutil.copy2(ROOT/'core'/'_paths.py',core/'_paths.py')
    (installed/'douyin_blog.db').write_bytes(b'legacy-database-marker')
    env={**os.environ,'PYTHONPATH':str(core),'DYDB_HOME':''}
    result=subprocess.run([sys.executable,'-c','import _paths;print(_paths.get_data_dir())'],
                          cwd=tmp_path,env=env,capture_output=True,text=True,check=True)
    assert Path(result.stdout.strip())==installed.resolve()


def test_packaged_app_serves_its_own_ui_even_if_data_contains_old_static_files(tmp_path):
    data=tmp_path/'data'
    bundle=tmp_path/'bundle'
    (data/'app'/'static').mkdir(parents=True)
    (bundle/'static').mkdir(parents=True)
    (data/'app'/'static'/'index.html').write_text('other version',encoding='utf-8')
    (bundle/'static'/'index.html').write_text('bundle version',encoding='utf-8')
    code=('import sys,json;sys.frozen=True;sys._MEIPASS='+repr(str(bundle))+
          ';import app;print(json.dumps((app.STATIC_DIR/\"index.html\").read_text()))')
    result=child(code,{'DYDB_HOME':str(data)})
    assert json.loads(result.stdout.strip().splitlines()[-1])=='bundle version'
