from diagnose_mec.result_schema import build_domain_result, finalize_summary


def test_canonical_diagnosis_schema():
    result = build_domain_result(
        result_type="diagnose_device_result",
        ip="10.0.0.1",
        project="德会",
        overall="error",
        root_cause="process_error",
        deep_analysis_recommended=True,
        next_action="deep_analysis",
        dimensions=[{"name": "进程", "status": "error", "detail": "infer异常"}],
    )
    result = finalize_summary(result)
    assert result["schema_version"] == "1.0"
    assert result["entity"]["ip"] == "10.0.0.1"
    assert result["project"] == "德会"
    assert result["root_cause"] == "process_error"
    assert result["deep_analysis_recommended"] is True
    assert result["next_action"] == "deep_analysis"
    assert "进程" in result["summary_for_llm"]
