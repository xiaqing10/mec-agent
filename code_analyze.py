#!/usr/bin/env python3
"""
MEC代码分析脚本 - 纯代码分析，自动推送钉钉
从飞书获取最新监控报告，解析结构化数据，分级告警，推送钉钉
输出 [TRIGGER_LLM_DEEP_ANALYSIS] 表示存在P0/P1严重问题需LLM深度诊断
"""
import json
import sys
import os
import re
import subprocess
from pathlib import Path
from datetime import datetime

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

from mec_analyze import fetch_latest_mec_message, extract_timestamp, load_last_check, save_last_check

HISTORY_FILE = SCRIPTS_DIR / 'mec_structured_history.json'
MAX_HISTORY = 30

# 已知项目列表（用于匹配和校验）
KNOWN_PROJECTS = ['南京仙新路', '山西灵石', '德会', '德会隧道', '柯诸', '汉宜', '汕梅', '沈海', '绵九', '贵阳', '青海']


# ============================================================
# 1. parse_mec_report - 解析飞书日志为结构化数据
# ============================================================
def parse_mec_report(report_text):
    """解析飞书MEC监控报告文本为结构化数据

    输出格式:
    {
      "timestamp": "2026-05-09 10:05:34",
      "projects": {
        "项目名": {
          "physical": {"total": N, "healthy": N, "rate": 95.0},
          "container": {"total": N, "healthy": N, "rate": 90.0},
          "sensor": {"total": N, "healthy": N, "rate": 85.0},
          "container_offline_but_pm_online": [{"name":"设备名","ip":"IP"}],
          "zero_images_devices": [{"name":"设备名","ip":"IP"}]
        }
      }
    }
    """
    if not report_text:
        return None

    result = {
        "timestamp": "",
        "projects": {}
    }

    # 提取时间戳
    ts = extract_timestamp(report_text)
    result["timestamp"] = ts

    # 按项目分割文本
    # 项目标题格式: 📁 **项目: XXX** 或 **项目: XXX**
    project_blocks = re.split(r'(?=📁\s*\*\*项目[:：]\s*.*?\*\*)', report_text)

    for block in project_blocks:
        if '项目' not in block:
            continue

        # 提取项目名
        proj_match = re.search(r'📁\s*\*\*项目[:：]\s*(.*?)\*\*', block)
        if not proj_match:
            proj_match = re.search(r'\*\*项目[:：]\s*(.*?)\*\*', block)
        if not proj_match:
            continue

        proj_name = proj_match.group(1).strip()
        if not proj_name:
            continue

        proj_data = {
            "physical": {"total": 0, "healthy": 0, "rate": 0.0},
            "physical_offline_devices": [],
            "container": {"total": 0, "healthy": 0, "rate": 0.0},
            "sensor": {"total": 0, "healthy": 0, "rate": 0.0},
            "container_offline_but_pm_online": [],
            "zero_images_devices": []
        }

        # 解析物理机: **物理机**: ✅ 在线: 4 台 - ❌ 离线: 1 台
        phys_match = re.search(
            r'\*\*物理机\*\*[^:\n]*[:：]\s*✅\s*在线[:：]\s*(\d+)\s*台\s*-\s*❌\s*离线[:：]\s*(\d+)\s*台',
            block
        )
        if phys_match:
            healthy = int(phys_match.group(1))
            offline = int(phys_match.group(2))
            total = healthy + offline
            rate = round(healthy / total * 100, 1) if total > 0 else 0.0
            proj_data["physical"] = {"total": total, "healthy": healthy, "rate": rate}
        else:
            # 尝试格式2: 🔴 **物理机**: 0/34 健康 (0.0%)  + 换行 ❌ 离线: 34 台
            phys_match3 = re.search(
                r'\*\*物理机\*\*[^:\n]*[:：]\s*(\d+)/(\d+)',
                block
            )
            if phys_match3:
                healthy = int(phys_match3.group(1))
                total = int(phys_match3.group(2))
                rate = round(healthy / total * 100, 1) if total > 0 else 0.0
                proj_data["physical"] = {"total": total, "healthy": healthy, "rate": rate}
            else:
                # 尝试只匹配在线数（无离线）
                phys_match2 = re.search(
                    r'\*\*物理机\*\*[^:\n]*[:：]\s*✅\s*在线[:：]\s*(\d+)\s*台',
                    block
                )
                if phys_match2:
                    healthy = int(phys_match2.group(1))
                    proj_data["physical"] = {"total": healthy, "healthy": healthy, "rate": 100.0}

        # 解析物理机离线设备列表: 行中含 "离线" + JSON 设备列表（在物理机段落内）
        for line in block.split('\n'):
            if '离线' in line and '"name"' in line and '"ip"' in line:
                phys_off_devices = _parse_device_json(line)
                if phys_off_devices:
                    proj_data["physical_offline_devices"] = phys_off_devices
                    break

        # 解析容器: **容器在线**: ✅ 在线: 3 台 - ❌ 离线: 1 台 - 🟡 2 天未上报: 1 台
        cont_match = re.search(
            r'\*\*容器在线\*\*[^:\n]*[:：]\s*✅\s*在线[:：]\s*(\d+)\s*台\s*-\s*❌\s*离线[:：]\s*(\d+)\s*台',
            block
        )
        if cont_match:
            online = int(cont_match.group(1))
            offline = int(cont_match.group(2))
            total = online + offline
            rate = round(online / total * 100, 1) if total > 0 else 0.0
            proj_data["container"] = {"total": total, "healthy": online, "rate": rate}
        else:
            # 尝试格式2: 🔴 **容器在线**: 0/34 台 (0.0%)  + 换行 ❌ 离线: 34 台
            cont_match3 = re.search(
                r'\*\*容器在线\*\*[^:\n]*[:：]\s*(\d+)/(\d+)',
                block
            )
            if cont_match3:
                online = int(cont_match3.group(1))
                total = int(cont_match3.group(2))
                rate = round(online / total * 100, 1) if total > 0 else 0.0
                proj_data["container"] = {"total": total, "healthy": online, "rate": rate}
            else:
                # 尝试只匹配在线数
                cont_match2 = re.search(
                    r'\*\*容器在线\*\*[^:\n]*[:：]\s*✅\s*在线[:：]\s*(\d+)\s*台',
                    block
                )
                if cont_match2:
                    online = int(cont_match2.group(1))
                    proj_data["container"] = {"total": online, "healthy": online, "rate": 100.0}

        # 解析传感器: **传感器**: ✅ 在线: 7 台 - 🔴 离线: 2 台
        sens_match = re.search(
            r'\*\*传感器\*\*[^:\n]*[:：]\s*✅\s*在线[:：]\s*(\d+)\s*台\s*-\s*[🔴❌]\s*离线[:：]\s*(\d+)\s*台',
            block
        )
        if sens_match:
            online = int(sens_match.group(1))
            offline = int(sens_match.group(2))
            total = online + offline
            rate = round(online / total * 100, 1) if total > 0 else 0.0
            proj_data["sensor"] = {"total": total, "healthy": online, "rate": rate}
        else:
            sens_match2 = re.search(
                r'\*\*传感器\*\*[^:\n]*[:：]\s*✅\s*在线[:：]\s*(\d+)\s*台',
                block
            )
            if sens_match2:
                online = int(sens_match2.group(1))
                proj_data["sensor"] = {"total": online, "healthy": online, "rate": 100.0}

        # 解析: 🔴 物理机在线但容器不可连(N台): [JSON]
        cont_off_match = re.search(
            r'物理机在线但容器不可连\(\d+台\)\s*[:：]\s*(\[.*?\])',
            block, re.DOTALL
        )
        if cont_off_match:
            devices = _parse_device_json(cont_off_match.group(1))
            proj_data["container_offline_but_pm_online"] = devices

        # 解析: 🟠 容器在线但今日图片为0(N台): [JSON]
        zero_img_match = re.search(
            r'容器在线但今日图片为0\(\d+台\)\s*[:：]\s*(\[.*?\])',
            block, re.DOTALL
        )
        if zero_img_match:
            devices = _parse_device_json(zero_img_match.group(1))
            proj_data["zero_images_devices"] = devices

        result["projects"][proj_name] = proj_data

    return result


