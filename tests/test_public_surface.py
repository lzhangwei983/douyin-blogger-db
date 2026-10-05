"""The personal daily workflow must not be exposed in the community edition."""
import sys
import json
from pathlib import Path
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
from _paths import CONFIG_JSON


def test_openapi_has_collection_and_library_routes_but_no_personal_daily_routes():
    paths=set(app.app.openapi()['paths'])
    assert '/api/collection' in paths
    assert '/api/collection/videos' in paths
    assert '/api/collection/login' in paths
    assert '/api/export' in paths
    private=[path for path in paths
             if path.startswith(('/api/daily','/api/review','/api/jev','/api/schedule'))]
    assert private==[]


def test_public_navigation_contains_no_personal_workflow():
    html=(Path(__file__).resolve().parents[1]/'static/index.html').read_text(encoding='utf-8')
    for label in ('每日信息差','候选审核','Jev','飞书推送','定向爬取存档'):
        assert label not in html


def test_public_source_has_no_personal_serverdock_integration():
    root=Path(__file__).resolve().parents[1]
    sources=[root/'app.py',root/'main.py',root/'core'/'asr_runtime.py',root/'core'/'_paths.py']
    text='\n'.join(path.read_text(encoding='utf-8') for path in sources)
    assert 'ServerDock' not in text
    assert 'projects.json' not in text


def test_settings_and_pipeline_status_expose_only_user_workflows():
    with TestClient(app.app,base_url='http://127.0.0.1') as client:
        settings=client.get('/api/settings')
        assert settings.status_code==200
        assert not any(key in settings.json() for key in ('jev','feishu','feishu_webhook','feishu_secret'))
        status=client.get('/api/pipeline/status')
        assert status.status_code==200
        assert 'daily' not in status.json()
        saved=client.put('/api/settings',json={
            'browser':{'mode':'background'},'device':'cpu','num_workers':2,
            'jev':{'enabled':True,'client_dir':'private-agent-directory'},
            'feishu_webhook':'synthetic-private-value','feishu_secret':'synthetic-secret',
        })
        assert saved.status_code==200
        stored=json.loads(CONFIG_JSON.read_text(encoding='utf-8'))
        assert set(stored)<= {'whisper','browser','yt_proxy'}
        assert 'jev' not in stored and 'feishu_webhook' not in stored


def test_cookie_status_covers_only_douyin():
    with TestClient(app.app,base_url='http://127.0.0.1') as client:
        result=client.get('/api/cookies/status')
    assert result.status_code==200
    assert set(result.json())=={'douyin'}
