import json

import tools.tool_context as tool_context


def test_resolve_project_exact():
    tool_context.KNOWN_PROJECTS = ["德会", "德会隧道"] if hasattr(tool_context, "KNOWN_PROJECTS") else None


def test_resolver_contract_for_empty_device_query():
    result = json.loads(tool_context.resolve_mec_device.invoke({"query": "", "project": ""}))
    assert result["resolved"] is False
    assert "error" in result
