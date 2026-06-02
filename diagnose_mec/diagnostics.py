import logging
import time
import re
from datetime import datetime
from pathlib import Path

from .ssh import (
    ssh_exec, _combined_ssh, _docker_cmd, _docker_exec_cmd,
    _get_device_credentials, find_physical_user, ping_host,
    CONTAINER_PORT, CONTAINER_USER, PHYSICAL_USERS, ROS_ENV_CMD,
)
from .parsers import (
    _parse_ssh_failure_reason, _load_diagnostic_patterns,
    _parse_supervisor_status, _format_abnormal_summary,
)
from query_sensor_status import get_sensor_status

logger = logging.getLogger("diagnose_mec.diagnostics")


def _add_sensor_status(result: dict, host_ip: str, project: str = ""):
    try:
        si = get_sensor_status(host_ip)
        if si.get("total_cameras", 0) > 0 or si.get("total_radars", 0) > 0:
            result["sensor_status"] = si
    except Exception:
        pass
    return result


def diagnose_container_offline(host_ip: str, progress_cb=None) -> dict:
    logger.info("=" * 70)
    logger.info("🔍 诊断：物理机在线但容器不可连 - %s", host_ip)
    logger.info("=" * 70)

    def _prog(step, detail):
        if progress_cb:
            progress_cb("物理机", "progress", f"[{step}] {detail}")

    _prog("1/4", "查找物理机登录信息...")

    result = {
        "host": host_ip,
        "type": "container_offline",
        "timestamp": datetime.now().isoformat(),
        "diagnosis": {},
        "recommendations": []
    }

    physical_user, login_method = find_physical_user(host_ip)

    is_password_login = login_method == "password"
    if is_password_login:
        creds = _get_device_credentials(host_ip)
        ssh_password = creds.get("pm_password") or creds.get("password", "")
    else:
        ssh_password = ""

    login_user = physical_user

    if not login_user:
        ping_ok, ping_info = ping_host(host_ip)
        result["diagnosis"]["physical_machine"] = f"无法连接 ❌ (Ping: {ping_info})"
        result["diagnosis"]["error"] = f"物理机无法连接：{host_ip}"
        logger.warning("❌ 物理机无法连接: %s (Ping: %s)", host_ip, ping_info)
        return _add_sensor_status(result, host_ip)

    login_method_str = "密码" if is_password_login else "公钥"
    result["diagnosis"]["physical_machine"] = f"{login_user}@{host_ip}:22 ✓ ({login_method_str})"
    result["diagnosis"]["_login_user"] = login_user
    result["diagnosis"]["_login_method"] = login_method
    result["diagnosis"]["_ssh_password"] = ssh_password

    _prog("2/4", "快速检测 Docker 是否可用...")
    logger.info("🐳 快速检测 Docker 可用性...")
    dc = _docker_cmd
    docker_check_cmd = dc(login_user, "timeout 10 docker ps -a --format='{{.Names}}|{{.Status}}' 2>&1 || echo 'DOCKER_UNAVAILABLE'")
    docker_check_stdout, _, docker_check_code = ssh_exec(
        host_ip, 22, login_user, docker_check_cmd, exec_timeout=15, password=ssh_password
    )
    docker_available = docker_check_code == 0 and "DOCKER_UNAVAILABLE" not in docker_check_stdout

    if not docker_available:
        result["diagnosis"]["docker_service"] = "未运行（docker 命令不可用）"
        result["diagnosis"]["dev_container"] = "不存在 ❌（Docker 不可用）"
        result["diagnosis"]["container_exec"] = "失败: Docker 不可用"
        result["diagnosis"]["issue"] = "Docker服务未运行"
        logger.warning("❌ Docker 不可用（docker ps 卡住或失败），跳过所有容器相关操作")
        uptime_stdout, _, _ = ssh_exec(
            host_ip, 22, login_user,
            "cat /proc/uptime | awk '{print int($1/86400)\"天 \"int(($1%86400)/3600)\"小时\"}'",
            exec_timeout=10, password=ssh_password
        )
        if uptime_stdout.strip():
            result["diagnosis"]["physical_uptime"] = uptime_stdout.strip()
        return _add_sensor_status(result, host_ip)

    _prog("3/4", "采集 Docker/容器/硬盘信息...")
    logger.info("🐳 Docker 可用，采集详细信息...")
    combined = _combined_ssh(host_ip, 22, login_user, [
        ("DOCKER_STATUS", dc(login_user, "systemctl is-active docker")),
        ("DEV_CONTAINER_ALL", dc(login_user, "docker ps -a --filter name=dev --format='{{.Names}}|{{.Status}}|{{.CreatedAt}}' 2>&1")),
        ("UPTIME", "cat /proc/uptime | awk '{print int($1/86400)\"天 \"int(($1%86400)/3600)\"小时\"}'"),
        ("DISK_ROOT", "df -h / 2>/dev/null | awk 'NR==2{print $5\" (\"$3\"/\"$2\")\"}'"),
        ("DISK_DATA", "df -h /data 2>/dev/null | awk 'NR==2{print $5\" (\"$3\"/\"$2\")\"}'"),
    ], exec_timeout=30, password=ssh_password)

    docker_status = combined.get("DOCKER_STATUS", "").strip()
    dev_container_all = combined.get("DEV_CONTAINER_ALL", "").strip()
    uptime = combined.get("UPTIME", "").strip()
    disk_root = combined.get("DISK_ROOT", "").strip()
    disk_data = combined.get("DISK_DATA", "").strip()

    _prog("4/4", "分析 Docker 服务和容器状态...")

    if dev_container_all:
        container_parts = dev_container_all.split('|')
        container_status = container_parts[1].strip() if len(container_parts) > 1 else ""
        container_created = container_parts[2].strip()[:19] if len(container_parts) > 2 else ""
        if "Up" in dev_container_all:
            result["diagnosis"]["docker_service"] = "运行中 ✓"
            result["diagnosis"]["dev_container"] = f"存在且运行中 ✓ ({container_status})"
            logger.info("✅ dev容器存在且运行中: %s", container_status)
        else:
            result["diagnosis"]["docker_service"] = "运行中 ✓"
            result["diagnosis"]["dev_container"] = f"存在但未运行 ⚠️ ({container_status})"
            result["diagnosis"]["issue"] = f"容器未运行 ({container_status})"
            logger.warning("⚠️ dev容器存在但未运行: %s", container_status)
    else:
        container_status = ""
        container_created = ""
        result["diagnosis"]["docker_service"] = "运行中 ✓"
        result["diagnosis"]["dev_container"] = "不存在 ❌"
        result["diagnosis"]["issue"] = "dev容器不存在"
        logger.warning("❌ dev容器不存在（Docker 可用但未找到 dev 容器）")

    _prog("5/4", "采集硬盘和容器启动时间...")

    if uptime:
        result["diagnosis"]["physical_uptime"] = uptime
    if disk_root:
        result["diagnosis"]["disk_root"] = disk_root
    if disk_data:
        result["diagnosis"]["disk_data"] = disk_data
    if container_status:
        result["diagnosis"]["container_status"] = container_status
    if container_created:
        result["diagnosis"]["container_started"] = container_created

    return _add_sensor_status(result, host_ip)