def _parse_device_json(json_str):
    """解析设备JSON列表，提取纯IP地址

    飞书IP格式: [10.145.58.111](http://10.145.58.111/)
    需要提取纯IP: 10.145.58.111
    """
    devices = []
    try:
        # 清理可能的markdown格式
        cleaned = json_str.strip()
        items = json.loads(cleaned)
        if not isinstance(items, list):
            return devices
        for item in items:
            name = item.get('name', '')
            ip_raw = item.get('ip', '')
            # 提取纯IP: 从 [IP](url) 格式中提取
            ip = _extract_ip(ip_raw)
            devices.append({"name": name, "ip": ip})
    except (json.JSONDecodeError, TypeError):
        # 尝试逐个解析
        try:
            # 可能是多个JSON对象拼接
            individual = re.findall(r'\{[^}]+\}', json_str)
            for obj_str in individual:
                obj = json.loads(obj_str)
                name = obj.get('name', '')
                ip_raw = obj.get('ip', '')
                ip = _extract_ip(ip_raw)
                devices.append({"name": name, "ip": ip})
        except:
            pass
    return devices


def _extract_ip(ip_str):
    """从飞书格式的IP字符串中提取纯IP

    支持:
    - [10.145.58.111](http://10.145.58.111/) → 10.145.58.111
    - 10.145.58.111 → 10.145.58.111
    """
    if not ip_str:
        return ""
    # 匹配方括号内的IP: [IP](...)
    m = re.search(r'\[(\d+\.\d+\.\d+\.\d+)\]', ip_str)
    if m:
        return m.group(1)
    # 匹配纯IP
    m = re.search(r'(\d+\.\d+\.\d+\.\d+)', ip_str)
    if m:
        return m.group(1)
    return ip_str


# ============================================================
# 2. classify_priority - P0-P3分级
# ============================================================
def classify_priority(proj_name, proj_data):
    """对单个项目进行问题分级

    P0: phys_rate==0 且 cont_rate==0 (项目完全离线), 或 phys_rate<50
    P1: phys_rate<80, 或 cont_off_count>0 (物理机在线但容器不可连)
    P2: phys_rate<95, 或 zero_img_count>=3
    P3: zero_img_count>0, 或 sens_rate<100, 或 cont_rate<100
    OK: 全部正常

    返回: (priority, reasons)
      priority: 'P0'|'P1'|'P2'|'P3'|'OK'
      reasons: list of str, 描述具体原因
    """
    physical = proj_data.get("physical", {})
    container = proj_data.get("container", {})
    sensor = proj_data.get("sensor", {})

    phys_rate = physical.get("rate", 100.0)
    cont_rate = container.get("rate", 100.0)
    sens_rate = sensor.get("rate", 100.0)

    cont_off_list = proj_data.get("container_offline_but_pm_online", [])
    zero_img_list = proj_data.get("zero_images_devices", [])
    # 兼容旧历史格式
    if not zero_img_list:
        zero_img_list = proj_data.get("container_online_zero_images", [])

    cont_off_count = len(cont_off_list)
    zero_img_count = len(zero_img_list)

    reasons = []
    priority = "OK"

    # P0: 项目完全离线 (phys_rate==0 且 cont_rate==0)
    # 注意: 不检查 total>0，因为当飞书报告正则匹配失败时 total 可能为 0
    # 但只要 phys_rate==0 且 cont_rate==0 就说明全部离线
    if phys_rate == 0 and cont_rate == 0:
        total_phys = physical.get("total", 0)
        total_cont = container.get("total", 0)
        if total_phys > 0 or total_cont > 0:
            reason = f"项目完全离线 (物理机{total_phys}台/容器{total_cont}台均不可达)"
        else:
            reason = "项目完全离线 (物理机和容器均无在线设备)"
        priority = "P0"
        reasons.append(reason)
        return priority, reasons

    # P0: phys_rate<50
    if phys_rate < 50 and physical.get("total", 0) > 0:
        priority = "P0"
        reasons.append(
            f"物理机健康率过低: {physical.get('healthy',0)}/{physical.get('total',0)} ({phys_rate:.1f}%)"
        )
        return priority, reasons

    # P1: phys_rate<80
    if phys_rate < 80 and physical.get("total", 0) > 0:
        priority = "P1"
        reasons.append(
            f"物理机健康率偏低: {physical.get('healthy',0)}/{physical.get('total',0)} ({phys_rate:.1f}%)"
        )

    # P1: cont_off_count>0 (物理机在线但容器不可连)
    if cont_off_count > 0:
        if priority != "P0":
            priority = "P1"
        dev_names = ", ".join(d.get("name", "?") for d in cont_off_list[:5])
        if cont_off_count > 5:
            dev_names += f" 等{cont_off_count}台"
        reasons.append(f"物理机在线但容器不可连({cont_off_count}台): {dev_names}")

    if priority in ("P0", "P1"):
        return priority, reasons

    # P2: phys_rate<95
    if phys_rate < 95 and physical.get("total", 0) > 0:
        priority = "P2"
        reasons.append(
            f"物理机健康率<95%: {physical.get('healthy',0)}/{physical.get('total',0)} ({phys_rate:.1f}%)"
        )

    # P2: zero_img_count>=3
    if zero_img_count >= 3:
        priority = "P2"
        dev_names = ", ".join(d.get("name", "?") for d in zero_img_list[:5])
        if zero_img_count > 5:
            dev_names += f" 等{zero_img_count}台"
        reasons.append(f"容器在线但今日图片为0({zero_img_count}台): {dev_names}")

    if priority == "P2":
        return priority, reasons

    # P3: zero_img_count>0
    if zero_img_count > 0:
        priority = "P3"
        dev_names = ", ".join(d.get("name", "?") for d in zero_img_list[:5])
        reasons.append(f"容器在线但今日图片为0({zero_img_count}台): {dev_names}")

    # P3: sens_rate<100
    if sens_rate < 100 and sensor.get("total", 0) > 0:
        priority = "P3"
        reasons.append(
            f"传感器在线率<100%: {sensor.get('healthy',0)}/{sensor.get('total',0)} ({sens_rate:.1f}%)"
        )

    # P3: cont_rate<100
    if cont_rate < 100 and container.get("total", 0) > 0:
        priority = "P3"
        reasons.append(
            f"容器在线率<100%: {container.get('healthy',0)}/{container.get('total',0)} ({cont_rate:.1f}%)"
        )

    if priority == "P3":
        return priority, reasons

    return "OK", []


