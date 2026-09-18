import py_compile
from pathlib import Path


def test_runtime_python_sources_compile():
    for path in (
        Path("diagnose_mec/ssh.py"),
        Path("tools/tool_device.py"),
    ):
        py_compile.compile(str(path), doraise=True)


def test_device_tools_have_runtime_json_import_and_tool_docstrings():
    device_source = Path("tools/tool_device.py").read_text(encoding="utf-8")
    db_source = Path("tools/tool_db.py").read_text(encoding="utf-8")
    assert "def mec_diagnose_device" in device_source
    assert "import json" in device_source
    assert "def query_mec_device_from_db" in db_source
    assert "import json" in db_source
    assert "json.dumps(" in device_source
    assert "json.dumps(" in db_source

    # LangChain @tool requires the function docstring to be the first
    # statement. A local import/comment before it makes the decorator fail
    # during module import with "Function must have a docstring".
    device_marker = "def mec_diagnose_device(ip: str, project: str = \"\") -> str:\n"
    assert device_marker in device_source
    device_after_def = device_source.split(device_marker, 1)[1]
    assert device_after_def.startswith('    \"\"\"')

    db_marker = "def query_mec_device_from_db(ip: str) -> str:\n"
    assert db_marker in db_source
    db_after_def = db_source.split(db_marker, 1)[1]
    assert db_after_def.startswith('    \"\"\"')
