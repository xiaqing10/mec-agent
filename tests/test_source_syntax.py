import py_compile
from pathlib import Path


def test_runtime_python_sources_compile():
    for path in (
        Path("diagnose_mec/ssh.py"),
        Path("tools/tool_device.py"),
    ):
        py_compile.compile(str(path), doraise=True)


def test_device_tools_have_runtime_local_json_guard():
    device_source = Path("tools/tool_device.py").read_text(encoding="utf-8")
    db_source = Path("tools/tool_db.py").read_text(encoding="utf-8")
    assert "def mec_diagnose_device" in device_source
    assert "import json as _json" in device_source
    assert "def query_mec_device_from_db" in db_source
    assert "import json as _json" in db_source
    assert "_json.dumps(" in device_source
    assert "_json.dumps(" in db_source
