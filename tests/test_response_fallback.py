from types import SimpleNamespace

from response_fallback import build_deterministic_fallback, collect_turn_tool_context


def msg(msg_type, content="", **kwargs):
    return SimpleNamespace(type=msg_type, content=content, **kwargs)


def test_collects_only_current_turn_tools_and_errors():
    messages = [
        msg("human", "上一轮"),
        msg("tool", '{"error":"旧错误"}', name="old_tool"),
        msg("ai", "上一轮回复"),
        msg("human", "诊断设备"),
        msg("tool", '{"type":"diagnose_device_result","status":"error","root_cause":"容器不可达","dimensions":[{"name":"container","status":"error"}]}', name="mec_diagnose_device"),
        msg("tool", "连接超时", name="mec_ssh_exec"),
        msg("ai", ""),
    ]
    tools, errors = collect_turn_tool_context(messages)
    assert tools == ["mec_diagnose_device", "mec_ssh_exec"]
    assert any("容器不可达" in error for error in errors)
    assert any("连接超时" in error for error in errors)
    assert all("旧错误" not in error for error in errors)


def test_fallback_is_non_empty_and_contains_tools_and_errors():
    text = build_deterministic_fallback(
        [],
        tool_names=["mec_diagnose_device", "mec_ssh_exec"],
        errors=["连接超时"],
    )
    assert text.strip()
    assert "mec_diagnose_device" in text
    assert "mec_ssh_exec" in text
    assert "连接超时" in text
    assert "LLM" not in text or "模型没有返回可显示的正文" in text


def test_fallback_does_not_call_a_model():
    text = build_deterministic_fallback([], tool_names=[], errors=[])
    assert text.startswith("本轮处理已完成")