# ============================================================
# 2.5 upgrade_priority_by_duration - 基于持续时长动态升级
# ============================================================
PRIORITY_LEVELS = {"OK": 0, "P3": 1, "P2": 2, "P1": 3, "P0": 4}

def upgrade_priority_by_duration(base_priority, duration_hours):
    """基于问题持续时长动态升级优先级

    规则:
      < 2小时:   保持原级
      2-6小时:   升一级 (P2→P1, P3→P2)
      6-24小时:  升两级 (P1→P0, P2→P1, P3→P2)
      > 24小时:  一律P0 (只要不是OK)
      > 72小时:  一律P0，标注"长期未解决"

    Args:
        base_priority: 原始分级 'P0'|'P1'|'P2'|'P3'|'OK'
        duration_hours: 问题持续小时数 (0表示新问题)

    Returns:
        (upgraded_priority, upgrade_reason)
    """
    if base_priority == "OK" or duration_hours <= 0:
        return base_priority, None

    base_level = PRIORITY_LEVELS.get(base_priority, 0)

    if duration_hours > 72:
        return "P0", f"持续{duration_hours:.0f}小时未解决，升级为P0"
    elif duration_hours > 24:
        return "P0", f"持续{duration_hours:.0f}小时，升级为P0"
    elif duration_hours > 6:
        new_level = min(base_level + 2, 4)
        new_priority = [k for k, v in PRIORITY_LEVELS.items() if v == new_level][0]
        return new_priority, f"持续{duration_hours:.0f}小时，{base_priority}升级为{new_priority}"
    elif duration_hours > 2:
        new_level = min(base_level + 1, 4)
        new_priority = [k for k, v in PRIORITY_LEVELS.items() if v == new_level][0]
        return new_priority, f"持续{duration_hours:.0f}小时，{base_priority}升级为{new_priority}"
    else:
        return base_priority, None


def calc_issue_duration_hours(proj_name, history):
    """计算项目异常持续小时数

    从历史报告中找到项目最后一次正常的时间，计算到现在的小时差。
    如果项目一直异常（无正常记录），返回从第一条历史记录开始的小时差。

    Args:
        proj_name: 项目名
        history: load_structured_history() 输出

    Returns:
        float: 持续小时数，0表示新问题或无历史
    """
    if not history or 'reports' not in history or not history['reports']:
        return 0

    reports = history['reports']
    # 从最新往前找，找到最后一次正常的时间
    last_ok_time = None
    for rpt in reversed(reports):
        proj = rpt.get("projects", {}).get(proj_name, {})
        if proj:
            p, _ = classify_priority(proj_name, proj)
            if p == "OK":
                last_ok_time = rpt.get("timestamp", rpt.get("saved_at", ""))
                break

    # 如果没找到正常记录，从最早一条异常记录开始计算
    if last_ok_time is None:
        first_abnormal_time = None
        for rpt in reports:
            proj = rpt.get("projects", {}).get(proj_name, {})
            if proj:
                p, _ = classify_priority(proj_name, proj)
                if p != "OK":
                    first_abnormal_time = rpt.get("timestamp", rpt.get("saved_at", ""))
                    break
        if first_abnormal_time:
            start = _parse_timestamp(first_abnormal_time)
            if start:
                now = datetime.now()
                return max(0, (now - start).total_seconds() / 3600)
        return 0

    # 从最后一次正常时间开始计算
    ok_dt = _parse_timestamp(last_ok_time)
    if ok_dt:
        now = datetime.now()
        return max(0, (now - ok_dt).total_seconds() / 3600)
    return 0


def _parse_timestamp(ts_str):
    """解析各种时间戳格式"""
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"]:
        try:
            # 去掉时区后缀的+08:00等
            clean = ts_str.split("+")[0].rstrip("Z")
            return datetime.strptime(clean, fmt)
        except (ValueError, AttributeError):
            continue
    return None


