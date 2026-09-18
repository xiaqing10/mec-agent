import json
import logging
import re
from datetime import datetime

from langchain_core.tools import tool

from ._shared import get_diag_progress_callback, _notify_progress, _summarize_log_errors, _build_diag_result

logger = logging.getLogger(__name__)


@tool
def mec_diagnose_device(ip: str, project: str = "") -> str:
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

    cont = diagnose_container_offline(
        ip, progress_cb=get_diag_progress_callback(), project=effective_project
    )
    cd = cont.get("diagnosis", {})

    ce = cd.get("error", "")

    if ce:
        _notify_progress("物理机", "warning", "物理机SSH未登录成功，尝试容器10022端口直连...")
        fallback_img = diagnose_zero_images(
            ip, container_ssh_info=None,
            progress_cb=get_diag_progress_callback(),
            project=effective_project
        )
        fallback_diag = fallback_img.get("diagnosis", {})
        container_access = fallback_diag.get("container_access", "unavailable")

        if container_access not in ("direct_ssh", "docker_exec"):
            _notify_progress("物理机", "error", ce[:80])
            dimensions.append({
                "name": "物理机", "status": "error", "detail": ce,
                "problem": "ssh_unreachable"
            })
            for dim_name in ["容器", "进程", "主题+日志", "今日事件数", "传感器"]:
                dimensions.append({
                    "name": dim_name, "status": "skip",
                    "detail": "物理机和容器SSH均未建立连接"
                })
            db_info = get_device_db_info(ip)
            db_detail = format_device_db_info(db_info)
            if db_detail:
                dimensions.append({"name": "数据库记录", "status": "warning", "detail": db_detail})
            if "Permission denied" in ce or "公钥" in ce:
                dimensions.append({
                    "name": "登录建议", "status": "warning",
                    "detail": "物理机SSH认证失败，但仍已尝试容器直连；可检查物理机SSH账号/密钥配置"
                })
            elif "超时" in ce or "Timeout" in ce.lower():
                dimensions.append({
                    "name": "网络建议", "status": "warning",
                    "detail": "物理机SSH超时，且容器直连也未建立；建议检查网络、端口和防火墙"
                })
            from ._diag_cache import cache_diag_data
            cache_diag_data(ip, {
                "physical_user": "", "login_method": "",
                "ssh_password": "", "unreachable": True
            })
            return _build_diag_result(ip, dimensions, "physical_unreachable", project=effective_project, access={"device_reachable": True, "physical_ssh": False, "container_ssh": True, "access_mode": "direct_container"})

        _notify_progress("物理机", "warning", "物理机SSH不可用，但容器SSH可直接访问，继续诊断")
        dimensions.append({
            "name": "物理机", "status": "warning",
            "detail": f"{ce}；但容器SSH（10022）可直接连接",
            "problem": "physical_ssh_unavailable"
        })
        dimensions.append({
            "name": "容器", "status": "ok",
            "detail": "容器SSH（10022）直连成功，已绕过物理机SSH继续诊断"
        })
        cd = {}
        pu = "物理机SSH不可用"
        disk_root = ""
        disk_data = ""
    else:
        pu = cd.get("physical_uptime", "未知")
    disk_root = cd.get("disk_root", "")
    disk_data = cd.get("disk_data", "")
    disk_detail_parts = [f"在线，运行 {pu}"]
    if disk_root:
        disk_detail_parts.append(f"/: {disk_root}")
    if disk_data:
        disk_detail_parts.append(f"/data: {disk_data}")
    _notify_progress("物理机", "ok", " | ".join(disk_detail_parts))
    dimensions.append({"name": "物理机", "status": "ok", "detail": " | ".join(disk_detail_parts)})

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
            "physical_user": cd.get("_login_user", ""),
            "login_method": cd.get("_login_method", ""),
            "ssh_password": cd.get("_ssh_password", ""),
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
        cache_diag_data(ip, {"physical_user": cd.get("_login_user", ""), "login_method": cd.get("_login_method", ""),
                              "ssh_password": cd.get("_ssh_password", ""), "docker_unavailable": True})
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
        cache_diag_data(ip, {"physical_user": cd.get("_login_user", ""), "login_method": cd.get("_login_method", ""),
                              "ssh_password": cd.get("_ssh_password", ""), "container_unreachable": True})
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
        "physical_user": cd.get("_login_user", ""),
        "login_method": cd.get("_login_method", ""),
        "ssh_password": cd.get("_ssh_password", ""),
        "container_ssh_info": cd.get("_container_ssh_info"),
        "project": effective_project,
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

    return _build_diag_result(ip, dimensions, root_cause if has_error else "", project=effective_project)


