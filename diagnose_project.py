#!/usr/bin/env python3
"""
MEC项目诊断模块 - 对指定项目进行设备诊断

用法:
  from diagnose_project import diagnose_project
  result = diagnose_project("德会")
"""
import sys
import json
import time
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

SELF_AGENT_DIR = Path(__file__).parent
LLM_PENDING_DIR = SELF_AGENT_DIR / "diagnose_logs" / "llm_pending"


def fetch_mec_report_from_feishu():
    sys.path.insert(0, str(SELF_AGENT_DIR))
    from mec_analyze import fetch_latest_mec_message, extract_timestamp
    report_text, error = fetch_latest_mec_message()
    if error:
        return None, error
    if not report_text:
        return None, "未找到报告"
    timestamp = extract_timestamp(report_text)
    return report_text, None


def parse_abnormal_devices(report_text, project_name=None):
    container_offline = []
    zero_images = []
    physical_offline = []

    project_pattern = re.compile(r'\U0001f4c1\s*\*\*项目:\s*(.+?)\*\*')
    found_projects = project_pattern.findall(report_text)

    if not found_projects:
        if project_name:
            alt_pattern = re.compile(rf'\*\*项目:\s*{re.escape(project_name)}\*\*')
            if alt_pattern.search(report_text):
                found_projects = [project_name]
        if not found_projects:
            return container_offline, zero_images, physical_offline

    for proj_name in found_projects:
        # Respect an explicitly requested project; the previous code compared
        # project_name with itself and therefore never filtered anything.
        if project_name and proj_name != project_name:
            continue

        project_marker = f'\U0001f4c1 **项目: {proj_name}**'
        if project_marker not in report_text:
            project_marker = f'**项目: {proj_name}**'
        idx = report_text.find(project_marker)
        if idx == -1:
            continue

        next_idx = len(report_text)
        for other_project in found_projects:
            if other_project != proj_name:
                marker = f'\U0001f4c1 **项目: {other_project}**'
                pos = report_text.find(marker, idx + 1)
                if pos != -1 and pos < next_idx:
                    next_idx = pos

        content = report_text[idx:next_idx]
        lines = content.split('\n')

        current_section = None

        for line in lines:
            if '**物理机**' in line or '物理机**: ' in line:
                current_section = 'physical'
            elif '**容器在线**' in line or '容器在线**: ' in line or '**容器**' in line:
                current_section = 'container'

            if '物理机在线但容器不可连' in line:
                devices = _parse_json_devices(line)
                for device in devices:
                    device['project'] = proj_name
                    device['diag_type'] = 'container_offline'
                    container_offline.append(device)

            elif '容器在线但今日图片为0' in line:
                devices = _parse_json_devices(line)
                for device in devices:
                    device['project'] = proj_name
                    device['diag_type'] = 'zero_images'
                    zero_images.append(device)

            elif '离线' in line and '[' in line and '"name"' in line and current_section == 'physical':
                devices = _parse_json_devices(line)
                for device in devices:
                    device['project'] = proj_name
                    device['diag_type'] = 'physical_offline'
                    physical_offline.append(device)

    def dedup(device_list):
        seen_ips = set()
        result = []
        for device in device_list:
            ip = device.get('ip', '')
            if ip not in seen_ips:
                seen_ips.add(ip)
                result.append(device)
        return result

    container_offline = dedup(container_offline)
    zero_images = dedup(zero_images)
    physical_offline = dedup(physical_offline)

    physical_offline_ips = {d['ip'] for d in physical_offline}
    container_offline = [d for d in container_offline if d['ip'] not in physical_offline_ips]
    zero_images = [d for d in zero_images if d['ip'] not in physical_offline_ips]

    return container_offline, zero_images, physical_offline