# ============================================================
# 3. compare_with_history - 历史对比
# ============================================================
def compare_with_history(current, history):
    """与历史报告对比，识别持续/新增/已恢复/恶化/好转

    参数:
      current: 当前解析后的结构化数据 (parse_mec_report的输出)
      history: load_structured_history()的输出，包含reports列表

    返回: {
      "persistent_issues": [...],  # 持续问题 (连续3次以上同一问题)
      "new_issues": [...],         # 新增问题
      "recovered_issues": [...],   # 已恢复
      "worsening": [...],          # 恶化 (健康率比历史平均下降>5%)
      "improving": [...]           # 好转 (健康率比历史平均上升>5%)
    }
    """
    comparison = {
        "persistent_issues": [],
        "new_issues": [],
        "recovered_issues": [],
        "worsening": [],
        "improving": []
    }

    if not history or 'reports' not in history or not history['reports']:
        # 无历史数据，所有问题都是新增
        for proj_name, proj_data in current.get("projects", {}).items():
            priority, reasons = classify_priority(proj_name, proj_data)
            if priority != "OK":
                for reason in reasons:
                    comparison["new_issues"].append({
                        "project": proj_name,
                        "level": priority,
                        "reason": reason
                    })
        return comparison

    reports = history['reports']

    # 获取当前各项目的问题分类
    current_issues = {}
    for proj_name, proj_data in current.get("projects", {}).items():
        priority, reasons = classify_priority(proj_name, proj_data)
        current_issues[proj_name] = {
            "priority": priority,
            "reasons": reasons,
            "phys_rate": proj_data.get("physical", {}).get("rate", 100.0),
            "cont_rate": proj_data.get("container", {}).get("rate", 100.0),
            "sens_rate": proj_data.get("sensor", {}).get("rate", 100.0),
        }

    # 获取上一次各项目的问题分类
    last_report = reports[-1]
    prev_issues = {}
    for proj_name, proj_data in last_report.get("projects", {}).items():
        priority, reasons = classify_priority(proj_name, proj_data)
        prev_issues[proj_name] = {
            "priority": priority,
            "reasons": reasons,
        }

    # 计算历史平均健康率（最近5次，不含当前）
    recent_reports = reports[-5:] if len(reports) >= 5 else reports
    history_avg = {}
    for proj_name in current_issues:
        phys_rates = []
        cont_rates = []
        sens_rates = []
        for rpt in recent_reports:
            proj = rpt.get("projects", {}).get(proj_name, {})
            if proj:
                phys = proj.get("physical", {})
                cont = proj.get("container", {})
                sens = proj.get("sensor", {})
                # 兼容旧格式: container可能用online而非healthy
                phys_r = phys.get("rate", None)
                cont_r = cont.get("rate", None)
                sens_r = sens.get("rate", None)
                if phys_r is not None:
                    phys_rates.append(phys_r)
                if cont_r is not None:
                    cont_rates.append(cont_r)
                if sens_r is not None:
                    sens_rates.append(sens_r)

        history_avg[proj_name] = {
            "phys_avg": sum(phys_rates) / len(phys_rates) if phys_rates else 100.0,
            "cont_avg": sum(cont_rates) / len(cont_rates) if cont_rates else 100.0,
            "sens_avg": sum(sens_rates) / len(sens_rates) if sens_rates else 100.0,
        }

    # 分析各项目
    all_proj_names = set(list(current_issues.keys()) + list(prev_issues.keys()))

    for proj_name in all_proj_names:
        curr = current_issues.get(proj_name)
        prev = prev_issues.get(proj_name)

        curr_priority = curr["priority"] if curr else "OK"
        prev_priority = prev["priority"] if prev else "OK"

        # 新增问题: 当前有问题但之前没有
        if curr_priority != "OK" and (prev_priority == "OK" or prev is None):
            for reason in (curr["reasons"] if curr else []):
                comparison["new_issues"].append({
                    "project": proj_name,
                    "level": curr_priority,
                    "reason": reason
                })

        # 已恢复: 之前有问题但当前没有
        elif curr_priority == "OK" and prev_priority != "OK":
            for reason in (prev["reasons"] if prev else []):
                comparison["recovered_issues"].append({
                    "project": proj_name,
                    "level": prev_priority,
                    "reason": reason
                })

        # 持续问题: 当前和之前都有问题
        elif curr_priority != "OK" and prev_priority != "OK":
            # 检查是否连续3次以上
            consecutive_count = 0
            for rpt in reversed(reports):
                proj = rpt.get("projects", {}).get(proj_name, {})
                if proj:
                    p, _ = classify_priority(proj_name, proj)
                    if p != "OK":
                        consecutive_count += 1
                    else:
                        break
                else:
                    break

            # 当前也算一次
            if curr_priority != "OK":
                consecutive_count += 1

            if consecutive_count >= 3:
                # 计算持续时长
                duration_hours = calc_issue_duration_hours(proj_name, {"reports": reports})
                for reason in (curr["reasons"] if curr else []):
                    comparison["persistent_issues"].append({
                        "project": proj_name,
                        "level": curr_priority,
                        "reason": reason,
                        "consecutive": consecutive_count,
                        "duration_hours": round(duration_hours, 1)
                    })
            else:
                # 当前有问题但不够持续3次，视为持续中的问题（非persistent，但仍在new中标记）
                for reason in (curr["reasons"] if curr else []):
                    comparison["new_issues"].append({
                        "project": proj_name,
                        "level": curr_priority,
                        "reason": reason
                    })

        # 恶化/好转: 基于健康率与历史平均值对比
        if curr and proj_name in history_avg:
            avg = history_avg[proj_name]
            # 物理机
            if curr["phys_rate"] < avg["phys_avg"] - 5:
                comparison["worsening"].append({
                    "project": proj_name,
                    "metric": "物理机健康率",
                    "current": curr["phys_rate"],
                    "average": round(avg["phys_avg"], 1),
                    "delta": round(curr["phys_rate"] - avg["phys_avg"], 1)
                })
            elif curr["phys_rate"] > avg["phys_avg"] + 5:
                comparison["improving"].append({
                    "project": proj_name,
                    "metric": "物理机健康率",
                    "current": curr["phys_rate"],
                    "average": round(avg["phys_avg"], 1),
                    "delta": round(curr["phys_rate"] - avg["phys_avg"], 1)
                })

            # 容器
            if curr["cont_rate"] < avg["cont_avg"] - 5:
                comparison["worsening"].append({
                    "project": proj_name,
                    "metric": "容器在线率",
                    "current": curr["cont_rate"],
                    "average": round(avg["cont_avg"], 1),
                    "delta": round(curr["cont_rate"] - avg["cont_avg"], 1)
                })
            elif curr["cont_rate"] > avg["cont_avg"] + 5:
                comparison["improving"].append({
                    "project": proj_name,
                    "metric": "容器在线率",
                    "current": curr["cont_rate"],
                    "average": round(avg["cont_avg"], 1),
                    "delta": round(curr["cont_rate"] - avg["cont_avg"], 1)
                })

            # 传感器
            if curr["sens_rate"] < avg["sens_avg"] - 5:
                comparison["worsening"].append({
                    "project": proj_name,
                    "metric": "传感器在线率",
                    "current": curr["sens_rate"],
                    "average": round(avg["sens_avg"], 1),
                    "delta": round(curr["sens_rate"] - avg["sens_avg"], 1)
                })
            elif curr["sens_rate"] > avg["sens_avg"] + 5:
                comparison["improving"].append({
                    "project": proj_name,
                    "metric": "传感器在线率",
                    "current": curr["sens_rate"],
                    "average": round(avg["sens_avg"], 1),
                    "delta": round(curr["sens_rate"] - avg["sens_avg"], 1)
                })

    return comparison


