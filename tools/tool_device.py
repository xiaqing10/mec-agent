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

    兼容入口：实际执行由 diagnose_mec.device_diagnosis 负责，不能由
    LLM 直接选择内部 SSH/Docker/进程检查步骤。
    """
    from diagnose_mec.device_diagnosis import run_device_diagnosis_collection
    return run_device_diagnosis_collection(ip, project)

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
                    count_cmd = f"find /home/files/nfsroot/{d} -maxdepth 1 -type f \\( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \\) 2>/dev/null | wc -l"
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
            retry=0,
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

