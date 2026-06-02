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
SCRIPTS_DIR = Path("/home/sy/.hermes/scripts")
LLM_PENDING_DIR = SELF_AGENT_DIR / "diagnose_logs" / "llm_pending"


def should_need_llm(diagnosis_result):
    diagnosis = diagnosis_result.get('diagnosis', {})
    diag_type = diagnosis_result.get('type', '')
    issue = diagnosis.get('issue', '')

    if 'error' in diagnosis:
        clear_errors = ['物理机无法连接']
        if any(ce in diagnosis['error'] for ce in clear_errors):
            return False
        return True

    if diag_type == 'container_offline':
        clear_issues = ['Docker服务未运行', 'dev容器不存在', '容器未运行',
                        'docker exec失败', '容器SSH无法连接', '容器内SSH服务不可连接']
        if any(ci in issue for ci in clear_issues):
            return False
        return True

    if diag_type == 'zero_images':
        clear_issues = ['进程异常:', 'roscore未运行', 'supervisorctl status无输出',
                        '驱动没有加载', 'ROS环境异常', '设备已恢复正常']
        if any(ci in issue for ci in clear_issues):
            return False
        error_category = diagnosis.get('error_category', '')
        if error_category and error_category != 'process':
            return False
        if '日志错误' in issue:
            log_errors = diagnosis.get('log_errors', {})
            for proc_name, info in log_errors.items():
                errors = info.get('errors', [])
                for err in errors:
                    err_lower = err.lower()
                    if any(kw in err_lower for kw in ['host is unreachable', 'cnrterror',
                                                        'get current device failed', 'card : none']):
                        return False
            return True
        vague_issues = ['topic无数据', '但所有topic无数据', 'topic有数据，但图片为0',
                       'rostopic list无输出', '无image相关topic']
        if any(vi in issue for vi in vague_issues):
            return True
        if '容器SSH无法连接' in issue:
            return False
        return True

    return False


