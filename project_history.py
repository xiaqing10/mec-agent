#!/usr/bin/env python3
"""
项目诊断历史管理 - 每次设备诊断结果累积到项目历史
"""
import json
from pathlib import Path
from datetime import datetime

SELF_AGENT_DIR = Path(__file__).parent
HISTORY_DIR = SELF_AGENT_DIR / "diagnose_logs" / "project_history"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _project_file(project: str) -> Path:
    return HISTORY_DIR / f"{project}.json"


def load_project_records(project: str) -> dict:
    pfile = _project_file(project)
    if pfile.exists():
        try:
            with open(pfile) as f:
                return json.load(f)
        except Exception:
            pass
    return {"project": project, "devices": {}, "updated_at": ""}


def save_diagnosis(project: str, device_name: str, ip: str, diag_result: dict):
    if not project:
        return
    records = load_project_records(project)
    diagnosis = diag_result.get("diagnosis", {})
    record = {
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "diag_type": diag_result.get("type", ""),
        "issue": diagnosis.get("issue", ""),
        "error": diagnosis.get("error", ""),
        "physical_uptime": diagnosis.get("physical_uptime", ""),
        "container_status": diagnosis.get("container_status", ""),
        "today_image_count": diagnosis.get("today_image_count", -1),
    }
    if device_name not in records["devices"]:
        records["devices"][device_name] = {"name": device_name, "ip": ip, "records": []}
    records["devices"][device_name]["records"].append(record)
    records["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with open(_project_file(project), "w") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def build_history_summary(project: str) -> str:
    records = load_project_records(project)
    if not records["devices"]:
        return ""
    lines = [f"## {project}项目诊断历史概览\n"]
    for dev_name, dev_data in sorted(records["devices"].items()):
        recs = dev_data["records"]
        total = len(recs)
        latest = recs[-1] if recs else {}
        latest_issue = latest.get("issue", "") or latest.get("error", "正常")
        same_count = sum(1 for r in recs if r.get("issue") == latest.get("issue"))
        trend = "持续" if same_count >= 2 else "新发"
        lines.append(f"- {dev_name} ({dev_data['ip']}): 共{total}次诊断, 最新问题: {latest_issue[:60]}, 趋势: {trend}")
    lines.append("")
    return "\n".join(lines)


def build_llm_trend_prompt(project: str) -> str:
    summary = build_history_summary(project)
    if not summary:
        return ""
    prompt = f"""以下是{project}项目设备诊断的历史记录，请进行趋势分析：

{summary}

请分析：
1. 哪些设备的问题在持续恶化？
2. 是否有多台设备同时出现相似问题（可能是共性问题）？
3. 哪些设备需要优先处理？
4. 给出整体评价和建议

请用中文回答，简洁专业。"""
    return prompt