# ============================================================
# ============================================================
# 4a. _format_rate - 格式化健康率显示
# ============================================================
def _format_rate(data, compact=False):
    """格式化健康率显示

    当total=0时显示'-'(无数据)，而非误导性的0.0%

    Args:
        data: {"total": N, "healthy": N, "rate": float}
        compact: True=表格用紧凑格式, False=详情用完整格式
    """
    total = data.get("total", 0)
    healthy = data.get("healthy", 0)
    rate = data.get("rate", 0.0)

    if total == 0:
        return "-"

    if compact:
        if rate >= 100:
            return f"{healthy}/{total}"
        else:
            return f"{healthy}/{total}({rate:.0f}%)"
    else:
        if rate >= 100:
            return f"{healthy}/{total} ✅"
        else:
            return f"{healthy}/{total} ({rate:.1f}%)"


# ============================================================
# 4b. _get_suggestion - 根据问题类型生成针对性建议
# ============================================================
def _get_suggestion(priority, proj_data):
    """根据问题类型生成针对性建议，替代泛泛的通用建议"""
    phys = proj_data.get("physical", {})
    cont = proj_data.get("container", {})
    sens = proj_data.get("sensor", {})

    cont_off = proj_data.get("container_offline_but_pm_online", [])
    zero_img = proj_data.get("zero_images_devices", [])
    if not zero_img:
        zero_img = proj_data.get("container_online_zero_images", [])
    phys_offline = proj_data.get("physical_offline_devices", [])

    has_phys_offline = len(phys_offline) > 0
    has_cont_off = len(cont_off) > 0
    has_zero_img = len(zero_img) > 0
    phys_all_offline = phys.get("total", 0) > 0 and phys.get("rate", 0) == 0
    cont_all_offline = cont.get("total", 0) > 0 and cont.get("rate", 0) == 0

    # 物理机离线（最高优先级）
    if has_phys_offline:
        return "检查离线物理机的网络连接、电源状态，确认是否关机或断网"

    # 完全离线
    if phys_all_offline or cont_all_offline:
        return "检查网络连通性，确认项目是否仍在运行"

    # 混合问题
    if has_cont_off and has_zero_img:
        return "优先排查容器不可连设备(docker ps)，其次检查图片为0设备的采集服务"

    # 容器不可连
    if has_cont_off:
        return "SSH到物理机检查容器状态(docker ps)，排查容器启动失败或网络不通"

    # 图片为0
    if has_zero_img:
        return "检查容器内图片采集服务，确认RTSP流和摄像头状态"

    # 物理机部分离线
    if phys.get("total", 0) > 0 and phys.get("rate", 0) < 100:
        return f"排查{phys['total'] - phys['healthy']}台离线物理机的网络和电源状态"

    # 容器离线
    if cont.get("total", 0) > 0 and cont.get("rate", 0) < 100:
        return f"排查{cont['total'] - cont['healthy']}台容器离线原因，检查Docker服务状态"

    # 传感器离线
    if sens.get("total", 0) > 0 and sens.get("rate", 0) < 100:
        return f"检查{sens['total'] - sens['healthy']}台离线传感器的网络连接和供电"

    # 默认
    return "关注设备状态变化趋势"