def diagnose_zero_images(host_ip: str, container_ssh_info=None, progress_cb=None) -> dict:
    logger.info("=" * 70)
    logger.info("🔍 诊断：容器在线但今日图片为0 - %s", host_ip)
    logger.info("=" * 70)

    def _prog(step, detail):
        if progress_cb:
            progress_cb("容器", "progress", f"[{step}] {detail}")

    _prog("1/5", "检查容器SSH连通性...")

    result = {
        "host": host_ip,
        "type": "zero_images",
        "timestamp": datetime.now().isoformat(),
        "diagnosis": {},
        "recommendations": []
    }

    logger.info("📡 步骤0: 检查容器连通性...")
    stdout, stderr, code = "", "", -1
    use_docker_exec = False
    physical_user = None
    ssh_password = ""

    if container_ssh_info:
        method = container_ssh_info.get("method", "")
        if method == "direct_key":
            logger.info("   [维度2已知] 容器公钥可用，跳过连通性检查")
            stdout, stderr, code = ssh_exec(host_ip, CONTAINER_PORT, CONTAINER_USER, "echo 'OK'", exec_timeout=10)
            container_ssh_password = ""
        elif method == "direct_password":
            logger.info("   [维度2已知] 容器密码可用，跳过公钥尝试")
            dev_password = container_ssh_info.get("password", "")
            stdout, stderr, code = ssh_exec(
                host_ip, CONTAINER_PORT, CONTAINER_USER, "echo 'OK'",
                exec_timeout=10, password=dev_password
            )
            container_ssh_password = dev_password if dev_password else ""
        else:
            container_ssh_info = None

    if not container_ssh_info:
        stdout, stderr, code = ssh_exec(host_ip, CONTAINER_PORT, CONTAINER_USER, "echo 'OK'", exec_timeout=10)

        if code != 0 or "OK" not in stdout:
            logger.info("   容器SSH公钥直连失败，尝试密码登录...")
            creds = _get_device_credentials(host_ip)
            dev_password = creds.get("password", "")
            if dev_password:
                stdout, stderr, code = ssh_exec(
                    host_ip, CONTAINER_PORT, CONTAINER_USER, "echo 'OK'",
                    exec_timeout=10, password=dev_password
                )
        if code == 0 and "OK" in stdout:
            container_ssh_password = dev_password if 'dev_password' in dir() and dev_password else ""
            result["diagnosis"]["_ssh_password"] = container_ssh_password

    if code == 0 and "OK" in stdout:
        result["diagnosis"]["_ssh_password"] = container_ssh_password

    if code != 0 or "OK" not in stdout:
        logger.info("  容器SSH直连失败，尝试通过物理机docker exec...")
        physical_user, login_method = find_physical_user(host_ip)
        is_password_login = login_method == "password"
        login_user = physical_user

        if login_user and (login_user in PHYSICAL_USERS or is_password_login):
            if is_password_login:
                creds = _get_device_credentials(host_ip)
                ssh_password = creds.get("pm_password") or creds.get("password", "")
            dc = _docker_cmd
            exec_test_stdout, _, exec_test_code = ssh_exec(
                host_ip, 22, login_user,
                dc(login_user, "docker exec dev bash -l -c 'echo OK' 2>&1"),
                exec_timeout=10, password=ssh_password
            )
            if exec_test_code == 0 and "OK" in exec_test_stdout:
                logger.info("  ✅ 物理机docker exec可用，通过docker exec诊断")
                use_docker_exec = True
                result["diagnosis"]["container_ssh_fallback"] = "docker exec"
                ssh_reason_cmd = (
                    "echo '===SSHD_STATUS===' && "
                    "service ssh status 2>&1 || systemctl status sshd 2>&1 || true; "
                    "echo '===SSHD_LISTEN===' && "
                    "ss -tlnp 2>/dev/null | grep 10022 || netstat -tlnp 2>/dev/null | grep 10022 || echo 'NOT_LISTENING'; "
                    "echo '===SSHD_PROCESS===' && "
                    "ps aux | grep ssh[d] 2>/dev/null || echo 'SSHD_NOT_RUNNING'; "
                    "echo '===FIREWALL===' && "
                    "iptables -L INPUT -n 2>/dev/null | grep 10022 || echo 'NO_10022_RULE'; "
                    "echo '===SSHD_CONFIG===' && "
                    "grep -E '^Port |^PermitRootLogin |^PubkeyAuthentication |^PasswordAuthentication ' /etc/ssh/sshd_config 2>/dev/null || echo 'READ_FAILED'"
                )
                reason_stdout, reason_stderr, _ = _docker_exec_cmd(
                    host_ip, login_user, ssh_reason_cmd, exec_timeout=15, password=ssh_password
                )
                reason_detail = _parse_ssh_failure_reason(reason_stdout, reason_stderr)
                result["diagnosis"]["container_ssh_connect"] = f"不可连接 ❌ ({reason_detail})"
                result["diagnosis"]["container_ssh_failure_reason"] = reason_detail
                logger.warning("  容器SSH不可达原因: %s", reason_detail)
            else:
                logger.warning("  ❌ 物理机docker exec也失败")
        else:
            logger.warning("  ❌ 物理机无法连接")

        if not use_docker_exec:
            result["diagnosis"]["issue"] = f"容器SSH无法连接: {(stderr or stdout).strip()[:200]}"
            logger.warning("❌ 容器无法连接")
            return _add_sensor_status(result, host_ip)

    if not use_docker_exec:
        logger.info("✅ 容器连接正常")
        creds = _get_device_credentials(host_ip)
        direct_password = creds.get("password", "")
        result["_exec_ctx"] = {"method": "direct", "ssh_password": direct_password}

    _prog("2/5", "采集容器初始数据（supervisor/roscore/图片数）...")

    today_str = datetime.now().strftime("%Y-%m-%d")
    logger.info("📦 一次SSH采集容器初始数据...")

    if use_docker_exec:
        docker_cmds = (
            "echo '===SUPERVISOR===' && supervisorctl status 2>&1; "
            "echo '===ROSCORE===' && ps -ef | grep roscore | grep -v grep || echo 'ROSCORE_NOT_RUNNING'; "
            f"echo '===IMG_COUNT===' && ls /home/files/nfsroot/{today_str}/ 2>/dev/null | wc -l; "
            f"echo '===IMG_INFO===' && ls -lt --time-style='+%Y-%m-%d %H:%M:%S' /home/files/nfsroot/{today_str}/ 2>/dev/null | head -2; "
            "echo '===GREP_CONF===' && grep -hE 'stdout_logfile=|stderr_logfile=' /etc/supervisor/conf.d/*.conf 2>/dev/null | sort -u"
        )
        exec_full, _, _ = _docker_exec_cmd(host_ip, login_user, docker_cmds, exec_timeout=30, password=ssh_password)
        logger.info("DEBUG docker_cmds exec_full[:500] = %s", exec_full[:500])
        combined = {}
        for marker in ["SUPERVISOR", "ROSCORE", "IMG_COUNT", "IMG_INFO", "GREP_CONF"]:
            if f"==={marker}===" in exec_full:
                parts = exec_full.split(f"==={marker}===")
                remaining = parts[1] if len(parts) > 1 else ""
                next_marker = None
                for m in ["SUPERVISOR", "ROSCORE", "IMG_COUNT", "IMG_INFO", "GREP_CONF"]:
                    if m != marker and f"==={m}===" in remaining:
                        next_marker = f"==={m}==="
                        break
                if next_marker:
                    combined[marker] = remaining.split(next_marker)[0].strip()
                else:
                    combined[marker] = remaining.strip()
    else:
        combined = _combined_ssh(host_ip, CONTAINER_PORT, CONTAINER_USER, [
            ("SUPERVISOR", "supervisorctl status 2>&1"),
            ("ROSCORE", "ps -ef | grep roscore | grep -v grep"),
            ("IMG_COUNT", f"ls /home/files/nfsroot/{today_str}/ 2>/dev/null | wc -l"),
            ("IMG_INFO", f"ls -lt --time-style='+%Y-%m-%d %H:%M:%S' /home/files/nfsroot/{today_str}/ 2>/dev/null | head -2"),
            ("GREP_CONF", "grep -hE 'stdout_logfile=|stderr_logfile=' /etc/supervisor/conf.d/*.conf 2>/dev/null | sort -u"),
        ], exec_timeout=30, password=result.get("_exec_ctx", {}).get("ssh_password", ""))

    sv_raw = combined.get("SUPERVISOR", "").strip()
    ros_raw = combined.get("ROSCORE", "").strip()
    img_count_raw = combined.get("IMG_COUNT", "").strip()
    img_info_raw = combined.get("IMG_INFO", "").strip()
    grep_conf_raw = combined.get("GREP_CONF", "").strip()

    try:
        today_image_count = int(img_count_raw)
    except (ValueError, AttributeError):
        today_image_count = -1
    result["diagnosis"]["today_image_count"] = today_image_count

    if img_info_raw:
        for line in img_info_raw.split('\n'):
            parts = line.split()
            if len(parts) >= 7:
                result["diagnosis"]["latest_image_time"] = f"{parts[5]} {parts[6]}"
                result["diagnosis"]["latest_image_file"] = parts[7] if len(parts) > 7 else ""
                break

    if today_image_count > 0:
        result["diagnosis"]["issue"] = f"设备已恢复正常，今日图片数: {today_image_count}"
        logger.info("✅ 今日图片数: %d，已恢复正常，继续采集完整信息", today_image_count)
    elif today_image_count == 0:
        logger.info("⚠️ 确认今日图片为0，继续诊断")
    else:
        logger.info("⚠️ 无法获取图片数，继续诊断")

    _prog("3/5", "分析Supervisor进程状态和roscore...")

    if sv_raw:
        processes, abnormal_processes, running_count = _parse_supervisor_status(sv_raw)
        result["diagnosis"]["supervisor"] = {
            "total": len(processes), "running": running_count, "abnormal": len(abnormal_processes)
        }
        result["diagnosis"]["supervisor_output"] = sv_raw
        if abnormal_processes:
            result["diagnosis"]["abnormal_processes"] = abnormal_processes
            result["diagnosis"]["issue"] = _format_abnormal_summary(abnormal_processes)
            logger.warning("❌ %s", result["diagnosis"]["issue"])
        else:
            logger.info("✅ 所有进程运行正常 (%d/%d)", running_count, len(processes))
    else:
        logger.info("⚠️ supervisorctl第一次无输出，重试一次...")
        if use_docker_exec:
            retry_stdout, _, _ = _docker_exec_cmd(
                host_ip, login_user,
                "supervisorctl status 2>&1",
                exec_timeout=15, password=ssh_password
            )
        else:
            retry_stdout, _, _ = ssh_exec(
                host_ip, CONTAINER_PORT, CONTAINER_USER,
                "supervisorctl status 2>&1", exec_timeout=15
            )
        retry_raw = retry_stdout.strip()
        if retry_raw:
            sv_raw = retry_raw
            processes, abnormal_processes, running_count = _parse_supervisor_status(retry_raw)
            result["diagnosis"]["supervisor"] = {
                "total": len(processes), "running": running_count, "abnormal": len(abnormal_processes)
            }
            result["diagnosis"]["supervisor_output"] = retry_raw
            if abnormal_processes:
                result["diagnosis"]["abnormal_processes"] = abnormal_processes
                result["diagnosis"]["issue"] = _format_abnormal_summary(abnormal_processes)
                logger.warning("❌ 重试后仍有异常进程: %s", result["diagnosis"]["issue"])
            else:
                logger.info("✅ 重试成功，所有进程运行正常 (%d/%d)", running_count, len(processes))
        else:
            result["diagnosis"]["supervisor"] = "异常：无输出"
            if "issue" not in result["diagnosis"]:
                result["diagnosis"]["issue"] = "supervisorctl status无输出，Supervisor服务异常"
            logger.warning("❌ supervisorctl重试后仍无输出")

    if ros_raw:
        result["diagnosis"]["roscore"] = f"运行中: {ros_raw[:150]}"
        logger.info("✅ roscore运行中")
    else:
        logger.info("⚠️ roscore第1轮未检测到，1秒后重试...")
        if use_docker_exec:
            retry_ros_out, _, _ = _docker_exec_cmd(
                host_ip, login_user,
                "ps -ef | grep roscore | grep -v grep || echo 'ROSCORE_NOT_RUNNING'",
                exec_timeout=15, password=ssh_password
            )
        else:
            retry_ros_out, _, _ = ssh_exec(
                host_ip, CONTAINER_PORT, CONTAINER_USER,
                "ps -ef | grep roscore | grep -v grep || echo 'ROSCORE_NOT_RUNNING'",
                exec_timeout=15, password=result.get("_exec_ctx", {}).get("ssh_password", "")
            )
        if retry_ros_out.strip() and "ROSCORE_NOT_RUNNING" not in retry_ros_out:
            result["diagnosis"]["roscore"] = f"运行中: {retry_ros_out.strip()[:150]}"
            logger.info("✅ [重试] roscore运行中")
        else:
            result["diagnosis"]["roscore"] = "未运行"
            if "issue" not in result["diagnosis"]:
                result["diagnosis"]["issue"] = "roscore未运行"
            logger.warning("❌ roscore未运行（两轮均未检测到）")

    if grep_conf_raw:
        result["diagnosis"]["_grep_conf_raw"] = grep_conf_raw

    _prog("4/5", "检查各进程日志（检查log/err文件）...")

    logger.info("📋 步骤3: 检查各进程日志...")
    if use_docker_exec:
        result["_exec_ctx"] = {"method": "docker_exec", "login_user": login_user, "ssh_password": ssh_password}
    result, has_log_errors, error_category = _check_process_logs(host_ip, result)
    result.pop("_exec_ctx", None)
    if error_category:
        result["diagnosis"]["error_category"] = error_category
    if has_log_errors and "issue" not in result["diagnosis"]:
        patterns_config = _load_diagnostic_patterns()
        conclusion_from_config = None
        for pat in patterns_config:
            if pat["category"] == error_category:
                conclusion_from_config = pat.get("conclusion")
                break
        if conclusion_from_config and error_category != "process":
            procs = list(result["diagnosis"]["log_errors"].keys())
            result["diagnosis"]["issue"] = conclusion_from_config.format(procs=", ".join(procs))
        else:
            error_summary = []
            for proc_name, info in result["diagnosis"]["log_errors"].items():
                cat = info.get("error_category", "")
                if cat and cat != "process":
                    error_summary.append(f"{proc_name}({cat})")
                else:
                    error_summary.append(f"{proc_name}({len(info['errors'])}条错误)")
            result["diagnosis"]["issue"] = f"进程错误: {', '.join(error_summary)}"
        logger.warning("❌ %s", result["diagnosis"]["issue"])

    _prog("5/5", "检查ROS topic频率...")

    logger.info("📡 步骤4: rostopic hz 检查...")
    if use_docker_exec:
        result["_exec_ctx"] = {"method": "docker_exec", "login_user": login_user, "ssh_password": ssh_password}
    elif not result.get("_exec_ctx"):
        _tmp_creds = _get_device_credentials(host_ip)
        result["_exec_ctx"] = {"method": "direct", "ssh_password": _tmp_creds.get("password", "")}
    result = _check_rostopic_hz(host_ip, result, bool(result.get("diagnosis", {}).get("log_errors")))
    result.pop("_exec_ctx", None)

    if "issue" not in result["diagnosis"]:
        if today_image_count == 0:
            result["diagnosis"]["issue"] = "topic有数据，但图片为0"
        elif today_image_count < 0:
            result["diagnosis"]["issue"] = "无法获取图片数"

    return _add_sensor_status(result, host_ip)


