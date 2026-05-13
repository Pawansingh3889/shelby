"""Smoke tests for shelby.syscontrol."""
from __future__ import annotations


def test_known_app_recognises_curated_names():
    from shelby import syscontrol
    assert syscontrol.known_app("chrome")
    assert syscontrol.known_app("Chrome")  # case-insensitive
    assert syscontrol.known_app("calculator")
    assert syscontrol.known_app("vscode")
    assert not syscontrol.known_app("definitely-not-a-real-app-9999")


def test_list_known_returns_sorted_names():
    from shelby import syscontrol
    names = syscontrol.list_known()
    assert names == sorted(names)
    assert "chrome" in names
    assert "notepad" in names


def test_open_url_rejects_javascript_scheme():
    from shelby import syscontrol
    ok, msg = syscontrol.open_url("javascript:alert(1)")
    assert ok is False
    assert "javascript" in msg.lower()


def test_open_url_rejects_file_scheme():
    from shelby import syscontrol
    ok, msg = syscontrol.open_url("file:///etc/passwd")
    assert ok is False


def test_open_url_rejects_data_scheme():
    from shelby import syscontrol
    ok, msg = syscontrol.open_url("data:text/html,<script>x</script>")
    assert ok is False


def test_open_url_rejects_empty():
    from shelby import syscontrol
    ok, msg = syscontrol.open_url("")
    assert ok is False
    assert "no url" in msg.lower()


def test_open_app_rejects_empty():
    from shelby import syscontrol
    ok, msg = syscontrol.open_app("")
    assert ok is False
