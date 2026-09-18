from agent import extract_explicit_request_context


def test_extract_explicit_project_and_ip():
    project, ip = extract_explicit_request_context("诊断德会项目 10.145.4.1")
    assert project == "德会"
    assert ip == "10.145.4.1"


def test_extract_switch_project():
    project, ip = extract_explicit_request_context("切换到柯诸项目")
    assert project == "柯诸"
    assert ip == ""


def test_do_not_treat_current_project_as_explicit():
    project, ip = extract_explicit_request_context("查看这个项目状态")
    assert project == ""
    assert ip == ""