def write_llm_pending(diagnosis_result, diag_type, device_info):
    LLM_PENDING_DIR.mkdir(parents=True, exist_ok=True)
    ip = device_info.get('ip', diagnosis_result.get('host', ''))
    device_name = device_info.get('name', diagnosis_result.get('device_name', ''))
    project = device_info.get('project', diagnosis_result.get('project', ''))

    pending_data = {
        "ip": ip,
        "device_name": device_name,
        "project": project,
        "diag_type": diag_type,
        "code_diagnosis": diagnosis_result,
        "need_llm_reason": diagnosis_result.get('diagnosis', {}).get('issue', '未知'),
        "pending_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }

    filename = f"{ip}_{diag_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    filepath = LLM_PENDING_DIR / filename

    with open(filepath, 'w') as f:
        json.dump(pending_data, f, ensure_ascii=False, indent=2)
    return filepath


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


def diagnose_device(diag_type, device_info):
    from diagnose_mec import diagnose_container_offline, diagnose_zero_images
    device_name = device_info.get('name', '')
    ip = device_info.get('ip', '')
    project = device_info.get('project', '')

    if not ip:
        return {
            "host": "", "device_name": device_name, "project": project,
            "type": diag_type, "diagnosis": {"error": "IP地址为空"}, "recommendations": []
        }

    try:
        if diag_type == "container_offline" or diag_type == "physical_offline":
            result = diagnose_container_offline(ip)
        elif diag_type == "zero_images":
            result = diagnose_zero_images(ip)
        else:
            return {"host": ip, "device_name": device_name, "project": project,
                    "type": diag_type, "diagnosis": {"error": f"未知诊断类型: {diag_type}"}, "recommendations": []}

        if isinstance(result, dict):
            result["device_name"] = device_name
            result["project"] = project
        return result

    except Exception as e:
        return {"host": ip, "device_name": device_name, "project": project,
                "type": diag_type, "diagnosis": {"error": str(e)}, "recommendations": []}


def build_dingtalk_message(container_offline_results, zero_images_results, project_name, recovered_results=None):
    message = f"## 设备诊断-项目: {project_name}\n\n"
    message += f"**诊断时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    if container_offline_results:
        message += f"### \U0001f527 容器离线诊断 ({len(container_offline_results)}台)\n\n"
        for r in container_offline_results:
            ip = r.get('host', '')
            device_id = r.get('device_name', '未知')
            diagnosis = r.get('diagnosis', {})
            message += f"**{device_id} ({ip})**\n"
            if 'error' in diagnosis:
                message += f"\u274c 错误: {diagnosis['error']}\n"
            else:
                for key in ['physical_machine', 'docker_service', 'container_exec', 'container_ssh_connect']:
                    val = diagnosis.get(key, '')
                    if val:
                        message += f"- {key}: {val}\n"
                issue = diagnosis.get('issue', '')
                if issue:
                    message += f"- **问题**: {issue}\n"
            message += "\n"

    if zero_images_results:
        message += f"### \U0001f4f7 图片为0诊断 ({len(zero_images_results)}台)\n\n"
        for r in zero_images_results:
            ip = r.get('host', '')
            device_id = r.get('device_name', '未知')
            diagnosis = r.get('diagnosis', {})
            message += f"**{device_id} ({ip})**\n"
            if 'error' in diagnosis:
                message += f"\u274c 错误: {diagnosis['error']}\n"
            else:
                issue = diagnosis.get('issue', '')
                if issue:
                    message += f"- **问题**: {issue}\n"
                supervisor = diagnosis.get('supervisor', {})
                if supervisor and supervisor.get('abnormal', 0) > 0:
                    abnormals = diagnosis.get('abnormal_processes', [])
                    for p in abnormals[:5]:
                        message += f"  - {p.get('name','')}: {p.get('status','')}\n"
                log_errors = diagnosis.get('log_errors', {})
                if log_errors:
                    for proc_name, info in log_errors.items():
                        errors = info.get('errors', [])
                        if errors:
                            message += f"  - {proc_name}: {errors[0][:80]}\n"
            message += "\n"

    if recovered_results:
        message += f"### ✅ 已恢复设备 ({len(recovered_results)}台)\n\n"
        for r in recovered_results:
            ip = r.get('host', '')
            device_id = r.get('device_name', '未知')
            diagnosis = r.get('diagnosis', {})
            message += f"**{device_id} ({ip})**\n"
            today_count = diagnosis.get('today_image_count', 0)
            message += f"- 📸 今日图片: {today_count} 张\n"
            latest_time = diagnosis.get('latest_image_time', '')
            latest_file = diagnosis.get('latest_image_file', '')
            if latest_time:
                message += f"- 🕐 最新图片: {latest_time}\n"
            if latest_file:
                message += f"- 📄 最新文件: {latest_file}\n"
            supervisor_output = diagnosis.get('supervisor_output', '')
            if supervisor_output:
                message += f"- ⚙️ 进程状态:\n"
                for line in supervisor_output.split('\n')[:10]:
                    line = line.strip()
                    if line:
                        message += f"  {line}\n"
            issue = diagnosis.get('issue', '')
            if issue:
                message += f"- ℹ️ {issue}\n"
            message += "\n"

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
    from tools.tool_db import query_project_from_db
    db_result = query_project_from_db.invoke({"project": project_name})
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

    project_results = []
    total_need_llm = 0

    for device in container_offline_devices:
        result = diagnose_device("container_offline", device)
        project_results.append(result)
        if should_need_llm(result):
            write_llm_pending(result, "container_offline", device)
            total_need_llm += 1

    for device in zero_images_devices:
        result = diagnose_device("zero_images", device)
        project_results.append(result)
        if should_need_llm(result):
            write_llm_pending(result, "zero_images", device)
            total_need_llm += 1

    for device in physical_offline_devices:
        result = diagnose_device("physical_offline", device)
        project_results.append(result)
        if should_need_llm(result):
            write_llm_pending(result, "physical_offline", device)
            total_need_llm += 1

    container_results = [r for r in project_results if r.get('type') in ('container_offline', 'physical_offline')
                         and '正常' not in r.get('diagnosis', {}).get('issue', '')]
    zero_results = [r for r in project_results if r.get('type') == 'zero_images'
                    and '正常' not in r.get('diagnosis', {}).get('issue', '')
                    and '已恢复正常' not in r.get('diagnosis', {}).get('issue', '')]
    recovered_results = [r for r in project_results if r.get('type') == 'zero_images'
                         and '已恢复正常' in r.get('diagnosis', {}).get('issue', '')]

    dingtalk_msg = build_dingtalk_message(container_results, zero_results, project_name, recovered_results)

    result_summary["success"] = True
    result_summary["total_diagnosed"] = len(project_results)
    result_summary["container_offline"] = len(container_offline_devices)
    result_summary["zero_images"] = len(zero_images_devices)
    result_summary["need_llm"] = total_need_llm
    result_summary["results"] = project_results
    result_summary["dingtalk_message"] = dingtalk_msg

    return result_summary