# ============================================================
# 4. generate_report - 生成钉钉报告
# ============================================================
def generate_report(current, comparison, history=None):
    """生成钉钉Markdown格式报告

    格式: 全局总览表(所有项目) → 问题详情 → 趋势变化
    - 总览表一目了然，所有项目健康率+异常摘要
    - 区分"无数据"和"异常"，total=0显示"-"而非0.0%
    - 异常设备只列一次，不重复
    - 建议按问题类型针对性给出
    """
    projects = current.get("projects", {})
    timestamp = current.get("timestamp", "")

    # 对所有项目分级（含动态升级）
    proj_priorities = {}
    for proj_name, proj_data in projects.items():
        base_priority, reasons = classify_priority(proj_name, proj_data)
        upgrade_reason = None

        # 计算持续时长并动态升级
        if base_priority != "OK" and history:
            duration_hours = calc_issue_duration_hours(proj_name, history)
            if duration_hours > 0:
                upgraded_priority, upgrade_reason = upgrade_priority_by_duration(base_priority, duration_hours)
                if upgrade_reason:
                    reasons.append(f"⏱️ {upgrade_reason}")
                base_priority = upgraded_priority

        proj_priorities[proj_name] = {
            "priority": base_priority,
            "base_priority": classify_priority(proj_name, proj_data)[0],
            "reasons": reasons,
            "data": proj_data,
            "duration_hours": calc_issue_duration_hours(proj_name, history) if history and base_priority != "OK" else 0
        }

    # 按优先级排序
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "OK": 4}
    sorted_projects = sorted(proj_priorities.items(), key=lambda x: priority_order.get(x[1]["priority"], 5))

    # 按优先级分组
    groups = {"P0": [], "P1": [], "P2": [], "P3": [], "OK": []}
    for proj_name, info in proj_priorities.items():
        groups[info["priority"]].append((proj_name, info))

    has_severe = len(groups["P0"]) > 0 or len(groups["P1"]) > 0

    # 构建报告
    if has_severe:
        msg = "## 🔴 MEC监控-代码分析\n\n"
    else:
        msg = "## ✅ MEC监控-代码分析\n\n"

    msg += f"**报告时间**: {timestamp}\n"
    msg += f"**分析时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    # ── 全局总览表 ──
    msg += "### 📊 全局总览\n\n"
    msg += "| 项目 | 级别 | 物理机 | 容器 | 传感器 | 主要问题 |\n"
    msg += "|------|------|--------|------|--------|----------|\n"

    for proj_name, info in sorted_projects:
        proj_data = info["data"]
        phys = proj_data.get("physical", {})
        cont = proj_data.get("container", {})
        sens = proj_data.get("sensor", {})

        # 优先级图标
        p = info["priority"]
        p_icon = {"P0": "🔴P0", "P1": "🟠P1", "P2": "🟡P2", "P3": "🔵P3", "OK": "✅"}.get(p, p)

        # 健康率（紧凑格式，total=0显示"-"）
        phys_str = _format_rate(phys, compact=True)
        cont_str = _format_rate(cont, compact=True)
        sens_str = _format_rate(sens, compact=True)

        # 异常摘要（简洁，不含设备名）
        issues = []
        cont_off = proj_data.get("container_offline_but_pm_online", [])
        zero_img = proj_data.get("zero_images_devices", [])
        if not zero_img:
            zero_img = proj_data.get("container_online_zero_images", [])
        phys_offline = proj_data.get("physical_offline_devices", [])

        if phys_offline:
            issues.append(f"物理机离线({len(phys_offline)}台)")
        if cont_off:
            issues.append(f"容器不可连({len(cont_off)}台)")
        if zero_img:
            issues.append(f"图片为0({len(zero_img)}台)")
        if not issues and phys.get("total", 0) > 0 and phys.get("rate", 0) < 100:
            issues.append(f"物理机离线({phys['total'] - phys['healthy']}台)")
        if not issues and cont.get("total", 0) > 0 and cont.get("rate", 0) < 100:
            issues.append(f"容器离线({cont['total'] - cont['healthy']}台)")
        if not issues and sens.get("total", 0) > 0 and sens.get("rate", 0) < 100:
            issues.append(f"传感器离线({sens['total'] - sens['healthy']}台)")

        issue_str = "·".join(issues) if issues else "-"

        msg += f"| {proj_name} | {p_icon} | {phys_str} | {cont_str} | {sens_str} | {issue_str} |\n"

    msg += "\n"

    # 概览统计
    msg += f"🔴P0 {len(groups['P0'])} · 🟠P1 {len(groups['P1'])} · 🟡P2 {len(groups['P2'])} · 🔵P3 {len(groups['P3'])} · ✅正常 {len(groups['OK'])}\n\n"

    # 趋势摘要
    if comparison:
        comp_parts = []
        if comparison.get("persistent_issues"):
            comp_parts.append(f"🔄持续{len(comparison['persistent_issues'])}项")
        if comparison.get("new_issues"):
            comp_parts.append(f"🆕新增{len(comparison['new_issues'])}项")
        if comparison.get("recovered_issues"):
            comp_parts.append(f"✅恢复{len(comparison['recovered_issues'])}项")
        if comparison.get("worsening"):
            comp_parts.append(f"📉恶化{len(comparison['worsening'])}项")
        if comparison.get("improving"):
            comp_parts.append(f"📈好转{len(comparison['improving'])}项")
        if comp_parts:
            msg += f"**趋势**: {' '.join(comp_parts)}\n\n"

    msg += "---\n\n"

    # ── 紧急问题详情 ──
    if groups["P0"] or groups["P1"]:
        msg += "### 🔴 紧急问题\n\n"
        for proj_name, info in groups["P0"] + groups["P1"]:
            proj_data = info["data"]
            phys = proj_data.get("physical", {})
            cont = proj_data.get("container", {})
            sens = proj_data.get("sensor", {})

            icon = "🔴" if info["priority"] == "P0" else "🟠"

            # 标题行（含升级标记和持续时长）
            base_p = info.get("base_priority", info["priority"])
            duration_h = info.get("duration_hours", 0)
            if base_p != info["priority"]:
                msg += f"**{icon} [{info['priority']}] {proj_name}** (原{base_p}，持续{duration_h:.0f}h升级)\n\n"
            elif duration_h > 0:
                msg += f"**{icon} [{info['priority']}] {proj_name}** (持续{duration_h:.0f}h)\n\n"
            else:
                msg += f"**{icon} [{info['priority']}] {proj_name}**\n\n"

            # 健康率（完整格式，区分无数据）
            msg += f"- 物理机: {_format_rate(phys)}\n"
            msg += f"- 容器: {_format_rate(cont)}\n"
            msg += f"- 传感器: {_format_rate(sens)}\n"

            # 异常设备（只列一次，含IP）
            cont_off = proj_data.get("container_offline_but_pm_online", [])
            zero_img = proj_data.get("zero_images_devices", [])
            if not zero_img:
                zero_img = proj_data.get("container_online_zero_images", [])
            phys_offline = proj_data.get("physical_offline_devices", [])

            if phys_offline:
                msg += f"- ❌ 物理机离线({len(phys_offline)}台): "
                msg += ", ".join(f"{d.get('name','?')}({d.get('ip','')})" for d in phys_offline[:5])
                if len(phys_offline) > 5:
                    msg += f" 等{len(phys_offline)}台"
                msg += "\n"

            if cont_off:
                msg += f"- 🔴 容器不可连({len(cont_off)}台): "
                msg += ", ".join(f"{d.get('name','?')}({d.get('ip','')})" for d in cont_off[:5])
                if len(cont_off) > 5:
                    msg += f" 等{len(cont_off)}台"
                msg += "\n"

            if zero_img:
                msg += f"- 🟠 图片为0({len(zero_img)}台): "
                msg += ", ".join(f"{d.get('name','?')}({d.get('ip','')})" for d in zero_img[:5])
                if len(zero_img) > 5:
                    msg += f" 等{len(zero_img)}台"
                msg += "\n"

            # 针对性建议
            msg += f"- 💡 建议: {_get_suggestion(info['priority'], proj_data)}\n\n"

    # P2 重要
    if groups["P2"]:
        msg += "### 🟡 P2 重要问题\n\n"
        for proj_name, info in groups["P2"]:
            proj_data = info["data"]
            phys = proj_data.get("physical", {})
            cont = proj_data.get("container", {})
            sens = proj_data.get("sensor", {})

            base_p = info.get("base_priority", info["priority"])
            duration_h = info.get("duration_hours", 0)
            if base_p != info["priority"]:
                msg += f"**🟡 [{info['priority']}] {proj_name}** (原{base_p}，持续{duration_h:.0f}h升级)\n\n"
            else:
                msg += f"**🟡 [{info['priority']}] {proj_name}**\n\n"

            msg += f"- 物理机: {_format_rate(phys)}\n"
            msg += f"- 容器: {_format_rate(cont)}\n"
            msg += f"- 传感器: {_format_rate(sens)}\n"

            zero_img = proj_data.get("zero_images_devices", [])
            if not zero_img:
                zero_img = proj_data.get("container_online_zero_images", [])
            if zero_img:
                msg += f"- 🟠 图片为0({len(zero_img)}台): "
                msg += ", ".join(f"{d.get('name','?')}" for d in zero_img[:3])
                if len(zero_img) > 3:
                    msg += f" 等{len(zero_img)}台"
                msg += "\n"

            msg += f"- 💡 建议: {_get_suggestion(info['priority'], proj_data)}\n\n"

    # P3 一般
    if groups["P3"]:
        msg += "### 🔵 P3 一般问题\n\n"
        for proj_name, info in groups["P3"]:
            proj_data = info["data"]
            phys = proj_data.get("physical", {})
            cont = proj_data.get("container", {})
            sens = proj_data.get("sensor", {})

            msg += f"**🔵 [{info['priority']}] {proj_name}** - "
            msg += f"物理机{_format_rate(phys, compact=True)} | "
            msg += f"容器{_format_rate(cont, compact=True)} | "
            msg += f"传感器{_format_rate(sens, compact=True)}\n\n"

            # 简洁问题列表
            zero_img = proj_data.get("zero_images_devices", [])
            if not zero_img:
                zero_img = proj_data.get("container_online_zero_images", [])
            if zero_img:
                msg += f"- 图片为0: {', '.join(d.get('name','?') for d in zero_img[:3])}\n"
            if cont.get("total", 0) > 0 and cont.get("rate", 0) < 100:
                msg += f"- 容器离线{cont['total'] - cont['healthy']}台\n"
            if sens.get("total", 0) > 0 and sens.get("rate", 0) < 100:
                msg += f"- 传感器离线{sens['total'] - sens['healthy']}台\n"

            msg += "\n"

    # OK 正常
    if groups["OK"]:
        msg += "### ✅ 运行正常\n\n"
        for proj_name, info in groups["OK"]:
            proj_data = info["data"]
            phys = proj_data.get("physical", {})
            cont = proj_data.get("container", {})
            sens = proj_data.get("sensor", {})
            msg += f"- **{proj_name}** 物理机{_format_rate(phys, compact=True)} | 容器{_format_rate(cont, compact=True)} | 传感器{_format_rate(sens, compact=True)}\n"
        msg += "\n"

    # 恶化/好转
    if comparison:
        if comparison.get("worsening"):
            msg += "### 📉 恶化趋势\n\n"
            for w in comparison["worsening"]:
                msg += f"- **{w['project']}** {w['metric']}: 当前{w['current']:.1f}% (历史均{w['average']:.1f}%, {w['delta']:+.1f}%)\n"
            msg += "\n"

        if comparison.get("improving"):
            msg += "### 📈 好转趋势\n\n"
            for i in comparison["improving"]:
                msg += f"- **{i['project']}** {i['metric']}: 当前{i['current']:.1f}% (历史均{i['average']:.1f}%, {i['delta']:+.1f}%)\n"
            msg += "\n"

    msg += "---\n*此报告由代码分析自动生成*"
    return msg, has_severe



