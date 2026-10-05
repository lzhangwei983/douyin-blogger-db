from types import SimpleNamespace

import pytest

import browser_runtime


class FakeChromium:
    def __init__(self, available):
        self.available = available
        self.calls = []
        self.browser = object()

    def launch(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("channel") not in self.available:
            raise RuntimeError("browser not installed")
        return self.browser


def test_browser_uses_installed_edge_before_bundled_chromium():
    chromium = FakeChromium({"msedge"})

    browser = browser_runtime.launch_browser(SimpleNamespace(chromium=chromium))

    assert browser is chromium.browser
    assert [call.get("channel") for call in chromium.calls] == ["chrome", "msedge"]


def test_browser_falls_back_to_playwright_chromium_when_no_system_browser_exists():
    chromium = FakeChromium({None})

    browser = browser_runtime.launch_browser(SimpleNamespace(chromium=chromium))

    assert browser is chromium.browser
    assert [call.get("channel") for call in chromium.calls] == ["chrome", "msedge", None]


def test_browser_unavailable_error_explains_chromium_setup():
    chromium = FakeChromium(set())
    with pytest.raises(browser_runtime.BrowserUnavailableError, match="浏览器|Chromium|安装"):
        browser_runtime.launch_browser(SimpleNamespace(chromium=chromium))
