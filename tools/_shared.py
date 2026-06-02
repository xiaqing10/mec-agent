"""Shared state and helpers for tool modules."""

_diag_progress_callback = None


def set_diag_progress_callback(cb):
    global _diag_progress_callback
    _diag_progress_callback = cb


def _notify_progress(name, status, detail):
    cb = _diag_progress_callback
    if cb:
        cb(name, status, detail)


def _summarize_log_errors(log_errors: dict) -> str:
    parts = []
    for proc_name, info in log_errors.items():
        errors = info.get("errors", [])
        cat = info.get("error_category", "")
        display_name = "系统日志" if proc_name == "_system" else proc_name
        if cat == "driver":
            parts.append(f"{display_name}(驱动异常)")
        elif cat == "ros_master":
            parts.append(f"{display_name}(ROS连接失败)")
        elif cat == "oom":
            parts.append(f"{display_name}(OOM)")
        elif errors:
            parts.append(f"{display_name}({len(errors)}条错误)")
    return "; ".join(parts) if parts else ""


def _build_diag_result(ip, dims, root=""):
    import json
    from datetime import datetime

    has_e = any(d["status"] == "error" for d in dims)
    has_w = any(d["status"] == "warning" for d in dims)
    overall = "error" if has_e else ("warning" if has_w else "normal")

    diag_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    summary_parts = [f"设备 {ip} 诊断完毕（{'异常' if has_e else '需要关注' if has_w else '正常'}），{diag_time}"]
    if root:
        summary_parts.append(f"根因: {ROOT_CAUSE_CN.get(root, root)}")
    else:
        error_names = [d["name"] for d in dims if d["status"] == "error"]
        warning_names = [d["name"] for d in dims if d["status"] == "warning"]
        if error_names:
            summary_parts.append(f"异常维度: {', '.join(error_names)}")
        if warning_names:
            summary_parts.append(f"需要关注的维度: {', '.join(warning_names)}")

    ok_names = [d["name"] for d in dims if d["status"] == "ok"]
    if ok_names:
        summary_parts.append(f"正常维度: {', '.join(ok_names)}")

    skip_names = [d["name"] for d in dims if d["status"] == "skip"]
    if skip_names:
        summary_parts.append(f"未检查维度: {', '.join(skip_names)}（因上游不可达）")

    summary_for_llm = "\n".join(summary_parts)

    result = {
        "type": "diagnose_device_result",
        "ip": ip,
        "overall": overall,
        "root_cause": root,
        "diagnosis_time": diag_time,
        "dimensions": [],
        "summary_for_llm": summary_for_llm,
    }

    for d in dims:
        dim_entry = {
            "name": d["name"],
            "status": d["status"],
            "detail": d.get("detail", ""),
        }
        if d.get("problem"):
            dim_entry["problem"] = d["problem"]

        if d.get("_log_errors"):
            err_snippets = []
            for proc_name, err_info in d["_log_errors"].items():
                for e in err_info.get("errors", [])[:3]:
                    err_snippets.append(("  " + e) if isinstance(e, str) else ("  " + str(e)))
            if err_snippets:
                dim_entry["log_errors_detail"] = err_snippets[:5]

        if d.get("_topic_rates"):
            topic_items = []
            zero_set = set(d.get("_zero_topics", []))
            for t, r in d["_topic_rates"].items():
                item = f"{t}: {r}"
                topic_items.append({"topic": item, "is_zero": t in zero_set})
            dim_entry["topic_rates"] = topic_items

        result["dimensions"].append(dim_entry)

    return json.dumps(result, ensure_ascii=False)


ROOT_CAUSE_CN = {
    "ssh_unreachable": "SSH无法连接物理机 — 设备可能关机、断网或SSH服务未启动",
    "container_ssh_down": "容器SSH服务不可连接 — 容器内sshd服务未运行或端口未开放",
    "dev_container_missing": "dev容器不存在 — 容器被删除或未创建",
    "dev_container_stopped": "dev容器存在但未运行 — 容器已停止，需重启",
    "docker_service_down": "Docker服务未运行 — 物理机上Docker守护进程未启动",
    "container_exec_failed": "docker exec失败 — 容器状态异常，无法执行命令",
    "container_offline": "容器不可用 — 容器整体离线，无法进行后续诊断",
    "gpu_driver_error": "GPU驱动异常 — 推断进程FATAL，可能是显卡驱动问题或显存不足",
    "process_fatal": "进程FATAL — 关键进程异常退出，需检查进程日志",
    "process_error": "进程异常 — supervisor管理的进程存在异常状态",
    "process_log_error": "进程日志异常 — supervisor进程正常但日志中有错误输出",
    "ros_master_error": "ROS Master连接失败 — roscore无法连接或异常",
    "oom_error": "内存溢出(OOM) — 系统或进程内存不足",
    "roscore_down": "roscore未运行 — ROS主节点未启动",
    "zero_images": "今日图片为0 — 数据源无图片产生，可能相机/算法异常",
    "supervisor_error": "Supervisor服务异常 — 进程管理服务本身出现问题",
    "topic_all_zero": "所有ROS话题无数据 — 关键topic帧率为0",
    "topic_partial_zero": "部分ROS话题无数据 — 部分关键topic帧率为0",
    "log_error_only": "日志异常 — supervisor正常但日志中存在错误输出",
    "unknown": "未知根因 — 需人工进一步排查",
}