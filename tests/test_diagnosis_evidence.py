from diagnose_mec.evidence import correlate_root_cause


def _result(problems, details=()):
    dims = []
    for i, problem in enumerate(problems):
        dims.append({
            "name": f"d{i}",
            "status": "error",
            "problem": problem,
            "detail": details[i] if i < len(details) else "",
        })
    return {"root_cause": "process_error", "dimensions": dims, "symptoms": []}


def test_oom_outranks_process_symptoms():
    result = correlate_root_cause(_result(
        ["process_error", "zero_images"],
        ["infer FATAL", "今日图片为0"],
    ))
    assert result["root_cause"] == "process_error"

    result = correlate_root_cause(_result(
        ["process_error", "zero_images"],
        ["OOM killed: out of memory", "今日图片为0"],
    ))
    assert result["root_cause"] == "container_memory_exhaustion"
    assert result["root_cause_confidence"] == "high"


def test_zero_images_is_not_treated_as_strong_root_cause():
    result = correlate_root_cause(_result(["zero_images"], ["今日图片为0"]))
    assert result["root_cause"] == "zero_images"
    assert result["root_cause_confidence"] == "low"


def test_infrastructure_failure_has_high_confidence():
    result = correlate_root_cause(_result(["docker_service_down"], ["Docker服务未运行"]))
    assert result["root_cause"] == "docker_service_down"
    assert result["root_cause_confidence"] == "high"