# ============================================================
# 5. save_report_to_history - 保存到结构化历史
# ============================================================
def save_report_to_history(current):
    """保存当前报告到结构化历史

    mec_structured_history.json, 最多30条, FIFO
    """
    history = load_structured_history()
    if history is None:
        history = {"reports": []}

    # 构建保存条目
    entry = {
        "timestamp": current.get("timestamp", ""),
        "saved_at": datetime.now().isoformat(),
        "projects": {}
    }

    for proj_name, proj_data in current.get("projects", {}).items():
        entry["projects"][proj_name] = proj_data

    # 追加
    history["reports"].append(entry)

    # FIFO: 保留最近30条
    if len(history["reports"]) > MAX_HISTORY:
        history["reports"] = history["reports"][-MAX_HISTORY:]

    # 写入文件
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        print(f"   ✅ 已保存到历史 ({len(history['reports'])}条)")
    except Exception as e:
        print(f"   ❌ 保存历史失败: {e}")


# ============================================================
# 6. load_structured_history - 加载历史
# ============================================================
def load_structured_history():
    """加载结构化历史数据

    返回: {"reports": [...]} 或 None
    """
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None
    return None


# ============================================================
# 7. push_to_dingtalk - 推送钉钉
# ============================================================
def push_to_dingtalk(report):
    """推送消息到钉钉

    使用 dingtalk_send.send_dingtalk("MEC监控代码分析报告", report)
    """
    try:
        from dingtalk_send import send_dingtalk
        result = send_dingtalk("MEC监控-代码分析", report)
        errcode = result.get('errcode', -1)
        if errcode == 0:
            print("✅ 钉钉推送成功")
        else:
            print(f"⚠️ 钉钉推送返回: {json.dumps(result, ensure_ascii=False)}")
        return errcode == 0
    except Exception as e:
        print(f"❌ 钉钉推送失败: {e}")
        return False


# ============================================================
# 8. update_task_status - 更新任务状态
# ============================================================
def update_task_status(status):
    """记录任务执行状态"""
    print(f"📋 任务状态: {status}")


# ============================================================
# 9. _should_trigger_llm - 判断是否触发LLM
# ============================================================
def _should_trigger_llm(current, comparison):
    """判断是否需要触发LLM深度分析

    **只有P0/P1才返回True**（已收紧条件，不再检查持续问题和恶化趋势）
    """
    for proj_name, proj_data in current.get("projects", {}).items():
        priority, _ = classify_priority(proj_name, proj_data)
        if priority in ("P0", "P1"):
            return True
    return False


