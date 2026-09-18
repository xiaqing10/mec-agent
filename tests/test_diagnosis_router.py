from diagnosis_router import route_device_result, should_run_deep_analysis


def test_router_calls_deep_analysis_only_when_recommended():
    calls = []
    result = {
        "type": "diagnose_device_result",
        "ip": "10.0.0.1",
        "project": "德会",
        "deep_analysis_recommended": True,
        "next_action": "deep_analysis",
    }
    routed = route_device_result(
        result,
        deep_analysis_invoke=lambda ip, project: calls.append((ip, project)) or
        {"type": "deep_diagnosis_result", "analysis": "ok"},
    )
    assert should_run_deep_analysis(result) is True
    assert calls == [("10.0.0.1", "德会")]
    assert routed["stage"] == "deep"
    assert routed["next_action"] == "report"
    assert routed["deep_analysis_recommended"] is False


def test_router_does_not_call_deep_analysis_for_access_only_warning():
    calls = []
    result = {
        "type": "diagnose_device_result",
        "ip": "10.0.0.2",
        "project": "德会",
        "root_cause": "physical_ssh_unavailable",
        "deep_analysis_recommended": False,
        "next_action": "report",
    }
    routed = route_device_result(result, deep_analysis_invoke=lambda *_: calls.append(1))
    assert should_run_deep_analysis(result) is False
    assert calls == []
    assert routed["next_action"] == "report"


def test_router_marks_deep_failure_terminal():
    result = {
        "type": "diagnose_device_result",
        "ip": "10.0.0.3",
        "project": "德会",
        "deep_analysis_recommended": True,
    }
    routed = route_device_result(result, deep_analysis_invoke=lambda *_: (_ for _ in ()).throw(RuntimeError("timeout")))
    assert routed["deep_analysis_recommended"] is False
    assert routed["next_action"] == "report"
    assert "deep_analysis_error" in routed