def _check_process_logs(host_ip: str, result: dict):
    ctx = result.get("_exec_ctx", {})
    if ctx.get("method") == "docker_exec":
        _run_in_container = lambda cmd: _docker_exec_cmd(
            host_ip, ctx["login_user"], cmd, exec_timeout=20, password=ctx.get("ssh_password", "")
        )
    else:
        _run_in_container = lambda cmd: ssh_exec(
            host_ip, CONTAINER_PORT, CONTAINER_USER, cmd,
            exec_timeout=20, password=ctx.get("ssh_password", "")
        )

    stdout_conf, _, _ = _run_in_container(
        "grep -hE 'stdout_logfile=|stderr_logfile=' /etc/supervisor/conf.d/*.conf 2>/dev/null | sort -u"
    )

    log_files = {}
    for line in stdout_conf.strip().split('\n'):
        for log_type in ['stderr_logfile=', 'stdout_logfile=']:
            if log_type in line:
                path = line.split(log_type)[1].strip()
                basename = path.split('/')[-1].replace('.err', '').replace('.log', '')
                if basename not in log_files:
                    log_files[basename] = []
                log_files[basename].append(path)

    if not log_files:
        log_files = {
            "rtsp": ["/home/files/common_logs/rtsp.err", "/home/files/common_logs/rtsp.log"],
            "infer": ["/home/files/common_logs/infer.err", "/home/files/common_logs/infer.log"],
            "traffic": ["/home/files/common_logs/traffic.err", "/home/files/common_logs/traffic.log"],
            "kafka_event": ["/home/files/common_logs/kafka_event.err", "/home/files/common_logs/kafka_event.log"],
            "kafka_flow": ["/home/files/common_logs/kafka_flow.err", "/home/files/common_logs/kafka_flow.log"],
        }

    KAFKA_EXTRA_LOGS = {
        "kafka_event": "/home/files/common_logs/event.log",
        "kafka_flow": "/home/files/common_logs/flow.log",
    }
    for proc_name, extra_path in KAFKA_EXTRA_LOGS.items():
        if proc_name in log_files:
            log_files[proc_name].append(extra_path)
            logger.debug("为 %s 补充额外日志: %s", proc_name, extra_path)

    result["diagnosis"]["log_errors"] = {}
    has_log_errors = False
    error_category = None

    patterns_config = _load_diagnostic_patterns()

    config_grep_keywords = []
    for pat in patterns_config:
        if pat.get("grep_in_logs", True):
            config_grep_keywords.extend(pat["keywords"])
    config_grep_pattern = "|".join(config_grep_keywords)

    base_error_pattern = (
        "error|fatal|failed|exception|traceback|"
        "Runtime context is not initialized|CUDA out of memory|"
        "host is unreachable|Connection refused|No such file or directory"
    )

    if config_grep_pattern:
        grep_pattern = f"{base_error_pattern}|{config_grep_pattern}"
    else:
        grep_pattern = base_error_pattern

    proc_path_map = {}
    for proc_name, paths in log_files.items():
        for log_path in paths:
            proc_path_map[log_path] = proc_name

    shell_cmds = []
    for log_path, proc_name in proc_path_map.items():
        shell_cmds.append(
            f"if [ -f {log_path} ]; then "
            f"echo '__START__{proc_name}::{log_path}'; "
            f"tail -500 {log_path} 2>/dev/null | grep -iE '{grep_pattern}' | sort -u | head -20; "
            f"echo '__END__{proc_name}::{log_path}'; "
            f"fi"
        )
    combined_cmd = " ; ".join(shell_cmds) if shell_cmds else "echo '__NO_LOGS__'"

    stdout, _, _ = _run_in_container(combined_cmd)

    proc_errors_map = {}
    proc_seen_map = {}
    checked_files_set = set()
    current_proc = None
    current_path = None

    if stdout.strip() and "__NO_LOGS__" not in stdout:
        for line in stdout.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            if line.startswith('__START__'):
                marker = line[len('__START__'):]
                parts = marker.split('::', 1)
                if len(parts) == 2:
                    current_proc = parts[0]
                    current_path = parts[1]
                    checked_files_set.add(current_path)
                    if current_proc not in proc_errors_map:
                        proc_errors_map[current_proc] = []
                        proc_seen_map[current_proc] = set()
                continue
            if line.startswith('__END__'):
                current_proc = None
                current_path = None
                continue
            if current_proc is None:
                continue
            clean = re.sub(r'\x1b\[[0-9;]*m', '', line.strip())
            if not clean:
                continue
            dedup_key = re.sub(r'\[\d+\.\d+\]', '[TIMESTAMP]', clean)[:80]
            if dedup_key not in proc_seen_map[current_proc]:
                proc_seen_map[current_proc].add(dedup_key)
                proc_errors_map[current_proc].append((current_path, clean[:150]))

    compiled_patterns = []
    for pat in patterns_config:
        if not pat.get("grep_in_logs", True):
            continue
        regex = re.compile("|".join(pat["keywords"]), re.IGNORECASE)
        compiled_patterns.append((pat, regex))

    matched_pattern = None
    matched_procs = {}

    for pat, regex in compiled_patterns:
        procs_with_this = []
        for proc_name, error_list in proc_errors_map.items():
            errors = [e for _, e in error_list]
            if any(regex.search(e) for e in errors):
                applicable = pat.get("applicable_procs", ["all"])
                if "all" in applicable or proc_name in applicable:
                    procs_with_this.append(proc_name)
        if procs_with_this:
            matched_pattern = pat
            matched_procs[pat["category"]] = procs_with_this
            break

    if matched_pattern:
        cat = matched_pattern["category"]
        error_category = cat
        procs = matched_procs[cat]

        if cat == "ros_master":
            ros_regex = re.compile("|".join(matched_pattern["keywords"]), re.IGNORECASE)
            for proc_name in procs:
                errors = [e for _, e in proc_errors_map[proc_name]]
                filtered = [e for e in errors if ros_regex.search(e)]
                result["diagnosis"]["log_errors"][proc_name] = {
                    "log_files": [p for p in log_files.get(proc_name, []) if p in checked_files_set],
                    "errors": filtered[:4],
                    "error_category": cat
                }
            conclusion = matched_pattern["conclusion"].format(procs=", ".join(procs))
            logger.warning("❌ %s, 涉及进程: %s", conclusion, ', '.join(procs))
        else:
            for proc_name in procs:
                errors = [e for _, e in proc_errors_map[proc_name]]
                result["diagnosis"]["log_errors"][proc_name] = {
                    "log_files": [p for p in log_files.get(proc_name, []) if p in checked_files_set],
                    "errors": errors[:8],
                    "error_category": cat
                }
            logger.warning("❌ %s, 涉及进程: %s", matched_pattern["conclusion"], ', '.join(procs))

        has_log_errors = True

    else:
        for proc_name in log_files.keys():
            checked_files = [p for p in log_files[proc_name] if p in checked_files_set]
            errors = [e for _, e in proc_errors_map.get(proc_name, [])]

            if errors:
                result["diagnosis"]["log_errors"][proc_name] = {
                    "log_files": checked_files,
                    "errors": errors[:8]
                }
                has_log_errors = True
                if error_category is None:
                    error_category = "process"
                logger.info("❌ %s: %d种不同错误 (来自%s)", proc_name, len(errors), checked_files)
                for el in errors[:5]:
                    logger.debug("  • %s", el[:100])
            elif checked_files:
                logger.info("✅ %s: 无明显错误", proc_name)
            else:
                logger.info("⏭️  %s: 日志文件不存在", proc_name)

    if error_category is None:
        for pat in patterns_config:
            if pat.get("grep_in_logs", True):
                continue
            check_cmd = pat.get("check_cmd")
            if not check_cmd:
                continue
            stdout_dmesg, _, _ = _run_in_container(check_cmd)
            if stdout_dmesg.strip():
                error_category = pat["category"]
                result["diagnosis"]["log_errors"]["_system"] = {
                    "log_files": ["dmesg"],
                    "errors": stdout_dmesg.strip().split("\n")[:4],
                    "error_category": pat["category"]
                }
                has_log_errors = True
                logger.warning("❌ %s (dmesg检测)", pat["conclusion"])
                break

    return result, has_log_errors, error_category


