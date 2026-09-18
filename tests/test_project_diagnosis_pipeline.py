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

    with patch("tools.mec_diagnose_device.invoke", return_value=__import__("json").dumps(basic, ensure_ascii=False)) as basic_invoke, \
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