def _parse_json_devices(line):
    json_start = line.find('[')
    json_end = line.rfind(']')
    if json_start == -1 or json_end == -1:
        return []
    try:
        json_str = line[json_start:json_end+1]
        devices = json.loads(json_str)
        result = []
        for device in devices:
            name = device.get('name', '')
            ip_field = device.get('ip', '')
            ip_match = re.search(r'\[(\d+\.\d+\.\d+\.\d+)\]', ip_field)
            ip = ip_match.group(1) if ip_match else ip_field.replace('[', '').replace(']', '').split('http')[0].strip()
            if name and ip:
                result.append({'name': name, 'ip': ip})
        return result
    except Exception:
        return []


def diagnose_device(device_info):
    """Run the exact same single-device pipeline as the interactive Agent."""
    from tools import mec_diagnose_device, mec_llm_diagnose_device
    from diagnosis_router import route_device_result, parse_result

    device_name = device_info.get("name", "")
    ip = device_info.get("ip", "")
    project = device_info.get("project", "")

    if not ip:
        return {
            "schema_version": "1.0", "type": "diagnose_device_result",
            "status": "warning", "stage": "basic",
            "entity": {"ip": "", "project": project},
            "ip": "", "project": project, "root_cause": "missing_ip",
            "next_action": "ask_user", "deep_analysis_recommended": False,
            "error": "IP地址为空", "device_name": device_name,
        }

    try:
        raw = mec_diagnose_device.invoke({"ip": ip, "project": project})
        result = parse_result(raw)
        if not result:
            result = {
                "schema_version": "1.0", "type": "diagnose_device_result",
                "status": "warning", "stage": "basic",
                "entity": {"ip": ip, "project": project},
                "ip": ip, "project": project,
                "root_cause": "invalid_tool_result",
                "next_action": "report", "deep_analysis_recommended": False,
                "error": "mec_diagnose_device 返回了无法解析的结果",
            }
    except Exception as exc:
        result = {
            "schema_version": "1.0", "type": "diagnose_device_result",
            "status": "warning", "stage": "basic",
            "entity": {"ip": ip, "project": project},
            "ip": ip, "project": project,
            "root_cause": "diagnosis_execution_failed",
            "next_action": "report", "deep_analysis_recommended": False,
            "error": str(exc)[:500],
        }

    result["device_name"] = device_name
    result["project"] = result.get("project") or project
    result["entity"] = {"ip": result.get("ip") or ip, "project": result.get("project") or project}

    return route_device_result(
        result,
        deep_analysis_invoke=lambda target_ip, target_project: mec_llm_diagnose_device.invoke({
            "ip": target_ip, "project": target_project
        }),
    )
def build_dingtalk_message(results, project_name):
    """Render canonical device diagnosis results for the project report."""
    message = f"## 设备诊断-项目: {project_name}\\n\\n"
    message += f"**诊断时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\\n\\n"
    if not results:
        return message + "项目当前无异常设备，无需诊断。"

    for r in results:
        ip = r.get("ip", r.get("entity", {}).get("ip", ""))
        name = r.get("device_name", "未知")
        message += f"### {name} ({ip})\\n"
        message += f"- 状态: {r.get("status", r.get("overall", "warning"))}\\n"
        if r.get("root_cause"):
            message += f"- 根因: {r["root_cause"]}\\n"
        for item in (r.get("evidence") or [])[:5]:
            if isinstance(item, dict):
                label = item.get("dimension", item.get("name", "证据"))
                detail = item.get("detail", item.get("value", ""))
                message += f"- 证据: {label} — {detail}\\n"
        deep = r.get("deep_analysis")
        if isinstance(deep, dict) and deep.get("analysis"):
            analysis = str(deep["analysis"]).replace("\\n", " ").strip()
            message += f"- 深度分析: {analysis[:500]}\\n"
        if r.get("error"):
            message += f"- 错误: {str(r["error"])[:300]}\\n"
        message += "\\n"
    return message