def _check_rostopic_hz(host_ip: str, result: dict, has_log_errors: bool) -> dict:
    result["diagnosis"]["topic_rates"] = {}

    ctx = result.get("_exec_ctx", {})
    if ctx.get("method") == "docker_exec":
        _run_in_container = lambda cmd: _docker_exec_cmd(
            host_ip, ctx["login_user"], cmd, exec_timeout=60, password=ctx.get("ssh_password", "")
        )
    else:
        _run_in_container = lambda cmd: ssh_exec(
            host_ip, CONTAINER_PORT, CONTAINER_USER, cmd,
            exec_timeout=60, password=ctx.get("ssh_password", "")
        )

    stdout, stderr, _ = _run_in_container(
        f"{ROS_ENV_CMD} && rostopic list 2>&1"
    )

    if not stdout.strip():
        result["diagnosis"]["rostopic"] = f"rostopic list无输出 (stderr: {(stderr or '').strip()[:200]})"
        if not has_log_errors:
            result["diagnosis"]["issue"] = "rostopic list无输出，ROS环境可能异常"
        logger.warning("❌ rostopic list无输出")
        return result

    all_topics = [t.strip() for t in stdout.strip().split('\n') if t.strip()]
    result["diagnosis"]["ros_topics"] = all_topics

    TOPIC_OWNER = {
        'track_object':                 ('radar_bridge', 'lj'),
        'image_raw':                    ('rtsp',          'zzm'),
        'track_object_project':         ('calibration',   'cy'),
        'image_detect/compressed':      ('infer',         'xq'),
        'fusion_track_object':          ('fusion',        'pj'),
        'traffic_event_object/fps_hz':  ('traffic',       'zrh'),
    }

    def _find_owner(topic):
        for suffix, (proc, person) in TOPIC_OWNER.items():
            if topic.endswith(suffix):
                return proc, person
        return None, None

    key_topics = [t for t in all_topics if any(t.endswith(s) for s in TOPIC_OWNER)]

    if not key_topics:
        result["diagnosis"]["rostopic"] = f"无关键数据topic，现有: {all_topics[:5]}"
        if not has_log_errors:
            result["diagnosis"]["issue"] = f"无关键数据topic，当前topic: {', '.join(all_topics[:5])}"
        logger.warning("❌ 无关键数据topic，当前: %s", ', '.join(all_topics[:5]))
        return result

    topics_to_check = key_topics
    logger.info("关键topic: %s", ', '.join(topics_to_check))

    BATCH_SIZE = 5
    topic_output = {}
    for batch_start in range(0, len(topics_to_check), BATCH_SIZE):
        batch = topics_to_check[batch_start:batch_start + BATCH_SIZE]
        check_parts = []
        for topic in batch:
            safe_topic_name = re.sub(r'[^a-zA-Z0-9_]', '_', topic)
            check_parts.append(
                f"({ROS_ENV_CMD} && timeout 15 rostopic hz {topic} "
                f"> /tmp/hz_{safe_topic_name}.txt 2>&1) &"
            )
        collect_parts = []
        for topic in batch:
            safe_topic_name = re.sub(r'[^a-zA-Z0-9_]', '_', topic)
            collect_parts.append(
                f"echo 'TOPIC:{topic}' && cat /tmp/hz_{safe_topic_name}.txt 2>/dev/null && echo '---END---'"
            )
        batch_cmd = " ".join(check_parts) + " wait 2>/dev/null; " + "; ".join(collect_parts)
        stdout, _, _ = _run_in_container(batch_cmd)

        current_topic = None
        for line in stdout.strip().split('\n'):
            line = line.strip()
            if line.startswith('TOPIC:'):
                current_topic = line[6:]
                topic_output[current_topic] = []
            elif line == '---END---':
                current_topic = None
            elif current_topic:
                topic_output.setdefault(current_topic, []).append(line)

    zero_rate_topics = []
    for topic in topics_to_check:
        proc, person = _find_owner(topic)
        person_tag = f" @{person}" if person else ""
        topic_text = '\n'.join(topic_output.get(topic, []))
        if 'average rate' in topic_text.lower():
            rate_match = re.search(r'average rate:\s*([\d.]+)', topic_text)
            if rate_match:
                rate = rate_match.group(1)
                result["diagnosis"]["topic_rates"][topic] = f"{rate} Hz"
                logger.info("✅ %s: %s Hz", topic, rate)
            else:
                result["diagnosis"]["topic_rates"][topic] = topic_text.strip()[:80]
                logger.info("ℹ️  %s: %s", topic, topic_text.strip()[:60])
        elif 'no new messages' in topic_text.lower() or not topic_text.strip():
            result["diagnosis"]["topic_rates"][topic] = f"0 Hz (无数据){person_tag}"
            zero_rate_topics.append(topic)
            logger.warning("❌ [第1轮] %s: 无数据, 负责人%s", topic, person_tag)
        else:
            clean_text = topic_text.strip()[:60]
            if clean_text:
                result["diagnosis"]["topic_rates"][topic] = f"无数据: {clean_text}{person_tag}"
            else:
                result["diagnosis"]["topic_rates"][topic] = f"无数据{person_tag}"
            logger.warning("⚠️  %s: 无数据 - %s, 负责人%s", topic, clean_text, person_tag)

    if zero_rate_topics:
        logger.warning("第1轮 %d 个 topic 无数据，2秒后重试第2轮...", len(zero_rate_topics))
        time.sleep(2)

        retry_output = {}
        for batch_start in range(0, len(zero_rate_topics), BATCH_SIZE):
            batch = zero_rate_topics[batch_start:batch_start + BATCH_SIZE]
            check_parts = []
            for topic in batch:
                safe_topic_name = re.sub(r'[^a-zA-Z0-9_]', '_', topic)
                check_parts.append(
                    f"({ROS_ENV_CMD} && timeout 15 rostopic hz {topic} "
                    f"> /tmp/hz_retry_{safe_topic_name}.txt 2>&1) &"
                )
            collect_parts = []
            for topic in batch:
                safe_topic_name = re.sub(r'[^a-zA-Z0-9_]', '_', topic)
                collect_parts.append(
                    f"echo 'TOPIC:{topic}' && cat /tmp/hz_retry_{safe_topic_name}.txt 2>/dev/null && echo '---END---'"
                )
            retry_cmd = " ".join(check_parts) + " wait 2>/dev/null; " + "; ".join(collect_parts)
            retry_stdout, _, _ = _run_in_container(retry_cmd)

            current_topic = None
            for line in retry_stdout.strip().split('\n'):
                line = line.strip()
                if line.startswith('TOPIC:'):
                    current_topic = line[6:]
                    retry_output[current_topic] = []
                elif line == '---END---':
                    current_topic = None
                elif current_topic:
                    retry_output.setdefault(current_topic, []).append(line)

        still_zero = []
        for topic in zero_rate_topics:
            proc, person = _find_owner(topic)
            person_tag = f" @{person}" if person else ""
            topic_text = '\n'.join(retry_output.get(topic, []))
            if 'average rate' in topic_text.lower():
                rate_match = re.search(r'average rate:\s*([\d.]+)', topic_text)
                if rate_match:
                    rate = rate_match.group(1)
                    result["diagnosis"]["topic_rates"][topic] = f"{rate} Hz"
                    logger.info("✅ [第2轮] %s: %s Hz", topic, rate)
                else:
                    result["diagnosis"]["topic_rates"][topic] = topic_text.strip()[:80]
                    logger.info("ℹ️  [第2轮] %s: %s", topic, topic_text.strip()[:60])
            elif 'no new messages' in topic_text.lower() or not topic_text.strip():
                logger.warning("❌ [第2轮仍无] %s: 无数据, 负责人%s", topic, person_tag)
                still_zero.append(topic)
            else:
                clean_text = topic_text.strip()[:60]
                if clean_text:
                    result["diagnosis"]["topic_rates"][topic] = f"无数据: {clean_text}{person_tag}"
                else:
                    result["diagnosis"]["topic_rates"][topic] = f"无数据{person_tag}"
                logger.warning("⚠️  [第2轮] %s: 无数据 - %s, 负责人%s", topic, clean_text, person_tag)
                still_zero.append(topic)

        zero_rate_topics = still_zero

    if zero_rate_topics and not has_log_errors:
        mentions = []
        for t in zero_rate_topics:
            _, person = _find_owner(t)
            mentions.append(f"{t} @{person}" if person else t)
        result["diagnosis"]["issue"] = f"ROS topic无数据: {', '.join(mentions)}"

    return result


