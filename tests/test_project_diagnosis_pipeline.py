import json
from unittest.mock import patch

from diagnose_project import diagnose_device


def test_project_device_pipeline_uses_canonical_result_and_deep_router():
    basic = {
        "schema_version": "1.0",
        "type": "diagnose_device_result",
        "status": "error",
        "stage": "basic",
        "entity": {"ip": "10.0.0.1", "project": "德会"},
        "ip": "10.0.0.1",
        "project": "德会",
        "root_cause": "process_error",
        "deep_analysis_recommended": True,
        "next_action": "deep_analysis",
        "evidence": [{"dimension": "进程", "detail": "infer异常"}],
    }
    deep = {
        "schema_version": "1.0",
        "type": "deep_diagnosis_result",
        "status": "normal",
        "stage": "deep",
        "ip": "10.0.0.1",
        "project": "德会",
        "analysis": "进程异常与模型加载失败相关",
    }

    with patch("tools.mec_diagnose_device.invoke", return_value=json.dumps(basic, ensure_ascii=False)) as basic_invoke, \
         patch("tools.mec_llm_diagnose_device.invoke", return_value=__import__("json").dumps(deep, ensure_ascii=False)) as deep_invoke:
        result = diagnose_device({"name": "mec_1001", "ip": "10.0.0.1", "project": "德会"})

    basic_invoke.assert_called_once_with({"ip": "10.0.0.1", "project": "德会"})
    deep_invoke.assert_called_once_with({"ip": "10.0.0.1", "project": "德会"})
    assert result["type"] == "diagnose_device_result"
    assert result["device_name"] == "mec_1001"
    assert result["stage"] == "deep"
    assert result["next_action"] == "report"
    assert result["deep_analysis_recommended"] is False
    assert result["deep_analysis"]["analysis"].startswith("进程异常")


def test_project_device_pipeline_does_not_invoke_deep_llm_when_not_recommended():
    basic = {
        "schema_version": "1.0",
        "type": "diagnose_device_result",
        "status": "error",
        "stage": "basic",
        "entity": {"ip": "10.0.0.2", "project": "德会"},
        "ip": "10.0.0.2",
        "project": "德会",
        "root_cause": "docker_service_down",
        "deep_analysis_recommended": False,
        "next_action": "report",
    }

    with patch("tools.mec_diagnose_device.invoke", return_value=__import__("json").dumps(basic, ensure_ascii=False)) as basic_invoke, \
         patch("tools.mec_llm_diagnose_device.invoke") as deep_invoke:
        result = diagnose_device({"name": "mec_1002", "ip": "10.0.0.2", "project": "德会"})

    basic_invoke.assert_called_once_with({"ip": "10.0.0.2", "project": "德会"})
    deep_invoke.assert_not_called()
    assert result["next_action"] == "report"
    assert "deep_analysis" not in result


def test_project_batch_deduplicates_candidates_and_uses_one_device_tool():
    db_text = """异常设备列表
| 设备名 | IP | 物理机 | 容器 | 图片 | 其他 | 状态 | 备注 |
| mec-a | 10.0.0.1 | ✅ 在线 | ❌ 离线 | - | - | 异常 | - |
| mec-a | 10.0.0.1 | ✅ 在线 | ❌ 离线 | - | - | 异常 | - |
| mec-b | 10.0.0.2 | ✅ 在线 | ✅ 在线 | 0张 | - | 异常 | - |
"""
    basic_a = {"schema_version": "1.0", "type": "diagnose_device_result", "status": "error", "stage": "basic", "entity": {"ip": "10.0.0.1", "project": "德会"}, "ip": "10.0.0.1", "project": "德会", "root_cause": "docker_service_down", "deep_analysis_recommended": False, "next_action": "report"}
    basic_b = {"schema_version": "1.0", "type": "diagnose_device_result", "status": "error", "stage": "basic", "entity": {"ip": "10.0.0.2", "project": "德会"}, "ip": "10.0.0.2", "project": "德会", "root_cause": "zero_images", "deep_analysis_recommended": False, "next_action": "report"}

    with patch("tools.tool_db.query_mec_project_from_db.invoke", return_value=db_text), \
         patch("tools.mec_diagnose_device.invoke", side_effect=[json.dumps(basic_a, ensure_ascii=False), json.dumps(basic_b, ensure_ascii=False)]) as device_invoke:
        from diagnose_project import diagnose_project
        result = diagnose_project("德会")

    assert result["total_diagnosed"] == 2
    assert device_invoke.call_count == 2
    device_invoke.assert_any_call({"ip": "10.0.0.1", "project": "德会"})
    device_invoke.assert_any_call({"ip": "10.0.0.2", "project": "德会"})
