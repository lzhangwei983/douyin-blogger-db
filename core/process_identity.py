"""PID identity helpers; a live PID is not sufficient proof of task ownership."""
from __future__ import annotations

import json
from pathlib import Path

import psutil

from storage import atomic_text


def _script_name(value):
    return str(value or "").replace("\\", "/").rsplit("/", 1)[-1].casefold()


def process_matches_pid(pid, expected_script=None, created_at=None):
    try:
        process = psutil.Process(int(pid))
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return False
        if created_at is not None and abs(process.create_time() - float(created_at)) > 1.0:
            return False
        expected = _script_name(expected_script)
        if expected:
            args = [_script_name(arg) for arg in process.cmdline()]
            if expected not in args:
                return False
        return True
    except (ValueError, TypeError, psutil.Error, OSError):
        return False


def read_process_identity(path):
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        value = raw
    if isinstance(value, dict):
        try:
            return {"pid": int(value["pid"]), "create_time": value.get("create_time"),
                    "script": value.get("script") or ""}
        except (KeyError, ValueError, TypeError):
            return None
    try:
        return {"pid": int(value), "create_time": None, "script": ""}
    except (ValueError, TypeError):
        return None


def write_process_identity(path, pid, script):
    path = Path(path)
    try:
        created = psutil.Process(int(pid)).create_time()
    except (ValueError, TypeError, psutil.Error, OSError):
        created = None
    identity = {"pid": int(pid), "create_time": created,
                "script": str(Path(script).resolve()) if script else ""}
    atomic_text(path, json.dumps(identity, ensure_ascii=False))
    return identity


def process_file_is_running(path, expected_script=None):
    identity = read_process_identity(path)
    if not identity:
        return False
    return process_matches_pid(identity["pid"], expected_script or identity.get("script"), identity.get("create_time"))
