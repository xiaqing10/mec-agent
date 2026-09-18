import json

import tools.tool_context as tool_context


def test_resolve_device_unique(monkeypatch):
    monkeypatch.setattr(
        tool_context,
        "lookup_device",
        lambda query, project=None: [{
            "name": "zk26_690",
            "ip": "10.145.4.1",
            "project": "柯诸",
            "pole": "K26",
        }],
    )
    result = json.loads(
        tool_context.resolve_mec_device.invoke({"query": "690", "project": "柯诸"})
    )
    assert result["resolved"] is True
    assert result["device"]["ip"] == "10.145.4.1"
    assert result["device"]["project"] == "柯诸"


def test_resolve_device_ambiguous(monkeypatch):
    monkeypatch.setattr(
        tool_context,
        "lookup_device",
        lambda query, project=None: [
            {"name": "zk26_690", "ip": "10.145.4.1", "project": "柯诸", "pole": ""},
            {"name": "dh26_690", "ip": "10.145.5.1", "project": "德会", "pole": ""},
        ],
    )
    result = json.loads(
        tool_context.resolve_mec_device.invoke({"query": "690", "project": ""})
    )
    assert result["resolved"] is False
    assert result["ambiguous"] is True
    assert len(result["candidates"]) == 2


def test_resolve_project_exact():
    result = json.loads(
        tool_context.resolve_mec_project.invoke({"query": "德会"})
    )
    assert result["resolved"] is True
    assert result["project"] == "德会"