@tool
def mec_device_info(ip: str, info_type: str = "disk") -> str:
    """查询MEC设备详细指标。物理机SSH不可用时，优先使用已解析的容器访问路径。"""
    import json
    import re
    from diagnose_mec import (
        ssh_exec, _docker_exec_cmd, _resolve_device,
        CONTAINER_PORT, CONTAINER_USER,
    )
    from diagnose_mec.ssh import resolve_device_access
    from query_sensor_status import get_device_db_info, format_device_db_info

    if not ip:
        return json.dumps({"error": "未指定设备IP"}, ensure_ascii=False)

    if not re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
        resolved_ip, dev_info = _resolve_device(ip)
        if dev_info and dev_info.get("_ambiguous"):
            return json.dumps({
                "schema_version": "1.0",
                "type": "device_info_result",
                "status": "warning",
                "entity": {"ip": ip},
                "next_action": "ask_user",
                "error": f"设备 '{ip}' 存在多个匹配，请指定项目或IP",
            }, ensure_ascii=False)
        if resolved_ip != ip:
            ip = resolved_ip

    if not re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
        return json.dumps({"error": f"无法解析设备 '{ip}'"}, ensure_ascii=False)

    access = resolve_device_access(ip)
    physical_available = bool(access.get("physical_ssh"))
    access_mode = access.get("access_mode", "none")
    physical_user = access.get("physical_user", "")
    physical_password = access.get("ssh_password", "")
    container_password = access.get("container_password", "")

    if not access.get("device_reachable"):
        db_info = get_device_db_info(ip)
        db_detail = format_device_db_info(db_info)
        result = {
            "schema_version": "1.0",
            "type": "device_info_result",
            "status": "warning",
            "entity": {"ip": ip},
            "ip": ip,
            "next_action": "verify_access",
            "access_mode": access_mode,
            "info_type": info_type,
            "info": {},
            "error": f"设备 {ip} 没有可用访问路径",
        }
        if db_detail:
            result["database"] = db_detail
        return json.dumps(result, ensure_ascii=False)

    def run_physical(command: str, timeout: int = 8):
        if not physical_available:
            return "", "物理机SSH不可用", -1
        return ssh_exec(
            ip, 22, physical_user, command,
            exec_timeout=timeout, password=physical_password
        )

    def run_container(command: str, timeout: int = 8):
        if access_mode == "docker_exec":
            return _docker_exec_cmd(
                ip, physical_user, command,
                exec_timeout=timeout, password=physical_password
            )
        return ssh_exec(
            ip, CONTAINER_PORT, CONTAINER_USER, command,
            exec_timeout=timeout, password=container_password
        )

    types = [t.strip() for t in (info_type or "disk").split(",") if t.strip()]
    info = {
        "ip": ip,
        "info_type": info_type,
        "access_mode": access_mode,
        "physical_ssh": physical_available,
    }

    for t in types or ["disk"]:
        primary = run_physical if physical_available else run_container

        if t == "disk":
            out, _, _ = primary("df -h / /home 2>/dev/null || df -h /")
            info["disk" if physical_available else "disk_container"] = out.strip() or "无法获取"
            if physical_available:
                out2, _, _ = run_container("df -h / /home 2>/dev/null || df -h /")
                if out2.strip():
                    info["disk_container"] = out2.strip()

        elif t == "memory":
            out, _, _ = primary("free -h")
            info["memory" if physical_available else "memory_container"] = out.strip() or "无法获取"
            if physical_available:
                out2, _, _ = run_container("free -h")
                if out2.strip():
                    info["memory_container"] = out2.strip()

        elif t == "cpu":
            out, _, _ = primary("top -bn1 | head -5")
            info["cpu" if physical_available else "cpu_container"] = out.strip() or "无法获取"
            if physical_available:
                out2, _, _ = run_container("top -bn1 | head -5")
                if out2.strip():
                    info["cpu_container"] = out2.strip()

        elif t == "network":
            out, _, _ = primary("ip addr show | grep 'inet ' | awk '{print $2, $NF}'")
            info["network" if physical_available else "network_container"] = out.strip() or "无法获取"

        elif t == "uptime":
            out, _, _ = primary("uptime")
            info["uptime" if physical_available else "uptime_container"] = out.strip() or "无法获取"

        elif t == "history":
            cmd = "ls -d /home/files/nfsroot/20[0-9][0-9]-[0-9][0-9]-[0-9][0-9] 2>/dev/null | sort"
            out, _, _ = primary(cmd)
            if out.strip():
                dirs = [d.strip().split("/")[-1] for d in out.splitlines() if d.strip()]
                import datetime
                today_str = datetime.date.today().strftime("%Y-%m-%d")
                counts = []
                for d in dirs[:30]:
                    count_cmd = f"find /home/files/nfsroot/{d} -maxdepth 1 -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \) 2>/dev/null | wc -l"
                    cnt_out, _, _ = primary(count_cmd)
                    cnt = cnt_out.strip() or "0"
                    suffix = " (今天)" if d == today_str else ""
                    counts.append(f"{d}: {cnt} 张{suffix}")
                info["history"] = f"共 {len(dirs)} 天数据\n" + "\n".join(counts[-7:])
            else:
                info["history"] = "无历史数据目录"

    labels = {
        "disk": "硬盘(物理机)", "disk_container": "硬盘(容器)",
        "memory": "内存(物理机)", "memory_container": "内存(容器)",
        "cpu": "CPU(物理机)", "cpu_container": "CPU(容器)",
        "network": "网络(物理机)", "network_container": "网络(容器)",
        "uptime": "运行时间(物理机)", "uptime_container": "运行时间(容器)",
        "history": "历史数据",
    }
    result = {
        "schema_version": "1.0",
        "type": "device_info_result",
        "status": "normal",
        "entity": {"ip": ip},
        "ip": ip,
        "access_mode": access_mode,
        "physical_ssh": physical_available,
        "info_type": info_type,
        "metrics": {
            key: value for key, value in info.items()
            if key not in {"ip", "info_type", "access_mode", "physical_ssh"}
        },
        "summary_for_llm": "；".join(
            f"{labels.get(k, k)}: {str(v)[:200]}"
            for k, v in info.items()
            if k not in {"ip", "info_type", "access_mode", "physical_ssh"} and v
        ),
        "next_action": "report",
    }
    return json.dumps(result, ensure_ascii=False)


