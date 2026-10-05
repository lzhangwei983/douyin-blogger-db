import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "work"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import pipeline


def test_transcription_download_forwards_explicit_public_proxy(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(pipeline, "get_proxy", lambda: "http://127.0.0.1:32100")
    monkeypatch.setattr(
        pipeline.subprocess,
        "run",
        lambda command, **kwargs: (commands.append(command) or SimpleNamespace(returncode=1, stdout="", stderr="offline")),
    )

    pipeline.download_one("https://www.douyin.com/video/123", tmp_path / "video.mp4", None)

    assert len(commands) == 1
    assert commands[0][commands[0].index("--proxy") + 1] == "http://127.0.0.1:32100"


def test_transcription_download_does_not_force_proxy_in_direct_mode(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(pipeline, "get_proxy", lambda: None)
    monkeypatch.setattr(
        pipeline.subprocess,
        "run",
        lambda command, **kwargs: (commands.append(command) or SimpleNamespace(returncode=1, stdout="", stderr="offline")),
    )

    pipeline.download_one("https://www.douyin.com/video/123", tmp_path / "video.mp4", None)

    assert len(commands) == 1
    assert "--proxy" not in commands[0]
