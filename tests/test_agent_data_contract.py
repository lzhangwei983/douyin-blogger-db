"""A user-selected Agent can exchange general analysis without a vendor SDK."""
import sys
import runpy
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(app,'DB_PATH',tmp_path/'agent-data.db')
    app.init_db()
    with TestClient(app.app,base_url='http://127.0.0.1') as test_client:
        yield test_client


def test_agent_can_export_full_analysis_from_local_database(client):
    blogger=client.post('/api/bloggers',json={'name':'测试博主','slug':'agent-test','platform':'抖音'})
    assert blogger.status_code==200
    video=client.post(f"/api/bloggers/{blogger.json()['id']}/videos",
                      json={'url':'https://www.douyin.com/video/1111111111111111111',
                            'kind':'视频','title':'样本作品','subtitle':'样本字幕'})
    assert video.status_code==200
    with app.get_db() as con:
        con.execute("""INSERT INTO analyses(video_id,full_md,summary,key_points,advice,industries,
          risks,credibility,actionable,parsed_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
          (video.json()['id'],'## 内容摘要\n完整Agent正文','摘要','观点','建议','行业','风险',
           '中等','值得',app.now()))
        con.execute('UPDATE videos SET has_analysis=1 WHERE id=?',(video.json()['id'],))
    exported=client.get('/api/export?format=json')
    assert exported.status_code==200
    record=exported.json()[0]['videos'][0]
    assert record['subtitle']=='样本字幕'
    assert record['analysis']['full_md']=='## 内容摘要\n完整Agent正文'


def test_general_markdown_import_contract_maps_analysis_fields(tmp_path):
    importer=runpy.run_path(str(Path(__file__).resolve().parents[1]/'work/import_to_db.py'))
    report=tmp_path/'analysis.md'
    report.write_text("""## 基本信息
- **标题**: 测试作品
## 内容摘要
正文摘要
## 核心观点
正文观点
## 可执行建议
正文建议
## 适合的行业或工作方向（归纳博主建议，非事实）
行业方向
## 风险和可疑之处
风险
## 对博主可信度的判断
待核实
## 哪些建议值得执行/需谨慎
需要逐项核实
""",encoding='utf-8')
    sections,info=importer['parse_md'](report)
    fields=importer['FIELD_MAP']
    assert info['标题']=='测试作品'
    assert sections['内容摘要']=='正文摘要'
    assert len(fields)==7 and set(fields.values())=={
        'summary','key_points','advice','industries','risks','credibility','actionable'}