@tool
def mec_llm_diagnose_device(ip: str, project: str = "") -> str:
    """对单台MEC设备进行LLM深度分析。

    本工具只接收基础诊断已确认需要深度分析的场景；它复用统一设备访问路径，
    并通过统一 LLM Gateway 调用当前会话选择的模型。
    """
    from diagnose_mec import collect_device_raw_data, _resolve_device
    from project_history import save_diagnosis, load_project_records
    from query_sensor_status import get_device_db_info, format_device_db_info
    from llm_gateway import invoke_text

    if not ip:
        return json.dumps({"error": "未指定设备IP或设备名"}, ensure_ascii=False)

    device_info = None
    if not re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
        resolved_ip, device_info = _resolve_device(ip, project=project or None)
        if device_info and device_info.get("_ambiguous"):
            projects = "、".join(device_info.get("projects") or []) or "多个项目"
            return json.dumps({
                "schema_version": "1.0",
                "type": "deep_diagnosis_result",
                "status": "warning",
                "stage": "deep",
                "entity": {"ip": ip, "project": project},
                "ip": ip,
                "project": project,
                "root_cause": "ambiguous_device",
                "next_action": "ask_user",
                "error": f"设备名 '{ip}' 匹配到 {device_info.get('matches', 0)} 台设备（{projects}），未自动选择。",
                "analysis": "",
            }, ensure_ascii=False)
        if resolved_ip != ip:
            ip = resolved_ip

    if not re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
        msg = f"数据库中未找到设备 '{ip}'"
        if project:
            msg += f"（项目：{project}）"
        return json.dumps({
            "schema_version": "1.0",
            "type": "deep_diagnosis_result",
            "status": "warning",
            "stage": "deep",
            "entity": {"ip": ip, "project": project},
            "ip": ip,
            "project": project,
            "root_cause": "device_not_found",
            "next_action": "ask_user",
            "error": msg,
            "analysis": "",
        }, ensure_ascii=False)

    # Reuse the basic diagnosis cache when it is still fresh; otherwise collect once.
    from ._diag_cache import get_diag_cache
    cached = get_diag_cache(ip)
    cache_project = (cached or {}).get("project", "") if cached else ""
    if cached and cached.get("raw_data") and (not project or not cache_project or cache_project == project):
        raw_data = dict(cached.get("raw_data", {}))
        raw_data["device_reachable"] = not bool(cached.get("unreachable"))
        raw_data.setdefault("access_mode", "cached")
        raw_data.setdefault("physical_ssh", "已由基础诊断确认")
        raw_data.setdefault("container_ssh", "已由基础诊断确认")
        raw_result = {
            "host": ip,
            "timestamp": datetime.now().isoformat(),
            "raw_data": raw_data,
        }
    else:
        raw_result = collect_device_raw_data(ip, project=project)
        raw_data = raw_result.get("raw_data", {})
    reachable = bool(raw_data.get("device_reachable"))

    if not reachable:
        db_info = get_device_db_info(ip)
        db_detail = format_device_db_info(db_info)
        result = {
            "schema_version": "1.0",
            "type": "deep_diagnosis_result",
            "status": "warning",
            "stage": "deep",
            "entity": {"ip": ip, "project": project},
            "ip": ip,
            "project": project,
            "root_cause": "device_unreachable",
            "next_action": "verify_access",
            "analysis": "",
            "error": raw_data.get("error", "设备没有可用访问路径"),
            "evidence": [
                {"name": "physical_ssh", "value": raw_data.get("physical_ssh", "不可用")},
                {"name": "container_ssh", "value": raw_data.get("container_ssh", "不可用")},
                {"name": "access_mode", "value": raw_data.get("access_mode", "none")},
            ],
        }
        if db_detail:
            result["evidence"].append({"name": "database", "value": db_detail[:1000]})
        return json.dumps(result, ensure_ascii=False)

    device_project = (device_info or {}).get("project", "") or project
    device_name = (device_info or {}).get("name", "")
    hist_text = ""
    if device_info:
        save_diagnosis(device_project, device_name, ip, raw_result)
        hist = load_project_records(device_project)
        if hist and device_name in hist.get("devices", {}):
            records = hist["devices"][device_name].get("records", [])
            hist_text = "\n".join(
                f"{r.get('timestamp', '')}: {r.get('issue', '') or r.get('error', '正常')}"
                for r in records[-10:]
            )

    # Never send credentials or internal access metadata to the LLM.
    safe_items = {}
    for key, value in raw_data.items():
        key_lower = key.lower()
        if key.startswith("_") or any(token in key_lower for token in ("password", "credential", "secret", "token")):
            continue
        if key in {"physical_user"}:
            continue
        safe_items[key] = value

    raw_data_text = "\n".join(
        f"## {key}\n{json.dumps(value, ensure_ascii=False, indent=2) if isinstance(value, (dict, list)) else value}"
        for key, value in safe_items.items()
    )

    history_section = f"\n该设备历史诊断记录:\n{hist_text}\n" if hist_text else ""
    prompt = f"""设备IP: {ip}
项目: {device_project}
访问路径: {raw_data.get("access_mode", "unknown")}

以下是基础诊断后的结构化采集数据：
{raw_data_text}
{history_section}

请基于证据进行深度根因分析。只讨论数据支持的结论，并区分：
1. 根因；
2. 当前症状；
3. 受影响的业务；
4. 建议的处理顺序；
5. 仍缺失的证据。

不要把“物理机SSH不可用”自动等同于“设备离线”；如果容器可达，应以容器证据继续分析。
不要输出密码、密钥或内部认证信息。
请用中文、简洁而专业地回答。"""

    try:
        content = invoke_text(
            "你是一位资深MEC边缘计算运维专家，必须严格基于提供的诊断数据分析，不得编造事实。",
            prompt,
            timeout=45,
            max_tokens=4096,
            retry=1,
        )
        status = "normal" if content else "warning"
        result = {
            "schema_version": "1.0",
            "type": "deep_diagnosis_result",
            "status": status,
            "stage": "deep",
            "entity": {"ip": ip, "project": device_project},
            "ip": ip,
            "project": device_project,
            "root_cause": "llm_analysis",
            "root_cause_confidence": None,
            "next_action": "report",
            "deep_analysis_recommended": False,
            "analysis": content,
            "evidence": [],
            "symptoms": [],
            "impact": [],
            "recommendations": [],
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        logger.exception("LLM深度诊断失败: %s", exc)
        return json.dumps({
            "schema_version": "1.0",
            "type": "deep_diagnosis_result",
            "status": "warning",
            "stage": "deep",
            "entity": {"ip": ip, "project": device_project},
            "ip": ip,
            "project": device_project,
            "root_cause": "deep_analysis_failed",
            "next_action": "report",
            "deep_analysis_recommended": False,
            "analysis": "",
            "error": str(exc)[:500],
        }, ensure_ascii=False)

