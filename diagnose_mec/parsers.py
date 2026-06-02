import json
import os
import logging

logger = logging.getLogger("diagnose_mec.parsers")


def _parse_ssh_failure_reason(diag_stdout: str, diag_stderr: str) -> str:
    full = (diag_stdout or "").strip()
    if not full:
        return f"探查命令无输出({(diag_stderr or '无错误').strip()[:100]})"

    reasons = []

    if "===SSHD_STATUS===" in full:
        ss = full.split("===SSHD_STATUS===")[1].split("===SSHD_LISTEN===")[0].strip()
        if "not running" in ss.lower() or "stopped" in ss.lower() or "dead" in ss.lower():
            reasons.append(f"sshd服务未运行({ss[:60]})")
        elif "could not be found" in ss.lower() or "unrecognized" in ss.lower():
            reasons.append("容器内未安装sshd服务")
        elif "running" in ss.lower() or "active" in ss.lower():
            reasons.append("sshd服务运行中")
        else:
            reasons.append(f"sshd状态未知({ss[:60]})")

    if "===SSHD_LISTEN===" in full:
        pl = full.split("===SSHD_LISTEN===")[1].split("===SSHD_PROCESS===")[0].strip()
        if "NOT_LISTENING" in pl:
            reasons.append("sshd未监听端口")
        elif "10022" in pl:
            reasons.append("10022端口在监听")
        else:
            reasons.append("sshd未监听10022端口")

    if "===SSHD_PROCESS===" in full:
        pp = full.split("===SSHD_PROCESS===")[1].split("===FIREWALL===")[0].strip()
        if "SSHD_NOT_RUNNING" in pp:
            if not any("sshd未运行" in r for r in reasons):
                reasons.append("sshd进程不存在")

    if "===FIREWALL===" in full:
        fw = full.split("===FIREWALL===")[1].split("===SSHD_CONFIG===")[0].strip()
        if "DROP" in fw or "REJECT" in fw:
            reasons.append("防火墙可能拦截了10022端口")

    if "===SSHD_CONFIG===" in full:
        sc = full.split("===SSHD_CONFIG===")[1].strip()
        if "READ_FAILED" not in sc:
            if "Port 10022" not in sc and "Port 22" not in sc:
                reasons.append("sshd_config中未配置10022端口")
            if "PasswordAuthentication no" in sc:
                reasons.append("sshd_config禁止密码登录")
            if "PubkeyAuthentication no" in sc:
                reasons.append("sshd_config禁止公钥登录")
        else:
            reasons.append("无法读取sshd配置")

    return "; ".join(reasons) if reasons else f"无法确定原因({full[:100]})"


def _load_diagnostic_patterns() -> list:
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "diagnostic_patterns.json")
    if not os.path.exists(config_path):
        logger.warning("诊断模式配置文件不存在: %s，使用空配置", config_path)
        return []
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        patterns = config.get("patterns", [])
        logger.debug("加载诊断模式 %d 条: %s", len(patterns), [p["id"] for p in patterns])
        return patterns
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("诊断模式配置文件解析失败: %s", e)
        return []


def _parse_supervisor_status(stdout: str):
    processes = []
    abnormal_processes = []
    running_count = 0

    for line in stdout.strip().split('\n'):
        line = line.strip()
        if not line:
            continue

        status_keywords = ['RUNNING', 'STOPPED', 'FATAL', 'EXITED', 'STARTING', 'BACKOFF']
        for status in status_keywords:
            if status in line:
                name = line.split(status)[0].strip()
                uptime = "未知"
                if 'uptime' in line:
                    try:
                        uptime = line.split('uptime')[-1].strip()
                    except Exception:
                        pass

                proc_info = {'name': name, 'status': status, 'uptime': uptime, 'line': line}
                processes.append(proc_info)

                if status == 'RUNNING':
                    running_count += 1
                    if uptime != "未知" and 'days' not in uptime:
                        try:
                            parts = uptime.split(':')
                            if len(parts) == 3 and int(parts[0]) == 0 and int(parts[1]) < 5:
                                abnormal_processes.append({**proc_info, 'status': 'FREQ_RESTART'})
                        except Exception:
                            pass
                elif status in ['FATAL', 'STOPPED', 'STARTING', 'BACKOFF']:
                    abnormal_processes.append(proc_info)
                break

    return processes, abnormal_processes, running_count


def _format_abnormal_summary(abnormal_processes: list) -> str:
    if not abnormal_processes:
        return "无异常进程"

    parts = []
    for p in abnormal_processes:
        status_map = {
            'FATAL': 'FATAL',
            'STOPPED': '已停止',
            'STARTING': '启动中',
            'BACKOFF': '启动失败',
            'FREQ_RESTART': '频繁重启',
        }
        label = status_map.get(p['status'], p['status'])
        parts.append(f"{p['name']}({label})")
    return ", ".join(parts)