# ============================================================
# 10. run_analysis - 主流程
# ============================================================
def run_analysis(report_text=None, push=True):
    """主分析流程

    返回: (report_text, should_trigger_llm)
    流程: 获取日志 → 解析 → 保存历史 → 历史对比 → 生成报告 → 推送钉钉 → 更新时间戳 → 判断LLM触发
    """
    print(f"=== MEC代码分析 {datetime.now()} ===")

    # 1. 获取日志
    print("\n1. 获取飞书日志...")
    _auto_fetched = False
    if report_text is None:
        report_text, error = fetch_latest_mec_message()
        _auto_fetched = True
        if error:
            print(f"   ❌ 获取失败: {error}")
            update_task_status("error")
            return "", False
        if not report_text:
            print("   ❌ 未获取到报告")
            update_task_status("error")
            return "", False
    else:
        print("   使用传入的报告文本")

    # 2. 解析为结构化数据
    print("\n2. 解析结构化数据...")
    current = parse_mec_report(report_text)
    if not current or not current.get("projects"):
        print("   ❌ 解析失败，无项目数据")
        update_task_status("error")
        return "", False

    timestamp = current.get("timestamp", "")
    print(f"   报告时间: {timestamp}")
    print(f"   解析到 {len(current['projects'])} 个项目")

    # 检查是否有新日志（避免重复推送）
    # 只在自动获取模式下检查（report_text由外部传入时不检查，因为可能是强制分析）
    if _auto_fetched and load_last_check().get('last_timestamp') == timestamp:
        print("   ℹ️ 无新日志，跳过分析")
        update_task_status("ok")
        return "", False

    print("   ✅ 发现新日志")

    # 3. 保存到历史
    print("\n3. 保存到历史...")
    save_report_to_history(current)

    # 4. 历史对比
    print("\n4. 历史对比...")
    history = load_structured_history()
    comparison = compare_with_history(current, history)

    # 打印对比摘要
    if comparison["persistent_issues"]:
        print(f"   🔄 持续问题: {len(comparison['persistent_issues'])}项")
    if comparison["new_issues"]:
        print(f"   🆕 新增问题: {len(comparison['new_issues'])}项")
    if comparison["recovered_issues"]:
        print(f"   ✅ 已恢复: {len(comparison['recovered_issues'])}项")
    if comparison["worsening"]:
        print(f"   📉 恶化: {len(comparison['worsening'])}项")
    if comparison["improving"]:
        print(f"   📈 好转: {len(comparison['improving'])}项")

    # 5. 生成报告
    print("\n5. 生成报告...")
    report, has_severe = generate_report(current, comparison, history)

    # 打印分级摘要
    for proj_name, proj_data in current.get("projects", {}).items():
        priority, reasons = classify_priority(proj_name, proj_data)
        icons = {"P0": "🔴", "P1": "🟠", "P2": "🟡", "P3": "🔵", "OK": "✅"}
        icon = icons.get(priority, "❓")
        if priority != "OK":
            print(f"   {icon} {proj_name}: {priority} ({len(reasons)}个问题)")
        else:
            print(f"   {icon} {proj_name}: 正常")

    # 6. 推送钉钉
    if push:
        print("\n6. 推送钉钉...")
        push_to_dingtalk(report)
    else:
        print("\n6. 跳过钉钉推送 (--no-push)")

    # 6.5 保存分析结果到diagnose_logs
    try:
        log_dir = SCRIPTS_DIR / "diagnose_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts_short = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_file = log_dir / f"code_analysis_{ts_short}.json"
        log_data = {
            "type": "代码分析",
            "title": "MEC监控-代码分析",
            "timestamp": timestamp,
            "saved_at": datetime.now().isoformat(),
            "report": report,
            "structured_data": current,
            "comparison": comparison,
            "project_priorities": {}
        }
        # 写入每个项目的升级后优先级和持续时长
        for proj_name, proj_data in current.get("projects", {}).items():
            base_p, _ = classify_priority(proj_name, proj_data)
            duration_h = calc_issue_duration_hours(proj_name, history) if history and base_p != "OK" else 0
            upgraded_p, upgrade_reason = upgrade_priority_by_duration(base_p, duration_h) if duration_h > 0 else (base_p, None)
            log_data["project_priorities"][proj_name] = {
                "priority": upgraded_p,
                "base_priority": base_p,
                "duration_hours": round(duration_h, 1),
                "upgrade_reason": upgrade_reason,
            }
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
        print(f"   💾 分析结果已保存: {log_file}")
    except Exception as e:
        print(f"   ⚠️ 保存分析结果失败: {e}")

    # 7. 更新时间戳
    print("\n7. 更新时间戳...")
    save_last_check(timestamp)
    print(f"   ✅ 已更新时间戳: {timestamp}")

    # 8. 判断LLM触发
    should_trigger = _should_trigger_llm(current, comparison)

    # 9. 更新任务状态
    status = "alert" if has_severe else "ok"
    update_task_status(status)

    # 10. 输出LLM触发标记
    if should_trigger:
        print("\n[TRIGGER_LLM_DEEP_ANALYSIS]")
    else:
        print("\n✅ 分析完成，无P0/P1严重问题")

    return report, should_trigger


# ============================================================
# 11. main入口
# ============================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description='MEC监控代码分析脚本')
    parser.add_argument('--analyze-only', action='store_true',
                        help='从stdin读取报告文本，仅分析不推送')
    parser.add_argument('--no-push', action='store_true',
                        help='不推送到钉钉')
    parser.add_argument('--force', action='store_true',
                        help='强制分析，忽略时间戳检查')
    args = parser.parse_args()

    if args.force:
        save_last_check('')

    if args.analyze_only:
        # 从stdin读取报告
        report_text = sys.stdin.read()
        if not report_text.strip():
            print("❌ stdin为空")
            sys.exit(1)
        report, should_trigger = run_analysis(report_text=report_text, push=False)
    elif args.no_push:
        report, should_trigger = run_analysis(push=False)
    else:
        report, should_trigger = run_analysis(push=True)


def analyze_project(project_name: str, push: bool = True) -> dict:
    """分析指定项目的日志。

    Args:
        project_name: 项目名称
        push: 是否推送到钉钉

    Returns:
        dict: {
            "success": bool,
            "project": project_name,
            "report": str,
            "has_severe": bool,
            "should_trigger_llm": bool,
            "project_data": dict | None,
            "error": str | None
        }
    """
    result = {
        "success": False,
        "project": project_name,
        "report": "",
        "has_severe": False,
        "should_trigger_llm": False,
        "project_data": None,
        "error": None
    }

    sys.path.insert(0, str(SCRIPTS_DIR))

    # 1. 获取日志
    report_text, error = fetch_latest_mec_message()
    if error:
        result["error"] = error
        return result
    if not report_text:
        result["error"] = "未获取到报告"
        return result

    # 2. 解析
    current = parse_mec_report(report_text)
    if not current or not current.get("projects"):
        result["error"] = "解析失败，无项目数据"
        return result

    # 3. 检查指定项目是否存在
    if project_name not in current["projects"]:
        result["error"] = f"项目 '{project_name}' 不在当前报告中。可用项目: {', '.join(current['projects'].keys())}"
        return result

    # 4. 只保留指定项目
    filtered_current = {
        "timestamp": current.get("timestamp", ""),
        "projects": {project_name: current["projects"][project_name]}
    }

    # 5. 保存到历史（整体保存，不影响过滤）
    save_report_to_history(current)

    # 6. 历史对比
    history = load_structured_history()
    comparison = compare_with_history(filtered_current, history)

    # 7. 生成报告
    report, has_severe = generate_report(filtered_current, comparison, history)

    # 8. 推送
    if push:
        push_to_dingtalk(report)

    # 9. 判断LLM触发
    should_trigger = _should_trigger_llm(filtered_current, comparison)

    if should_trigger:
        report += "\n\n**[TRIGGER_LLM] 存在P0/P1严重问题，建议深度诊断**"

    result["success"] = True
    result["report"] = report
    result["has_severe"] = has_severe
    result["should_trigger_llm"] = should_trigger
    result["project_data"] = filtered_current["projects"][project_name]

    return result


if __name__ == '__main__':
    main()