import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "work"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import asr_runtime
import pipeline


def make_tsv(path, items):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["序号", "类型", "链接", "标题", "发布日期", "点赞", "评论", "时长", "转发", "收藏", "图片"])
        for seq, video_id in items:
            writer.writerow([seq, "视频", f"https://www.douyin.com/video/{video_id}", f"演示作品{seq}", "20261002", 10, 2, 45, 1, 1, ""])


def test_all_transcription_failures_return_nonzero_and_structured_result(tmp_path, monkeypatch):
    tsv, work = tmp_path / "input.tsv", tmp_path / "work"
    work.mkdir()
    make_tsv(tsv, [(1, "100")])
    monkeypatch.setattr(sys, "argv", ["pipeline.py", str(tsv), str(work)])
    monkeypatch.setattr(pipeline, "download_one", lambda *args: (1, "synthetic download failure"))
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_: None)

    exit_code = pipeline.main()

    result = json.loads((work / "pipeline_result.json").read_text(encoding="utf-8"))
    assert exit_code == 2
    assert result["status"] == "failed"
    assert result["failed"] == 1
    assert result["failed_items"][0]["video_id"] == "100"

def test_partial_transcription_returns_distinct_status_and_failed_rows(tmp_path, monkeypatch):
    tsv, work = tmp_path / "input.tsv", tmp_path / "work"
    work.mkdir()
    make_tsv(tsv, [(1, "100"), (2, "200")])
    monkeypatch.setattr(sys, "argv", ["pipeline.py", str(tsv), str(work)])
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_: None)

    def download(url, mp4, cookie):
        if url.endswith("/100"):
            return 1, "synthetic download failure"
        mp4.parent.mkdir(parents=True, exist_ok=True)
        mp4.write_bytes(b"synthetic media")
        return 0, ""

    monkeypatch.setattr(pipeline, "download_one", download)
    monkeypatch.setattr(asr_runtime, "transcribe", lambda *args, **kwargs: ("识别结果", "zh", {"device": "cpu"}))

    exit_code = pipeline.main()

    result = json.loads((work / "pipeline_result.json").read_text(encoding="utf-8"))
    assert exit_code == 1
    assert result["status"] == "partial"
    assert result["done"] == 1 and result["failed"] == 1
    assert result["failed_items"][0]["video_id"] == "100"
