import main


def test_desktop_window_failure_shows_a_chinese_webview_runtime_guide(monkeypatch):
    messages = []

    class BrokenWebview:
        def create_window(self, *args, **kwargs): pass
        def start(self, **kwargs): raise RuntimeError("runtime missing")

    monkeypatch.setattr(main, "show_startup_error", messages.append)

    started = main.start_desktop_window(BrokenWebview(), "http://127.0.0.1:8321", "icon.ico")

    assert started is False
    assert "桌面窗口" in messages[0]
    assert "WebView2 Runtime" in messages[0]


def test_desktop_window_reports_success_when_the_shell_starts(monkeypatch):
    class WorkingWebview:
        def create_window(self, *args, **kwargs): pass
        def start(self, **kwargs): pass

    assert main.start_desktop_window(WorkingWebview(), "http://127.0.0.1:8321", "icon.ico") is True