def collect_device_raw_data(host_ip: str) -> dict:
    logger.info("=" * 70)
    logger.info("📡 数据采集（LLM模式）: %s", host_ip)
    logger.info("=" * 70)

    result = {
        "host": host_ip,
        "timestamp": datetime.now().isoformat(),
        "raw_data": {}
    }
    raw = result["raw_data"]

    logger.info("🔐 步骤1: 物理机连通性...")
    physical_user, login_method = find_physical_user(host_ip)
    is_password_login = login_method == "password"
    login_user = physical_user

    if not login_user or (login_user not in PHYSICAL_USERS and not is_password_login):
        raw["physical_ssh"] = f"连接失败: {host_ip}"
        raw["container_ssh"] = "未尝试（物理机不可达）"
        logger.warning("❌ 物理机无法连接")
        return _add_sensor_status(result, host_ip)

    login_method = "密码" if is_password_login else "公钥"
    raw["physical_ssh"] = f"{login_user}@{host_ip}:22 连接成功 ({login_method})"

    if is_password_login:
        creds = _get_device_credentials(host_ip)
        ssh_password = creds.get("pm_password") or creds.get("password", "")
    else:
        ssh_password = ""

    logger.info("📦 一次SSH采集物理机信息...")
    dc = _docker_cmd
    phys_data = _combined_ssh(host_ip, 22, login_user, [
        ("UPTIME", "cat /proc/uptime | awk '{print int($1/86400)\"天 \"int(($1%86400)/3600)\"小时\"}'"),
        ("DOCKER_STATUS", dc(login_user, "systemctl is-active docker")),
        ("DEV_CONTAINER", dc(login_user, "docker ps -a --filter name=dev --format='{{.Status}}|{{.CreatedAt}}' 2>&1")),
    ], exec_timeout=30, password=ssh_password)

    raw["physical_uptime"] = phys_data.get("UPTIME", "").strip() or "未知"
    raw["docker_status"] = phys_data.get("DOCKER_STATUS", "").strip() or "未知"
    dev_container_info = phys_data.get("DEV_CONTAINER", "").strip()
    if dev_container_info:
        parts = dev_container_info.split('|')
        raw["container_status"] = parts[0].strip() if parts else dev_container_info
        if len(parts) > 1 and parts[1].strip():
            raw["container_started_at"] = parts[1].strip()[:19]
    else:
        raw["container_status"] = "未找到容器"

    logger.info("🔑 步骤2: 容器SSH连通性...")
    ssh_stdout, ssh_stderr, ssh_code = ssh_exec(
        host_ip, CONTAINER_PORT, CONTAINER_USER, "echo 'SSH_OK'", exec_timeout=10
    )
    if ssh_code != 0 or "SSH_OK" not in ssh_stdout:
        cont_creds = _get_device_credentials(host_ip)
        cont_pass = cont_creds.get("password", "")
        if cont_pass:
            logger.info("  🔄 容器公钥失败，尝试密码登录...")
            ssh_stdout, ssh_stderr, ssh_code = ssh_exec(
                host_ip, CONTAINER_PORT, CONTAINER_USER, "echo 'SSH_OK'", exec_timeout=10, password=cont_pass
            )
    if ssh_code != 0 or "SSH_OK" not in ssh_stdout:
        raw["container_ssh"] = f"不可连接: {(ssh_stderr or ssh_stdout).strip()[:200]}"
        logger.warning("❌ 容器SSH不可连接，采集到此为止")
        return _add_sensor_status(result, host_ip)

    raw["container_ssh"] = "可连接"

    logger.info("📦 一次SSH采集容器初始数据...")
    today_str = datetime.now().strftime("%Y-%m-%d")
    ctn_data = _combined_ssh(host_ip, CONTAINER_PORT, CONTAINER_USER, [
        ("SUPERVISOR", "supervisorctl status 2>&1"),
        ("IMG_COUNT", f"ls /home/files/nfsroot/{today_str}/ 2>/dev/null | wc -l"),
        ("IMG_INFO", f"ls -lt --time-style='+%Y-%m-%d %H:%M:%S' /home/files/nfsroot/{today_str}/ 2>/dev/null | head -2"),
        ("GREP_CONF", "grep -hE 'stdout_logfile=|stderr_logfile=' /etc/supervisor/conf.d/*.conf 2>/dev/null | sort -u"),
    ], exec_timeout=30)

    raw["supervisor_raw"] = ctn_data.get("SUPERVISOR", "").strip() or "(无输出)"

    img_count_raw = ctn_data.get("IMG_COUNT", "").strip()
    try:
        raw["today_image_count"] = int(img_count_raw)
    except (ValueError, AttributeError):
        raw["today_image_count"] = -1

    img_info_raw = ctn_data.get("IMG_INFO", "").strip()
    if img_info_raw:
        for line in img_info_raw.split('\n'):
            parts = line.split()
            if len(parts) >= 7:
                raw["latest_image_time"] = f"{parts[5]} {parts[6]}"
                break

    stdout_conf = ctn_data.get("GREP_CONF", "").strip()

    log_files = {}
    for line in stdout_conf.strip().split('\n'):
        for log_type in ['stderr_logfile=', 'stdout_logfile=']:
            if log_type in line:
                path = line.split(log_type)[1].strip()
                basename = path.split('/')[-1].replace('.err', '').replace('.log', '')
                if basename not in log_files:
                    log_files[basename] = []
                log_files[basename].append(path)

    if not log_files:
        log_files = {
            "rtsp": ["/home/files/common_logs/rtsp.err", "/home/files/common_logs/rtsp.log"],
            "infer": ["/home/files/common_logs/infer.err", "/home/files/common_logs/infer.log"],
            "traffic": ["/home/files/common_logs/traffic.err", "/home/files/common_logs/traffic.log"],
            "kafka_event": ["/home/files/common_logs/kafka_event.err", "/home/files/common_logs/kafka_event.log"],
            "kafka_flow": ["/home/files/common_logs/kafka_flow.err", "/home/files/common_logs/kafka_flow.log"],
        }

    KAFKA_EXTRA = {"kafka_event": "/home/files/common_logs/event.log", "kafka_flow": "/home/files/common_logs/flow.log"}
    for proc, extra in KAFKA_EXTRA.items():
        if proc in log_files:
            log_files[proc].append(extra)

    log_parts = []
    for proc_name, paths in log_files.items():
        log_parts.append(
            f"echo '__START__{proc_name}'; "
            f"tail -50 {' '.join(paths)} 2>/dev/null; "
            f"echo '__END__{proc_name}'"
        )
    combined_cmd = " ; ".join(log_parts)
    stdout, _, _ = ssh_exec(host_ip, CONTAINER_PORT, CONTAINER_USER, combined_cmd, exec_timeout=20)

    raw["log_snippets"] = {}
    if stdout.strip():
        current_proc = None
        lines = []
        for line in stdout.strip().split('\n'):
            if line.startswith('__START__'):
                current_proc = line[len('__START__'):]
                lines = []
            elif line.startswith('__END__'):
                if current_proc:
                    raw["log_snippets"][current_proc] = '\n'.join(lines)
                current_proc = None
                lines = []
            elif current_proc is not None:
                lines.append(line)

    logger.info("📡 步骤6: rostopic检查...")
    stdout, _, _ = ssh_exec(host_ip, CONTAINER_PORT, CONTAINER_USER,
        f"{ROS_ENV_CMD} && rostopic list 2>/dev/null", exec_timeout=15)
    raw["rostopic_list"] = [t.strip() for t in stdout.strip().split('\n') if t.strip()] if stdout.strip() else []

    TOPIC_SUFFIXES = [
        'track_object', 'image_raw', 'track_object_project',
        'image_detect/compressed', 'fusion_track_object',
        'traffic_event_object/fps_hz'
    ]
    key_topics = [t for t in raw["rostopic_list"] if any(t.endswith(s) for s in TOPIC_SUFFIXES)]

    if key_topics:
        check_parts = []
        for topic in key_topics:
            safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', topic)
            check_parts.append(
                f"({ROS_ENV_CMD} && timeout 10 rostopic hz {topic} 2>&1 | tail -3) "
                f"> /tmp/hz_{safe_name}.txt 2>&1 &"
            )
        collect_parts = []
        for topic in key_topics:
            safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', topic)
            collect_parts.append(
                f"echo 'TOPIC:{topic}'; cat /tmp/hz_{safe_name}.txt 2>/dev/null; echo '---END---'"
            )
        parallel_cmd = " ".join(check_parts) + " wait; " + "; ".join(collect_parts)
        stdout, _, _ = ssh_exec(host_ip, CONTAINER_PORT, CONTAINER_USER, parallel_cmd, exec_timeout=30)

        raw["topic_rates"] = {}
        current_topic = None
        topic_output = {}
        for line in stdout.strip().split('\n'):
            line = line.strip()
            if line.startswith('TOPIC:'):
                current_topic = line[6:]
                topic_output[current_topic] = []
            elif line == '---END---':
                current_topic = None
            elif current_topic:
                topic_output.setdefault(current_topic, []).append(line)

        for topic in key_topics:
            raw["topic_rates"][topic] = '\n'.join(topic_output.get(topic, [])) or "(无输出)"

    try:
        si = get_sensor_status(host_ip)
        if si.get("total_cameras", 0) > 0 or si.get("total_radars", 0) > 0:
            raw["sensor_status"] = si
    except Exception:
        pass

    logger.info("✅ 数据采集完成")
    return result