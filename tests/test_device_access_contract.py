import json

from tools import _shared


def test_canonical_result_marks_unreachable():
    result = json.loads(
        _shared._build_diag_result(
            "10.0.0.1",
            [
                {"name": "可达性", "status": "error", "detail": "both paths failed", "problem": "device_unreachable"},
            ],
            "device_unreachable",
            project="德会",
            access={
                "device_reachable": False,
                "physical_ssh": False,
                "container_ssh": False,
                "access_mode": "none",
            },
        )
    )
    assert result["type"] == "diagnose_device_result"
    assert result["next_action"] == "report"
    assert result["access"]["device_reachable"] is False


def test_canonical_result_deep_analysis_is_not_needed_for_access_failure():
    result = json.loads(
        _shared._build_diag_result(
            "10.0.0.1",
            [
                {"name": "物理机", "status": "warning", "detail": "physical ssh unavailable", "problem": "physical_ssh_unavailable"},
                {"name": "容器", "status": "ok", "detail": "container reachable"},
            ],
            "physical_ssh_unavailable",
            project="德会",
            access={
                "device_reachable": True,
                "physical_ssh": False,
                "container_ssh": True,
                "access_mode": "direct_container",
            },
        )
    )
    assert result["deep_analysis_recommended"] is False
