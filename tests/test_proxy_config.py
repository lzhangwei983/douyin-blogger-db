import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import _paths


def test_proxy_defaults_to_direct_when_user_has_not_configured_one(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "CONFIG_JSON", tmp_path / "missing-config.json")
    for key in ("DYDB_PROXY", "YT_PROXY", "HTTP_PROXY", "HTTPS_PROXY"):
        monkeypatch.delenv(key, raising=False)
    assert _paths.get_proxy() is None


def test_explicit_empty_config_disables_system_proxy_fallback(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"yt_proxy": ""}), encoding="utf-8")
    monkeypatch.setattr(_paths, "CONFIG_JSON", path)
    monkeypatch.setenv("YT_PROXY", "http://127.0.0.1:10808")
    assert _paths.get_proxy() is None


def test_custom_proxy_setting_is_used(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"yt_proxy": "http://127.0.0.1:32100"}), encoding="utf-8")
    monkeypatch.setattr(_paths, "CONFIG_JSON", path)
    monkeypatch.delenv("DYDB_PROXY", raising=False)
    monkeypatch.delenv("YT_PROXY", raising=False)
    assert _paths.get_proxy() == "http://127.0.0.1:32100"
