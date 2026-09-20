"""Version contract tests for API, Web UI, and documentation."""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_shared_version_contract():
    config = (ROOT / "config.py").read_text(encoding="utf-8")
    server = (ROOT / "server.py").read_text(encoding="utf-8")
    webui = (ROOT / "webui.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    match = re.search(r'AGENT_VERSION = os\.getenv\("AGENT_VERSION", "([^"]+)"\)', config)
    assert match, "config.py must define the shared AGENT_VERSION"
    version = match.group(1)

    assert '"version": AGENT_VERSION' in server
    assert "AGENT_VERSION" in webui
    assert f"v{version} · LangGraph" in readme
    assert "v4.0-debug" not in webui
    assert "3.3debug" not in server
