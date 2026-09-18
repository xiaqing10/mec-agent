import py_compile
from pathlib import Path


def test_runtime_python_sources_compile():
    for path in (
        Path("diagnose_mec/ssh.py"),
        Path("tools/tool_device.py"),
    ):
        py_compile.compile(str(path), doraise=True)
