import logging
import time
from pathlib import Path
from datetime import datetime

from .ssh import (
    ssh_exec, _combined_ssh, _docker_cmd, _docker_exec_cmd,
    _get_device_credentials, find_physical_user, ping_host,
    SSH_CMD, SSH_KEY, CONTAINER_PORT, CONTAINER_USER, PHYSICAL_USERS,
    SUDO_USERS, ROS_ENV_CMD,
)
from .parsers import (
    _parse_ssh_failure_reason, _load_diagnostic_patterns,
    _parse_supervisor_status, _format_abnormal_summary,
)
from .diagnostics import (
    diagnose_container_offline, diagnose_zero_images,
    _check_process_logs, _check_rostopic_hz,
    collect_device_raw_data, _add_sensor_status,
)
from query_sensor_status import get_sensor_status, format_sensor_status_short, lookup_device

logger = logging.getLogger("diagnose_mec")

_DOCKER_EXEC_PREFIX = "docker exec dev supervisorctl status 2>&1; docker exec dev ps -ef 2>/dev/null | grep roscore | grep -v grep; docker exec dev ls /home/files/nfsroot/ 2>/dev/null | tail -5"

LOG_DIR = Path(__file__).parent.parent / "diagnose_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

SELF_AGENT_DIR = Path(__file__).parent.parent
DIAGNOSE_DIR = SELF_AGENT_DIR / "diagnose_logs"
DIAGNOSE_DIR.mkdir(parents=True, exist_ok=True)


def cleanup_old_logs(days: int = 7) -> int:
    import os
    count = 0
    cutoff = time.time() - days * 86400
    for f in os.listdir(DIAGNOSE_DIR):
        fp = DIAGNOSE_DIR / f
        if fp.is_file() and fp.stat().st_mtime < cutoff:
            fp.unlink()
            count += 1
    logger.info("已清理 %d 个过期日志文件", count)
    return count


def _resolve_device(query: str, project: str = "") -> tuple:
    try:
        device = lookup_device(query, project)
        if device:
            return device[0]["ip"], device[0]
    except Exception:
        pass
    return query, {"host": query, "username": "", "password": "", "project": project}


__all__ = [
    "ssh_exec", "_combined_ssh", "_docker_cmd", "_docker_exec_cmd",
    "_get_device_credentials", "find_physical_user", "ping_host",
    "_parse_ssh_failure_reason", "_load_diagnostic_patterns",
    "_parse_supervisor_status", "_format_abnormal_summary",
    "diagnose_container_offline", "diagnose_zero_images",
    "_check_process_logs", "_check_rostopic_hz",
    "collect_device_raw_data", "cleanup_old_logs",
    "_add_sensor_status", "_resolve_device",
]