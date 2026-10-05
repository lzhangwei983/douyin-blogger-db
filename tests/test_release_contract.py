from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_packaging_targets_only_public_core_and_work_modules():
    spec = (ROOT / "DouyinBlogDB.spec").read_text(encoding="utf-8").lower()
    assert "daily" not in spec
    assert "jev" not in spec
    assert "report_store" not in spec
    assert "spec_dir / 'core'" in spec
    assert "spec_dir / 'work'" in spec


def test_documentation_matches_the_public_collection_and_license_scope():
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert "每日信息差" not in readme
    assert "视频链接" in readme
    assert "起始日期" in readme or "开始日期" in readme
    assert "polyform noncommercial license 1.0.0" in readme
    assert "v1.0.6" in readme and "mit" in readme
    assert "start.bat" in readme


def test_clean_checkout_has_one_click_windows_setup_and_complete_browser_dependencies():
    launcher = (ROOT / "start.bat").read_text(encoding="utf-8").lower()
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "-m venv .venv" in launcher
    assert "pip install -r requirements.txt" in launcher
    assert "playwright install chromium" in launcher
    assert "playwright" in requirements
    assert "psutil" in requirements
    assert "python-multipart" in requirements


def test_transcription_pipeline_has_no_machine_specific_ffmpeg_fallback():
    pipeline = (ROOT / "work" / "pipeline.py").read_text(encoding="utf-8")
    assert not re.search(r"[A-Z]:[\\/](?:ffmpeg|DouyinBlogDB)(?:[\\/]|$)", pipeline, re.IGNORECASE)


def test_release_script_builds_a_clean_source_archive_and_licensed_windows_bundle():
    script = (ROOT / "package_release.ps1").read_text(encoding="utf-8").lower()
    assert "git archive" in script
    assert "third_party_notices.md" in script
    assert "--no-license-path" in script
    assert "发行版 exe 冒烟启动失败" in script
    assert "-or -not (test-path $smokereport)" not in script
    assert "license" in script
    assert "agent_guide.md" in script
    assert "douyinblogdb-windows-v$version.zip" in script
    assert "douyinblogdb-source-v$version.zip" in script
    assert "dydb_build_python" in script
    assert ".release-package-venv" in script
    assert "d:/" not in script


def test_public_guides_describe_manual_agent_work_and_current_collection_path():
    workflow = (ROOT / "数据采集流程与工作原理.md").read_text(encoding="utf-8")
    agent = (ROOT / "AGENT_GUIDE.md").read_text(encoding="utf-8")
    assert "yt-dlp" in workflow and "faster-whisper" in workflow
    assert "LLM 阅读字幕稿" not in workflow
    assert "GET /api/export?format=json" in agent
    assert "work/import_to_db.py" in agent
    assert "不会自动调用云端模型" in agent
