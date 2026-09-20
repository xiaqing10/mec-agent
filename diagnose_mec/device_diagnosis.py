"""Deterministic single-device diagnosis collector.

This module contains the actual MEC diagnosis implementation. It is not a
LangChain Tool and must only be called by trusted diagnosis workflows.
"""
import json
import logging
import re
from datetime import datetime

from diagnose_mec._shared import (
    get_diag_progress_callback,
    _notify_progress,
    _summarize_log_errors,
    _build_diag_result,
)

logger = logging.getLogger(__name__)

def run_device_diagnosis_collection(ip: str, project: str = "") -> str:
    """诊断单台MEC设备。

    通过SSH远程检查设备的6个维度：物理机、容器、进程(含ROS)、主题+日志、今日事件数、传感器。

    Args:
        ip: 设备IP地址或设备名（如 mec_1002、zk26_690）
        project: 设备所属项目名（可选，用于设备名模糊匹配时缩小范围）
    """
    from diagnose_mec import diagnose_container_offline, diagnose_zero_images, _resolve_device
    from query_sensor_status import get_sensor_status, get_device_db_info, format_device_db_info

    if not ip:
        return json.dumps({"error": "未指定设备IP或设备名"}, ensure_ascii=False)

    dev_info = None
    if not re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
        resolved_ip, dev_info = _resolve_device(ip, project=project or None)
        if dev_info and dev_info.get("_ambiguous"):
            projects = "、".join(dev_info.get("projects") or []) or "多个项目"
            return json.dumps({
                "type": "diagnose_device_result",
                "ip": ip,
                "overall": "warning",
                "root_cause": "ambiguous_device",
                "diagnosis_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "dimensions": [{
                    "name": "设备解析",
                    "status": "warning",
                    "detail": f"设备名 '{ip}' 匹配到 {dev_info.get('matches', 0)} 台设备（{projects}），未自动选择。请指定项目或IP。"
                }],
                "summary_for_llm": f"设备名 '{ip}' 存在多个匹配（{projects}），为避免误诊未自动选择设备。请指定项目或IP。"
            }, ensure_ascii=False)
        if resolved_ip != ip:
            ip = resolved_ip

    effective_project = project or (dev_info.get("project", "") if dev_info else "")

    if not re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
        msg = f"数据库中未找到设备 '{ip}'"
        if project:
            msg += f"（项目：{project}）"
        msg += "，请检查设备名是否正确，或直接使用IP地址"
        return json.dumps({"error": msg}, ensure_ascii=False)

    dimensions = []
    fallback_img = None

    # 一个请求只解析一次访问路径，后续诊断全部复用该结果。
    from diagnose_mec.ssh import resolve_device_access
    access = resolve_device_access(ip)
    physical_available = bool(access.get("physical_ssh"))
    container_available = bool(access.get("container_ssh") or access.get("docker_exec"))

    if not access.get("device_reachable"):
        detail = access.get("error", "设备没有可用访问路径")
        dimensions.append({
            "name": "可达性", "status": "error",
            "detail": detail, "problem": "device_unreachable",
        })
        dimensions.append({
            "name": "物理机SSH", "status": "error",
            "detail": "不可用", "problem": "physical_ssh_unavailable",
        })
        dimensions.append({
            "name": "容器访问", "status": "error",
            "detail": "10022与docker exec均不可用",
            "problem": "container_unreachable",
        })
        db_info = get_device_db_info(ip)
        db_detail = format_device_db_info(db_info)
        if db_detail:
            dimensions.append({"name": "数据库记录", "status": "warning", "detail": db_detail})
        from ._diag_cache import cache_diag_data
        cache_diag_data(ip, {
            "project": effective_project,
            "unreachable": True,
            "device_reachable": False,
            "physical_ssh": False,
            "container_ssh": False,
            "access_mode": "none",
        })
        return _build_diag_result(
            ip, dimensions, "device_unreachable",
            project=effective_project,
            access={
                "device_reachable": False,
                "physical_ssh": False,
                "container_ssh": False,
                "access_mode": "none",
            },
        )

    # 物理机可达：使用现有物理机/Docker诊断链。
    if physical_available:
        cont = diagnose_container_offline(
            ip, progress_cb=get_diag_progress_callback(), project=effective_project
        )
        cd = cont.get("diagnosis", {})
    else:
        # 物理机不可达但容器可达：直接进入容器诊断，绝不把物理SSH故障当成设备离线。
        if access.get("docker_exec"):
            container_ssh_info = {
                "method": "docker_exec",
                "login_user": access.get("physical_user", ""),
                "ssh_password": access.get("ssh_password", ""),
            }
        elif access.get("container_password"):
            container_ssh_info = {
                "method": "direct_password",
                "password": access.get("container_password", ""),
            }
        else:
            container_ssh_info = {"method": "direct_key"}

        fallback_img = diagnose_zero_images(
            ip,
            container_ssh_info=container_ssh_info,
            progress_cb=get_diag_progress_callback(),
            project=effective_project,
        )
        cd = {}

    ce = cd.get("error", "")
    if ce and physical_available:
        # 这里只处理物理机自身诊断失败；真正的设备不可达已在统一访问解析阶段决定。
        dimensions.append({
            "name": "物理机", "status": "error",
            "detail": ce, "problem": "physical_diagnostic_error",
        })
        _notify_progress("物理机", "error", ce[:100])

    disk_root = cd.get("disk_root", "") if physical_available else ""
    disk_data = cd.get("disk_data", "") if physical_available else ""
    pu = cd.get("physical_uptime", "未知") if physical_available else ""

    if physical_available:
        disk_detail_parts = [f"在线，运行 {pu}"]
        if disk_root:
            disk_detail_parts.append(f"/: {disk_root}")
        if disk_data:
            disk_detail_parts.append(f"/data: {disk_data}")
        physical_detail = " | ".join(disk_detail_parts)
        if not any(d.get("name") == "物理机" for d in dimensions):
            dimensions.append({"name": "物理机", "status": "ok", "detail": physical_detail})
            _notify_progress("物理机", "ok", physical_detail)
    else:
        physical_detail = "物理机SSH不可用，但设备/容器访问正常；不判定设备离线"
        dimensions.append({
            "name": "物理机", "status": "warning",
            "detail": physical_detail, "problem": "physical_ssh_unavailable",
        })
        _notify_progress("物理机", "warning", physical_detail)
        dimensions.append({
            "name": "容器访问", "status": "ok",
            "detail": f"访问方式: {access.get('access_mode', 'container')}",
        })

    # 容器维度：docker ps 状态 + 容器SSH连通性
    cs = cd.get("container_status", "")
    cst = cd.get("container_started", "")
    dev_cont = cd.get("dev_container", "")
    issue_text = cd.get("issue", "")
    docker_status = cd.get("docker_service", "")

    docker_ok = "运行中" in (docker_status or "") or (cs and "Up" in cs)
    docker_running = cs or ("运行中" if docker_ok else "未运行")

    container_ok = False

    # Explicitly stopped state takes precedence over a generic status string.
    if dev_cont and "未运行" in dev_cont:
        problem, detail = "dev_container_stopped", f"dev容器存在但未运行（{dev_cont}）"
        _notify_progress("容器", "error", detail)
        dimensions.append({"name": "容器", "status": "error", "detail": detail, "problem": problem})
        for dim_name in ["进程", "主题+日志", "今日事件数"]:
            dimensions.append({"name": dim_name, "status": "skip", "detail": "容器不可达，跳过"})
        si = get_sensor_status(ip, effective_project)
        if si and (si.get("cameras") or si.get("radars")):
            cam, rad = si.get("total_cameras", 0), si.get("total_radars", 0)
            cam_off, rad_off = si.get("offline_cameras", 0), si.get("offline_radars", 0)
            sensor_detail = f"摄像头 {cam - cam_off}/{cam}, 雷达 {rad - rad_off}/{rad}"
            sensor_status = "warning" if (cam_off > 0 or rad_off > 0) else "ok"
            dimensions.append({"name": "传感器", "status": sensor_status, "detail": sensor_detail})
        else:
            dimensions.append({"name": "传感器", "status": "skip", "detail": "无传感器数据"})
        db_info = get_device_db_info(ip)
        db_detail = format_device_db_info(db_info)
        if db_detail:
            dimensions.append({"name": "数据库记录", "status": "warning", "detail": db_detail})
        from ._diag_cache import cache_diag_data
        cache_diag_data(ip, {
            "project": effective_project,
            "device_reachable": True,
            "physical_ssh": physical_available,
            "container_ssh": container_available,
            "access_mode": access.get("access_mode", "none"),
            "container_unreachable": True,
        })
        return _build_diag_result(ip, dimensions, problem, project=effective_project)

    if cs:
        container_detail = f"dev容器: {cs}"
        if cst:
            container_detail += f"，启动于 {cst[:10]} {cst[11:16]}"
        container_ok = True
        container_detail += " | SSH: 待验证（阶段3探测）"
        _notify_progress("容器", "ok", container_detail)
        dimensions.append({"name": "容器", "status": "ok", "detail": container_detail})
    elif "Docker" in (issue_text or "") or (dev_cont and "Docker 不可用" in dev_cont):
        problem, detail = "docker_service_down", "Docker服务未运行"
        _notify_progress("容器", "error", detail)
        dimensions.append({"name": "容器", "status": "error", "detail": detail, "problem": problem})
        for dim_name in ["进程", "主题+日志", "今日事件数"]:
            dimensions.append({"name": dim_name, "status": "skip", "detail": "Docker不可用，跳过"})
        si = get_sensor_status(ip, effective_project)
        if si and (si.get("cameras") or si.get("radars")):
            cam, rad = si.get("total_cameras", 0), si.get("total_radars", 0)
            cam_off, rad_off = si.get("offline_cameras", 0), si.get("offline_radars", 0)
            sensor_detail = f"摄像头 {cam - cam_off}/{cam}, 雷达 {rad - rad_off}/{rad}"
            sensor_status = "warning" if (cam_off > 0 or rad_off > 0) else "ok"
            dimensions.append({"name": "传感器", "status": sensor_status, "detail": sensor_detail})
        else:
            dimensions.append({"name": "传感器", "status": "skip", "detail": "无传感器数据"})
        db_info = get_device_db_info(ip)
        db_detail = format_device_db_info(db_info)
        if db_detail:
            dimensions.append({"name": "数据库记录", "status": "warning", "detail": db_detail})
        from ._diag_cache import cache_diag_data
        cache_diag_data(ip, {
            "project": effective_project,
            "device_reachable": True,
            "physical_ssh": physical_available,
            "container_ssh": container_available,
            "access_mode": access.get("access_mode", "none"),
            "container_unreachable": True,
        })
        return _build_diag_result(ip, dimensions, problem, project=effective_project)
    elif dev_cont and "不存在" in dev_cont:
        if "不存在" in dev_cont:
            problem, detail = "dev_container_missing", f"dev容器不存在"
        else:
            problem, detail = "dev_container_stopped", f"dev容器存在但未运行（{dev_cont}）"
        _notify_progress("容器", "error", detail)
        dimensions.append({"name": "容器", "status": "error", "detail": detail, "problem": problem})
        for dim_name in ["进程", "主题+日志", "今日事件数"]:
            dimensions.append({"name": dim_name, "status": "skip", "detail": "容器不可达，跳过"})
        si = get_sensor_status(ip, effective_project)
        if si and (si.get("cameras") or si.get("radars")):
            cam, rad = si.get("total_cameras", 0), si.get("total_radars", 0)
            cam_off, rad_off = si.get("offline_cameras", 0), si.get("offline_radars", 0)
            sensor_detail = f"摄像头 {cam - cam_off}/{cam}, 雷达 {rad - rad_off}/{rad}"
            sensor_status = "warning" if (cam_off > 0 or rad_off > 0) else "ok"
            dimensions.append({"name": "传感器", "status": sensor_status, "detail": sensor_detail})
        else:
            dimensions.append({"name": "传感器", "status": "skip", "detail": "无传感器数据"})
        db_info = get_device_db_info(ip)
        db_detail = format_device_db_info(db_info)
        if db_detail:
            dimensions.append({"name": "数据库记录", "status": "warning", "detail": db_detail})
        from ._diag_cache import cache_diag_data
        cache_diag_data(ip, {
            "project": effective_project,
            "device_reachable": True,
            "physical_ssh": physical_available,
            "container_ssh": container_available,
            "access_mode": access.get("access_mode", "none"),
            "container_unreachable": True,
        })
        return _build_diag_result(ip, dimensions, problem, project=effective_project)
    elif "Docker" in (issue_text or ""):
        problem, detail = "docker_service_down", "Docker服务未运行"
        _notify_progress("容器", "error", detail)
        dimensions.append({"name": "容器", "status": "error", "detail": detail, "problem": problem})
        for dim_name in ["进程", "主题+日志", "今日事件数"]:
            dimensions.append({"name": dim_name, "status": "skip", "detail": "Docker不可用，跳过"})
        si = get_sensor_status(ip, effective_project)
        if si and (si.get("cameras") or si.get("radars")):
            cam, rad = si.get("total_cameras", 0), si.get("total_radars", 0)
            cam_off, rad_off = si.get("offline_cameras", 0), si.get("offline_radars", 0)
            sensor_detail = f"摄像头 {cam - cam_off}/{cam}, 雷达 {rad - rad_off}/{rad}"
            sensor_status = "warning" if (cam_off > 0 or rad_off > 0) else "ok"
            dimensions.append({"name": "传感器", "status": sensor_status, "detail": sensor_detail})
        else:
            dimensions.append({"name": "传感器", "status": "skip", "detail": "无传感器数据"})
        db_info = get_device_db_info(ip)
        db_detail = format_device_db_info(db_info)
        if db_detail:
            dimensions.append({"name": "数据库记录", "status": "warning", "detail": db_detail})
        from ._diag_cache import cache_diag_data
        cache_diag_data(ip, {"physical_user": cd.get("_login_user", ""), "login_method": cd.get("_login_method", ""),
                              "ssh_password": cd.get("_ssh_password", ""), "docker_unavailable": True})
        return _build_diag_result(ip, dimensions, problem, project=effective_project)

    # 容器存在且SSH可达，或前面已通过容器直连完成诊断
    container_ssh_info = cd.get("_container_ssh_info") if cd else None
    img = fallback_img or diagnose_zero_images(
        ip,
        container_ssh_info=container_ssh_info,
        progress_cb=get_diag_progress_callback(),
        project=effective_project
    )
    iz = img.get("diagnosis", {})
    ic = iz.get("today_image_count", -1)

    sv = iz.get("supervisor", {})
    abnormals = iz.get("abnormal_processes", [])
    sv_raw = iz.get("supervisor_output", "")
    log_errors = iz.get("log_errors", {})
    roscore = iz.get("roscore", "")
    topic_rates = iz.get("topic_rates", {})
    rostopic = iz.get("rostopic", "")
    container_fs_readonly = iz.get("container_filesystem_readonly")
    container_fs_detail = iz.get("container_filesystem", "无法判断")

    # 容器文件系统检查位于进程诊断之前；发现只读后仍继续进程/ROS检查，保留完整证据。
    if container_fs_readonly is True:
        fs_status = "error"
        fs_problem = "container_filesystem_readonly"
        fs_detail = f"容器根文件系统为只读（{container_fs_detail}）"
    elif container_fs_readonly is False:
        fs_status = "ok"
        fs_problem = None
        fs_detail = f"容器根文件系统可写（{container_fs_detail}）"
    else:
        fs_status = "warning"
        fs_problem = "container_filesystem_unknown"
        fs_detail = "无法判断容器根文件系统读写状态"
    dimensions.append({
        "name": "容器文件系统",
        "status": fs_status,
        "detail": fs_detail,
        **({"problem": fs_problem} if fs_problem else {}),
    })
    _notify_progress("容器文件系统", fs_status, fs_detail)

    # 进程维度：supervisorctl status + roscore
    proc_parts = []
    proc_problem = None
    if abnormals:
        for ap in abnormals:
            status = ap.get("status", "")
            name = ap.get("name", "")
            uptime = ap.get("uptime", "")
            if status == "FREQ_RESTART":
                proc_parts.append(f"{name}(频繁重启,uptime={uptime})")
            else:
                proc_parts.append(f"{name}({status})")
        fatal_names = [p["name"] for p in abnormals if p["status"] == "FATAL"]
        if fatal_names and any(n == "infer" for n in fatal_names):
            proc_problem = "gpu_driver_error" if iz.get("error_category") == "driver" else "process_fatal"
        else:
            proc_problem = "process_error"
        proc_detail = "; ".join(proc_parts)
    elif isinstance(sv, dict) and sv.get("total", 0) > 0:
        proc_detail = f"{sv.get('running',0)}/{sv.get('total',0)} 运行正常"
    elif isinstance(sv, str) and "异常" in sv:
        proc_detail = "Supervisor服务异常"
        proc_problem = "supervisor_error"
    else:
        proc_detail = "未获取到进程状态"

    # roscore 状态：结合 ROS topic 数据辅助判断
    roscore_running = False
    if roscore and "未运行" not in roscore:
        roscore_running = True
    elif topic_rates:
        logger.info("supervisorctl无输出但ROS topic有数据，判定roscore实际运行中")
        roscore_running = True

    if abnormals:
        final_proc_detail = proc_detail
        if roscore_running:
            final_proc_detail += " | roscore运行"
        else:
            final_proc_detail += " | roscore未运行"
            if not proc_problem:
                proc_problem = "roscore_down"
        dimensions.append({"name": "进程(supervisorctl+roscore)", "status": "error", "detail": final_proc_detail,
                           "problem": proc_problem, "supervisor_raw": sv_raw, "_log_errors": log_errors})
        _notify_progress("进程(supervisorctl+roscore)", "error", final_proc_detail)
    elif not roscore_running:
        final_detail = f"{proc_detail} | roscore未运行"
        dimensions.append({"name": "进程(supervisorctl+roscore)", "status": "error", "detail": final_detail,
                           "problem": "roscore_down", "supervisor_raw": sv_raw, "_log_errors": log_errors})
        _notify_progress("进程(supervisorctl+roscore)", "error", final_detail)
    elif isinstance(sv, dict) and sv.get("total", 0) > 0:
        final_detail = f"{proc_detail} | roscore运行"
        dimensions.append({"name": "进程(supervisorctl+roscore)", "status": "ok", "detail": final_detail,
                           "supervisor_raw": sv_raw, "_log_errors": log_errors})
        _notify_progress("进程(supervisorctl+roscore)", "ok", final_detail)
    elif isinstance(sv, str) and "异常" in sv:
        final_detail = f"{proc_detail} | roscore运行"
        if topic_rates:
            dimensions.append({"name": "进程(supervisorctl+roscore)", "status": "warning", "detail": final_detail,
                               "supervisor_raw": sv_raw, "_log_errors": log_errors})
            _notify_progress("进程(supervisorctl+roscore)", "warning", final_detail)
        else:
            dimensions.append({"name": "进程(supervisorctl+roscore)", "status": "error", "detail": final_detail,
                               "problem": "supervisor_error", "supervisor_raw": sv_raw, "_log_errors": log_errors})
            _notify_progress("进程(supervisorctl+roscore)", "error", final_detail)
    else:
        final_detail = f"{proc_detail} | roscore运行" if roscore_running else f"{proc_detail} | roscore未运行"
        dimensions.append({"name": "进程(supervisorctl+roscore)", "status": "warning", "detail": final_detail,
                           "supervisor_raw": sv_raw, "_log_errors": log_errors})
        _notify_progress("进程(supervisorctl+roscore)", "warning", final_detail)

    # 主题+日志维度：rostopic hz + log错误分析
    topic_log_parts = []
    topic_log_problem = None
    has_topic_issue = False

    if topic_rates:
        zero_topics = [t for t, r in topic_rates.items() if r.startswith("0 Hz") or "无数据" in r]
        for t, r in topic_rates.items():
            topic_log_parts.append(f"{t}: {r}")
        if len(zero_topics) == len(topic_rates) and topic_rates:
            has_topic_issue = True
            topic_log_problem = "topic_all_zero"
        elif zero_topics:
            has_topic_issue = True
            topic_log_problem = "topic_partial_zero"
    elif rostopic:
        topic_log_parts.append(f"rostopic: {rostopic}")
        has_topic_issue = True
        topic_log_problem = "topic_error"

    if log_errors:
        log_summary = _summarize_log_errors(log_errors)
        if log_summary:
            topic_log_parts.append(f"日志: {log_summary}")
            if not has_topic_issue:
                has_topic_issue = True
                topic_log_problem = "log_error_only"

    if topic_log_parts:
        topic_log_detail = " | ".join(topic_log_parts)
        if has_topic_issue:
            tl_status = "error" if topic_log_problem in ("topic_all_zero", "topic_error") else "warning"
            dimensions.append({"name": "主题+日志", "status": tl_status, "detail": topic_log_detail,
                               "problem": topic_log_problem, "_topic_rates": topic_rates,
                               "_zero_topics": zero_topics if topic_rates else [], "_log_errors": log_errors})
            _notify_progress("主题+日志", tl_status, topic_log_detail[:80])
        else:
            dimensions.append({"name": "主题+日志", "status": "ok", "detail": topic_log_detail,
                               "_topic_rates": topic_rates, "_log_errors": log_errors})
            _notify_progress("主题+日志", "ok", topic_log_detail[:80])
    elif not abnormals:
        if roscore_running:
            dimensions.append({"name": "主题+日志", "status": "ok", "detail": "无关键topic，roscore运行"})
            _notify_progress("主题+日志", "ok", "无关键topic")
        else:
            dimensions.append({"name": "主题+日志", "status": "skip", "detail": "roscore未运行，跳过"})
            _notify_progress("主题+日志", "skip", "roscore未运行")
    else:
        dimensions.append({"name": "主题+日志", "status": "skip", "detail": "进程异常，跳过"})
        _notify_progress("主题+日志", "skip", "进程异常")

    # 今日事件数维度
    latest_time = iz.get("latest_image_time", "")
    if ic > 0:
        dim5 = f"今日图片: {ic} 张"
        if latest_time:
            dim5 += f"，最新 {latest_time}"
        dimensions.append({"name": "今日事件数", "status": "ok", "detail": dim5})
        _notify_progress("今日事件数", "ok", dim5)
    elif ic == 0:
        dimensions.append({"name": "今日事件数", "status": "error", "detail": "今日图片: 0 张", "problem": "zero_images"})
        _notify_progress("今日事件数", "error", "今日图片: 0 张")
    else:
        dimensions.append({"name": "今日事件数", "status": "warning", "detail": "无法获取图片数"})
        _notify_progress("今日事件数", "warning", "无法获取图片数")

    si = get_sensor_status(ip, effective_project)
    if si and (si.get("cameras") or si.get("radars")):
        cam, rad = si.get("total_cameras", 0), si.get("total_radars", 0)
        cam_off, rad_off = si.get("offline_cameras", 0), si.get("offline_radars", 0)
        parts = []
        if cam > 0:
            parts.append(f"摄像头 {cam - cam_off}/{cam}")
        if rad > 0:
            parts.append(f"雷达 {rad - rad_off}/{rad}")
        has_problem = cam_off > 0 or rad_off > 0
        sensor_detail = "在线" if not has_problem else "部分离线"
        sensor_detail += " (" + ", ".join(parts) + ")"
        sensor_status = "warning" if has_problem else "ok"
        dimensions.append({"name": "传感器", "status": sensor_status, "detail": sensor_detail})
        _notify_progress("传感器", sensor_status, sensor_detail)
    else:
        dimensions.append({"name": "传感器", "status": "skip", "detail": "无传感器数据"})
        _notify_progress("传感器", "skip", "无传感器数据")

    has_error = any(d["status"] == "error" for d in dimensions)
    has_warning = any(d["status"] == "warning" for d in dimensions)
    overall_status = "error" if has_error else ("warning" if has_warning else "normal")

    error_dims = [d for d in dimensions if d["status"] == "error"]
    if error_dims:
        cause_priority = {
            "ssh_unreachable": 100,
            "docker_service_down": 90,
            "dev_container_missing": 85,
            "container_filesystem_readonly": 82,
            "dev_container_stopped": 85,
            "container_exec_failed": 80,
            "container_ssh_down": 75,
            "gpu_driver_error": 70,
            "process_fatal": 65,
            "supervisor_error": 60,
            "roscore_down": 55,
            "process_error": 50,
            "ros_master_error": 45,
            "topic_all_zero": 30,
            "topic_partial_zero": 25,
            "zero_images": 20,
            "log_error_only": 10,
        }
        root_dim = max(error_dims, key=lambda d: cause_priority.get(d.get("problem", ""), 1))
        root_cause = root_dim.get("problem", "unknown")
        summary = "异常 - " + "; ".join(f"{d['name']}: {d['detail']}" for d in error_dims)
    elif has_warning:
        warn_dims = [d for d in dimensions if d["status"] == "warning"]
        summary = "注意 - " + "; ".join(d['detail'] for d in warn_dims)
    else:
        summary = "正常运行"

    if dev_info and not dev_info.get("_ambiguous"):
        from project_history import save_diagnosis
        save_diagnosis(dev_info.get("project", ""), dev_info.get("name", ""), ip, img)

    from ._diag_cache import cache_diag_data
    cache_diag_data(ip, {
        "project": effective_project,
        "device_reachable": True,
        "physical_ssh": physical_available,
        "container_ssh": container_available,
        "access_mode": access.get("access_mode", "none"),
        "physical_uptime": pu,
        "container_status": cs,
        "raw_data": {
            "supervisor_output": sv_raw,
            "abnormal_processes": abnormals,
            "log_errors": log_errors,
            "roscore": roscore,
            "topic_rates": topic_rates,
            "today_image_count": ic,
        },
    })

    return _build_diag_result(
        ip,
        dimensions,
        root_cause if has_error else "",
        project=effective_project,
        access={
            "device_reachable": bool(access.get("device_reachable")),
            "physical_ssh": bool(access.get("physical_ssh")),
            "container_ssh": bool(access.get("container_ssh")),
            "docker_exec": bool(access.get("docker_exec")),
            "container_filesystem_readonly": container_fs_readonly,
            "access_mode": access.get("access_mode", "none"),
        },
    )



__all__ = ["run_device_diagnosis_collection"]
