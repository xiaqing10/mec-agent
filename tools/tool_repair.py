import json
import re
import logging
from datetime import datetime
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

REPAIR_LOG_DIR = Path(__file__).parent.parent / "repair_logs"
REPAIR_LOG_DIR.mkdir(exist_ok=True)

_ALLOWED_ACTIONS = {
    "restart_container": {
        "cmd": "docker restart {target}",
        "desc": "重启容器",
        "param_label": "容器名",
    },
    "restart_process": {
        "cmd": "supervisorctl restart {target}",
        "desc": "重启进程",
        "param_label": "进程名",
    },
    "restart_service": {
        "cmd": "systemctl restart {target}",
        "desc": "重启系统服务",
        "param_label": "服务名",
    },
    "clear_cache": {
        "cmd": "sync && echo 3 > /proc/sys/vm/drop_caches",
        "desc": "清理内存缓存",
        "param_label": None,
    },
    "vacuum_journal": {
        "cmd": "journalctl --vacuum-size=200M",
        "desc": "清理系统日志（保留200M）",
        "param_label": None,
    },
    "clean_temp": {
        "cmd": "find /tmp -type f -mtime +7 -delete",
        "desc": "清理7天前的临时文件",
        "param_label": None,
    },
}

_TARGET_PATTERN = re.compile(r'^[a-zA-Z0-9_\-\.]+$')


def _validate_target(target: str) -> bool:
    if not target:
        return False
    if len(target) > 128:
        return False
    return bool(_TARGET_PATTERN.match(target))


def _log_repair(ip: str, action: str, target: str, command: str, result: str, success: bool):
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "ip": ip,
        "action": action,
        "target": target,
        "command": command,
        "result": result[:2000],
        "success": success,
    }
    log_file = REPAIR_LOG_DIR / f"repair_{datetime.now().strftime('%Y%m%d')}.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


@tool
def mec_repair_device(ip: str, action: str, target: str = "") -> str:
    """在MEC设备上执行安全的修复操作。

    支持的操作：
    - restart_container: 重启Docker容器（需提供容器名）
    - restart_process: 重启supervisor进程（需提供进程名）
    - restart_service: 重启系统服务（需提供服务名）
    - clear_cache: 清理内存缓存（无需target）
    - vacuum_journal: 清理系统日志保留200M（无需target）
    - clean_temp: 清理7天前的临时文件（无需target）

    注意：此工具仅生成修复方案，不会自动执行。需要用户在前端弹窗确认后才会执行。

    Args:
        ip: 设备IP地址
        action: 修复动作，必须是 restart_container/restart_process/restart_service/clear_cache/vacuum_journal/clean_temp 之一
        target: 操作目标（容器名/进程名/服务名），clear_cache/vacuum_journal/clean_temp 不需要
    """
    from diagnose_mec import ssh_exec, find_physical_user, _resolve_device, _get_device_credentials, CONTAINER_PORT, CONTAINER_USER

    if action not in _ALLOWED_ACTIONS:
        return json.dumps({
            "error": f"不支持的操作 '{action}'，允许的操作: {', '.join(_ALLOWED_ACTIONS.keys())}",
            "suggestions": [
                {"action": a, "desc": info["desc"], "needs_target": info["param_label"] is not None}
                for a, info in _ALLOWED_ACTIONS.items()
            ]
        }, ensure_ascii=False)

    action_info = _ALLOWED_ACTIONS[action]
    needs_target = action_info["param_label"] is not None

    if needs_target:
        if not _validate_target(target):
            return json.dumps({
                "error": f"参数 '{target}' 不合法，只允许字母数字下划线横线点号，最长128字符",
                "suggestion": {"action": action, "desc": action_info["desc"], "param": action_info["param_label"]}
            }, ensure_ascii=False)
        if action == "restart_container" and target != "dev":
            target = "dev"

    if not re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
        resolved_ip, _ = _resolve_device(ip)
        if resolved_ip != ip:
            ip = resolved_ip
    if not re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
        return json.dumps({"error": f"无法解析设备 '{ip}'"}, ensure_ascii=False)

    if needs_target:
        command = action_info["cmd"].format(target=target)
    else:
        command = action_info["cmd"]

    return json.dumps({
        "status": "pending_confirmation",
        "device_ip": ip,
        "action": action,
        "action_desc": action_info["desc"],
        "target": target,
        "command": command,
        "message": f"修复方案已生成：{action_info['desc']}" + (f" ({target})" if target else "") + f"，请在弹窗中确认执行。\n命令: {command}",
    }, ensure_ascii=False)


def execute_repair(ip: str, action: str, target: str = "") -> dict:
    """Execute a confirmed repair action. Called from the API handler after user confirmation."""
    from diagnose_mec import ssh_exec, find_physical_user, _resolve_device, _get_device_credentials, CONTAINER_PORT, CONTAINER_USER

    if action not in _ALLOWED_ACTIONS:
        return {"success": False, "error": f"不支持的操作 '{action}'"}

    action_info = _ALLOWED_ACTIONS[action]
    needs_target = action_info["param_label"] is not None

    if needs_target and not _validate_target(target):
        return {"success": False, "error": f"参数 '{target}' 不合法"}

    if not re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
        resolved_ip, _ = _resolve_device(ip)
        if resolved_ip != ip:
            ip = resolved_ip
    if not re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
        return {"success": False, "error": f"无法解析设备 '{ip}'"}

    if needs_target:
        command = action_info["cmd"].format(target=target)
    else:
        command = action_info["cmd"]

    user, method = find_physical_user(ip)
    port = 22
    ssh_password = ""
    if method == "password":
        creds = _get_device_credentials(ip)
        ssh_password = creds.get("pm_password") or creds.get("password", "")
    if not user:
        return {"success": False, "error": f"物理机不可达: {ip}"}

    from diagnose_mec import _docker_cmd, _docker_exec_cmd

    if action == "restart_container":
        command = _docker_cmd(user, command)
        stdout, stderr, rc = ssh_exec(ip, port, user, command, exec_timeout=30, password=ssh_password)
    elif action == "restart_process":
        stdout, stderr, rc = _docker_exec_cmd(ip, user, command, exec_timeout=30, password=ssh_password)
    else:
        stdout, stderr, rc = ssh_exec(ip, port, user, command, exec_timeout=30, password=ssh_password)

    if rc != 0 or ("Permission denied" in stderr and "Identity file" not in stderr):
        result = (stdout + "\n" + stderr).strip()
        _log_repair(ip, action, target, command, result, False)
        return {"success": False, "error": f"执行失败 (exit={rc})", "output": result[:2000]}

    result = stdout.strip() if stdout.strip() else "执行成功（无输出）"
    _log_repair(ip, action, target, command, result, True)
    return {"success": True, "output": result[:2000], "command": command}