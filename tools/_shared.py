"""Shared state and helpers for tool modules.

Per-request diagnostic progress is stored in a ContextVar so concurrent
sessions cannot overwrite each other's callback.
"""

from contextvars import ContextVar
import json

_diag_progress_callback = ContextVar("diag_progress_callback", default=None)


def set_diag_progress_callback(cb):
    """Set the callback for the current async/task context and return a token."""
    return _diag_progress_callback.set(cb)


def reset_diag_progress_callback(token):
    """Restore the previous callback for the current async/task context."""
    _diag_progress_callback.reset(token)


def get_diag_progress_callback():
    return _diag_progress_callback.get()


def _notify_progress(name, status, detail):
    cb = _diag_progress_callback.get()
    if cb:
        try:
            cb(name, status, detail)
        except Exception:
            # Progress reporting must never break the actual diagnosis.
            pass


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


def _build_diag_result(ip, dims, root="", project="", access=None):
    """Build the canonical device diagnosis result while preserving UI fields."""
    from diagnose_mec.result_schema import build_domain_result, finalize_summary

    has_e = any(d.get("status") == "error" for d in dims)
    has_w = any(d.get("status") == "warning" for d in dims)

    # Prefer an explicitly supplied root cause; otherwise choose the first
    # actionable dimension in a stable priority order.
    priority = [
        "ssh_unreachable", "physical_unreachable",
        "docker_service_down", "dev_container_missing", "dev_container_stopped",
        "container_ssh_down", "container_exec_failed",
        "gpu_driver_error", "process_fatal", "supervisor_error",
        "process_error", "roscore_down", "ros_master_error",
        "topic_all_zero", "topic_partial_zero", "zero_images", "log_error_only",
    ]
    if not root:
        problems = [d.get("problem") for d in dims if d.get("problem")]
        root = next((p for p in priority if p in problems), problems[0] if problems else "")

    generic_root_causes = {
        "unknown", "process_error", "process_fatal", "supervisor_error",
        "roscore_down", "ros_master_error", "topic_all_zero",
        "topic_partial_zero", "log_error_only", "zero_images",
    }
    deep_analysis_recommended = bool(has_e and root in generic_root_causes)

    evidence = []
    symptoms = []
    recommendations = []
    for d in dims:
        if d.get("detail") and d.get("status") in {"error", "warning", "ok"}:
            evidence.append({
                "dimension": d.get("name", ""),
                "status": d.get("status", ""),
                "detail": str(d.get("detail", ""))[:500],
            })
        if d.get("status") in {"error", "warning"}:
            if d.get("name"):
                symptoms.append(d["name"])
        detail = str(d.get("detail", ""))
        if "建议" in detail or d.get("problem"):
            if detail:
                recommendations.append(detail[:300])

    overall = "error" if has_e else ("warning" if has_w else "normal")
    next_action = "deep_analysis" if deep_analysis_recommended else "report"
    if root in {"ambiguous_device", "device_not_found"}:
        next_action = "ask_user"
    elif root in {"physical_unreachable", "ssh_unreachable"}:
        next_action = "verify_access"

    result = build_domain_result(
        result_type="diagnose_device_result",
        ip=ip,
        project=project,
        overall=overall,
        root_cause=root,
        dimensions=[],
        evidence=evidence,
        symptoms=symptoms,
        recommendations=recommendations[:10],
        deep_analysis_recommended=deep_analysis_recommended,
        next_action=next_action,
        extra={"access": access or {}},
    )

    for d in dims:
        dim_entry = {
            "name": d.get("name", ""),
            "status": d.get("status", "warning"),
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
            zero_set = set(d.get("_zero_topics", []))
            dim_entry["topic_rates"] = [
                {"topic": f"{t}: {r}", "is_zero": t in zero_set}
                for t, r in d["_topic_rates"].items()
            ]
        result["dimensions"].append(dim_entry)

    return json.dumps(finalize_summary(result), ensure_ascii=False)




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
    "container_memory_exhaustion": "容器内存耗尽 — OOM导致进程/容器异常并可能引发下游数据中断",
    "device_unreachable": "设备整体不可达 — 当前没有可用的物理机或容器访问路径",
    "diagnosis_result_invalid": "诊断结果异常 — 采集完成但结果无法解析",
    "unknown": "未知根因 — 需人工进一步排查",
}