def diagnose_project(project_name):
    """诊断指定项目的所有异常设备。

    Args:
        project_name: 项目名称（如 "德会"）

    Returns:
        dict: {
            "success": bool,
            "project": project_name,
            "total_diagnosed": int,
            "container_offline": int,
            "zero_images": int,
            "need_llm": int,
            "results": [diagnosis_result, ...],
            "dingtalk_message": str,
            "error": str | None
        }
    """
    result_summary = {
        "success": False,
        "project": project_name,
        "total_diagnosed": 0,
        "container_offline": 0,
        "zero_images": 0,
        "need_llm": 0,
        "results": [],
        "error": None
    }

    sys.path.insert(0, str(SELF_AGENT_DIR))

    # 优先从数据库获取项目异常设备
    from tools.tool_db import query_mec_project_from_db
    db_result = query_mec_project_from_db.invoke({"project": project_name})
    db_devices = []
    if "异常设备列表" in db_result:
        import re as _re
        lines = db_result.split("\n")
        header_found = False
        for line in lines:
            if line.startswith("| 设备名"):
                header_found = True
                continue
            if header_found and line.startswith("|"):
                cells = [c.strip() for c in line.split("|")]
                if len(cells) >= 8:
                    name = cells[1]
                    ip = cells[2]
                    pm = cells[3]
                    container = cells[4]
                    img = cells[5]
                    is_abnormal = "❌ 离线" in pm or "❌ 离线" in container or "为0" in img or "偏低" in img or "无数据" in img
                    if is_abnormal:
                        if "❌ 离线" in pm:
                            db_devices.append({"name": name, "ip": ip, "diag_type": "physical_offline", "project": project_name})
                        elif "❌ 离线" in container:
                            db_devices.append({"name": name, "ip": ip, "diag_type": "container_offline", "project": project_name})
                        else:
                            db_devices.append({"name": name, "ip": ip, "diag_type": "zero_images", "project": project_name})

    if db_devices:
        container_offline_devices = [d for d in db_devices if d["diag_type"] == "container_offline"]
        zero_images_devices = [d for d in db_devices if d["diag_type"] == "zero_images"]
        physical_offline_devices = [d for d in db_devices if d["diag_type"] == "physical_offline"]
    else:
        # 数据库没有数据，回退到飞书报告
        report_text, error = fetch_mec_report_from_feishu()
        if error or not report_text:
            result_summary["error"] = error or "未获取到报告"
            return result_summary

        container_offline_devices, zero_images_devices, physical_offline_devices = \
            parse_abnormal_devices(report_text, project_name)

    if not container_offline_devices and not zero_images_devices and not physical_offline_devices:
        result_summary["success"] = True
        result_summary["dingtalk_message"] = f"## {project_name}\n\n项目当前无异常设备，无需诊断。"
        return result_summary

    # 这里只发现候选设备；实际诊断全部统一走 mec_diagnose_device。
    candidates = []
    seen_ips = set()
    for source_devices in (container_offline_devices, zero_images_devices, physical_offline_devices):
        for device in source_devices:
            ip = device.get("ip", "")
            if not ip or ip in seen_ips:
                continue
            seen_ips.add(ip)
            candidate = dict(device)
            candidate["project"] = project_name
            candidates.append(candidate)

    project_results = []
    total_need_llm = 0
    for device in candidates:
        result = diagnose_device(device)
        project_results.append(result)
        if result.get("deep_analysis"):
            total_need_llm += 1
    container_results = [
        r for r in project_results
        if r.get("status") in ("error", "warning")
    ]
    zero_results = [
        r for r in project_results
        if r.get("root_cause") in ("zero_images", "topic_all_zero", "topic_partial_zero")
    ]
    recovered_results = [r for r in project_results if r.get("status") == "normal"]

    dingtalk_msg = build_dingtalk_message(project_results, project_name)

    result_summary["success"] = True
    result_summary["total_diagnosed"] = len(project_results)
    result_summary["container_offline"] = len(container_offline_devices)
    result_summary["zero_images"] = len(zero_images_devices)
    result_summary["need_llm"] = total_need_llm
    result_summary["results"] = project_results
    result_summary["dingtalk_message"] = dingtalk_msg

    return result_summary