"""Regression tests for the deterministic diagnosis boundary."""
from pathlib import Path
import ast


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_agent_binds_only_explicit_agent_tool_allowlist():
    source = _source("agent.py")
    assert "from tools import AGENT_TOOLS" in source
    assert "ToolNode(AGENT_TOOLS)" in source
    assert "ToolNode(TOOLS)" not in source


def test_agent_route_does_not_expose_raw_ssh_or_diagnosis_tools():
    source = _source("agent.py")
    start = source.index("def _select_agent_tools")
    end = source.index("# ──────────────────────────────────────────────", start + 20)
    block = source[start:end]
    for forbidden in (
        '"mec_diagnose_device"',
        '"mec_diagnose_project"',
        '"mec_llm_diagnose_device"',
        '"mec_ssh_exec"',
        '"feishu_llm_analyze_logs"',
    ):
        assert forbidden not in block


def test_diagnosis_workflow_has_no_langchain_tool_decorator():
    source = _source("diagnose_mec/workflow.py")
    tree = ast.parse(source)
    decorators = [
        d
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for d in node.decorator_list
    ]
    assert not any(
        isinstance(d, ast.Name) and d.id == "tool" for d in decorators
    )


def test_agent_tool_allowlist_excludes_execution_primitives():
    source = _source("tools/__init__.py")
    tree = ast.parse(source)
    assignment = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "AGENT_TOOL_NAMES" for t in node.targets)
    )
    names = {elt.value for elt in assignment.value.elts}
    assert {"mec_ssh_exec", "mec_diagnose_device", "mec_diagnose_project"} .isdisjoint(names)
