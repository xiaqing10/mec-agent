from pathlib import Path


WEBUI = Path(__file__).resolve().parents[1] / "webui.py"


def test_webui_renders_markdown_and_normalizes_escaped_linebreaks():
    source = WEBUI.read_text(encoding="utf-8")
    assert "window.markdownit" in source
    assert "function renderMD(text)" in source
    assert ".replace(/\\\\n/g, '\\n')" in source
    assert "text = md.render(text)" in source


def test_webui_always_clears_stream_controller():
    source = WEBUI.read_text(encoding="utf-8")
    assert "window._streamController = controller" in source
    assert "if (window._streamController === controller) window._streamController = null;" in source
    assert "btn.disabled = false;" in source
