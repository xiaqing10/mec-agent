from request_router import route_request


def test_device_diagnosis_route():
    result = route_request("诊断设备 10.145.4.1")
    assert result["route"] == "device_diagnosis"
    assert result["explicit_ip"] == "10.145.4.1"


def test_project_diagnosis_route():
    result = route_request("诊断德会项目异常设备")
    assert result["route"] == "project_diagnosis"


def test_server_route():
    result = route_request("查询仙新路最近交通流量")
    assert result["route"] == "server_query"


def test_repair_route():
    result = route_request("重启设备容器")
    assert result["route"] == "repair"
    assert result["requires_confirmation"] is True
