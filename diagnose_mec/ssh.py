import logging
import subprocess
import asyncio
import socket
import shlex
import time
import concurrent.futures
import re
from pathlib import Path

from config import SSH_KEY_PATH,SSH_CMD_PATH, CONTAINER_SSH_PORT, CONTAINER_SSH_USER, PHYSICAL_SSH_USERS

logger = logging.getLogger("diagnose_mec.ssh")

SSH_CMD = SSH_CMD_PATH
SSH_KEY = SSH_KEY_PATH

CONTAINER_PORT = CONTAINER_SSH_PORT
CONTAINER_USER = CONTAINER_SSH_USER
PHYSICAL_USERS = PHYSICAL_SSH_USERS
SUDO_USERS = {"lcfc", "nvidia"}
ROS_ENV_CMD = "source /home/files/rvf/setup.bash 2>/dev/null || source /home/files/install/setup.bash 2>/dev/null || source /opt/ros/noetic/setup.bash 2>/dev/null"

_ssh_pool = None


def _get_ssh_pool():
    global _ssh_pool
    if _ssh_pool is None or _ssh_pool._shutdown:
        _ssh_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    return _ssh_pool


def ssh_exec(host_ip: str, port: int, user: str, command: str, exec_timeout: int = 30, password: str = "") -> tuple:
    connect_timeout = 5

    if password:
        sshpass_available = True
        try:
            sp_cmd = [
                "sshpass", "-p", password, SSH_CMD,
                "-o", "StrictHostKeyChecking=no",
                "-o", "BatchMode=yes",
                "-o", f"ConnectTimeout={connect_timeout}",
                "-o", "ServerAliveInterval=2",
                "-o", "ServerAliveCountMax=3",
                "-p", str(port),
                f"{user}@{host_ip}",
                command
            ]
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None and loop.is_running():
                cf = _get_ssh_pool().submit(lambda: subprocess.run(sp_cmd, capture_output=True, text=True, timeout=exec_timeout))
                result = cf.result(timeout=exec_timeout + 5)
            else:
                result = subprocess.run(sp_cmd, capture_output=True, text=True, timeout=exec_timeout)
            if result.returncode == 0:
                return result.stdout.strip(), result.stderr.strip(), result.returncode
            logger.debug("sshpass返回非零(%d): %s", result.returncode, result.stderr.strip()[:200])
        except FileNotFoundError:
            logger.debug("sshpass未安装，回退paramiko")
            sshpass_available = False
        except Exception as e:
            logger.debug("sshpass执行异常，回退paramiko: %s", e)

        try:
            import paramiko
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                hostname=host_ip, port=port, username=user, password=password,
                timeout=connect_timeout, banner_timeout=connect_timeout,
                allow_agent=False, look_for_keys=False,
            )
            try:
                stdin_fd, stdout_fd, stderr_fd = client.exec_command(command, timeout=exec_timeout)
                stdout = stdout_fd.read().decode('utf-8', errors='replace').strip()
                stderr = stderr_fd.read().decode('utf-8', errors='replace').strip()
                return stdout, stderr, stdout_fd.channel.recv_exit_status()
            finally:
                client.close()
        except Exception as e:
            logger.debug("Paramiko密码登录失败 %s@%s:%d - %s", user, host_ip, port, e)
            return "", str(e), -1

    cmd = [
        SSH_CMD,
        "-i", SSH_KEY,
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        "-o", "PasswordAuthentication=no",
        "-o", f"ConnectTimeout={connect_timeout}",
        "-o", "ServerAliveInterval=2",
        "-o", "ServerAliveCountMax=3",
        "-p", str(port),
        f"{user}@{host_ip}",
        command
    ]

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            cf = _get_ssh_pool().submit(lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=exec_timeout))
            result = cf.result(timeout=exec_timeout + 5)
            return result.stdout.strip(), result.stderr.strip(), result.returncode
        else:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=exec_timeout)
            return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        logger.debug("SSH执行超时: %s@%s:%d (%ds)", user, host_ip, port, exec_timeout)
        return "", f"SSH执行超时({exec_timeout}s)", -1
    except Exception as e:
        logger.debug("SSH异常: %s@%s:%d - %s", user, host_ip, port, e)
        return "", str(e), -1


def _combined_ssh(host_ip: str, port: int, user: str, commands: list, exec_timeout: int = 30, password: str = "") -> dict:
    marker = "===MKR==="
    parts = []
    for name, cmd in commands:
        parts.append(f"echo '{marker}{name}' && ({cmd}) 2>&1")
    full_cmd = "; ".join(parts)

    stdout, _, _ = ssh_exec(host_ip, port, user, full_cmd, exec_timeout=exec_timeout, password=password)

    if not stdout.strip() and password:
        logger.warning("_combined_ssh 合并命令返回空，用 echo 确认 SSH 连通性...")
        test_out, _, _ = ssh_exec(host_ip, port, user, "echo 'OK'", exec_timeout=10, password=password)
        if test_out.strip() != "OK":
            logger.warning("_combined_ssh SSH 连通性异常，跳过逐条重试")
            return {}
        logger.warning("_combined_ssh SSH 连通性正常，逐条重试关键命令（最多 %d 条）", len(commands))
        result = {}
        for name, cmd in commands:
            single_out, _, _ = ssh_exec(host_ip, port, user, cmd, exec_timeout=min(exec_timeout, 15), password=password)
            result[name] = single_out.strip()
        return result

    result = {}
    current_name = None
    current_lines = []
    for line in stdout.split('\n'):
        if line.startswith(marker):
            if current_name:
                result[current_name] = '\n'.join(current_lines).strip()
            current_name = line[len(marker):]
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)
    if current_name:
        result[current_name] = '\n'.join(current_lines).strip()
    return result


def _docker_cmd(physical_user: str, cmd: str) -> str:
    if physical_user in SUDO_USERS:
        return f"sudo {cmd}"
    return cmd


def _docker_exec_cmd(host_ip: str, physical_user: str, command: str, exec_timeout: int = 30, password: str = "") -> tuple:
    wrapped = f'docker exec dev bash -l -c {shlex.quote(command)}'
    exec_cmd = _docker_cmd(physical_user, wrapped)
    return ssh_exec(host_ip, 22, physical_user, exec_cmd, exec_timeout=exec_timeout, password=password)


def _get_device_credentials(host_ip: str) -> dict:
    try:
        from query_sensor_status import _get_conn
        conn = _get_conn()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT md.username, md.password, pm.username AS pm_username, pm.password AS pm_password
                FROM mec_device md
                LEFT JOIN physical_machine pm ON md.physical_machine_id = pm.id
                WHERE md.host = %s
                """,
                (host_ip,),
            )
            row = cursor.fetchone()
            if row:
                return {
                    "username": row["username"] or "",
                    "password": row["password"] or "",
                    "pm_username": row["pm_username"] or "",
                    "pm_password": row["pm_password"] or "",
                }
        conn.close()
    except Exception as e:
        logger.debug("查询设备凭据失败: %s", e)
    return {}


import errno as _errno

_ERRNO_MEANING = {
    _errno.ECONNREFUSED: "连接被拒绝(端口未监听/防火墙)",
    _errno.ETIMEDOUT: "连接超时",
    _errno.ENETUNREACH: "网络不可达",
    _errno.EHOSTUNREACH: "主机不可达",
    _errno.EAGAIN: "资源暂时不可用(可能连接数满/NAT表满/防火墙限流)",
    _errno.EHOSTDOWN: "主机已关机",
}

def _quick_port_check(host_ip: str, port: int, timeout: float = 5.0) -> bool:
    """Quick TCP port check before attempting SSH. Returns True if port is open."""
    for attempt in range(2):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((host_ip, port))
            sock.close()
            if result == 0:
                return True
            meaning = _ERRNO_MEANING.get(result, f"errno={result}")
            logger.warning("TCP 端口不可达: %s:%d (%s)", host_ip, port, meaning)
            if result in (_errno.ECONNREFUSED, _errno.EHOSTUNREACH, _errno.EHOSTDOWN):
                return False
            if attempt == 0:
                logger.warning("TCP 端口检查 1秒后重试: %s:%d", host_ip, port)
                time.sleep(1)
        except socket.timeout:
            logger.warning("TCP 端口检查超时: %s:%d (%.1fs)", host_ip, port, timeout)
            if attempt == 0:
                logger.warning("TCP 端口检查 1秒后重试: %s:%d", host_ip, port)
                time.sleep(1)
        except Exception as e:
            logger.warning("TCP 端口检查异常: %s:%d - %s", host_ip, port, e)
            return False
    return False


def _check_ssh_banner(host_ip: str, port: int = 22, timeout: float = 5.0) -> bool:
    """Check if remote port actually speaks SSH protocol by reading the banner."""
    for attempt in range(2):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((host_ip, port))
            banner = sock.recv(256)
            sock.close()
            if banner.startswith(b"SSH-"):
                return True
            logger.warning("端口 %s:%d 返回非SSH协议内容: %s", host_ip, port, banner[:100])
            return False
        except socket.timeout:
            logger.warning("SSH Banner 检查超时: %s:%d (%.1fs)", host_ip, port, timeout)
            if attempt == 0:
                logger.warning("SSH Banner 检查 1秒后重试: %s:%d", host_ip, port)
                time.sleep(1)
        except Exception as e:
            logger.warning("SSH Banner 检查异常: %s:%d - %s", host_ip, port, e)
            if attempt == 0:
                logger.warning("SSH Banner 检查 1秒后重试: %s:%d", host_ip, port)
                time.sleep(1)
    return False


def ping_host(host_ip: str, count: int = 2, timeout: int = 3) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["ping", "-c", str(count), "-W", str(timeout), host_ip],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            m = re.search(r'time[= ]+(\d+\.?\d*)\s*ms', result.stdout)
            latency = m.group(1) + "ms" if m else "?"
            return True, f"可达 (延迟 {latency})"
        return False, "不可达"
    except subprocess.TimeoutExpired:
        return False, "不可达 (ping超时)"


def find_physical_user(host_ip: str) -> tuple:
    if not _quick_port_check(host_ip, 22):
        logger.warning("物理机 %s:22 端口不可达（已重试2次），跳过所有SSH探测", host_ip)
        return "", ""
    if not _check_ssh_banner(host_ip, 22):
        logger.warning("物理机 %s:22 端口未响应SSH协议（已重试2次），跳过所有SSH探测", host_ip)
        return "", ""
    creds = _get_device_credentials(host_ip)
    db_pm_user = creds.get("pm_username", "")
    db_pm_pass = creds.get("pm_password", "")
    db_dev_pass = creds.get("password", "")

    if db_pm_user:
        try:
            stdout, stderr, code = ssh_exec(host_ip, 22, db_pm_user, "echo 'OK'", exec_timeout=10)
            if code == 0 and stdout.strip() == "OK":
                logger.info("物理机用户: %s@%s (密钥)", db_pm_user, host_ip)
                return db_pm_user, "key"
        except Exception:
            pass

        db_pass = db_pm_pass or db_dev_pass
        if db_pass:
            try:
                stdout, stderr, code = ssh_exec(host_ip, 22, db_pm_user, "echo 'OK'", exec_timeout=10, password=db_pass)
                if code == 0 and stdout.strip() == "OK":
                    logger.info("物理机用户: %s@%s (数据库密码)", db_pm_user, host_ip)
                    return db_pm_user, "password"
            except Exception:
                pass

    for pm_user in PHYSICAL_USERS:
        try:
            stdout, stderr, code = ssh_exec(host_ip, 22, pm_user, "echo 'OK'", exec_timeout=10)
            if code == 0 and stdout.strip() == "OK":
                logger.info("物理机用户: %s@%s (密钥)", pm_user, host_ip)
                return pm_user, "key"
        except Exception:
            pass

    if db_pm_pass:
        for pm_user in PHYSICAL_USERS:
            try:
                stdout, stderr, code = ssh_exec(host_ip, 22, pm_user, "echo 'OK'", exec_timeout=10, password=db_pm_pass)
                if code == 0 and stdout.strip() == "OK":
                    logger.info("物理机用户: %s@%s (密码)", pm_user, host_ip)
                    return pm_user, "password"
            except Exception:
                continue

    for pm_user in PHYSICAL_USERS[1:]:
        try:
            stdout, stderr, code = ssh_exec(host_ip, 22, pm_user, "echo 'OK'", exec_timeout=10)
            if code == 0 and stdout.strip() == "OK":
                logger.info("物理机用户: %s@%s (密钥)", pm_user, host_ip)
                return pm_user, "key"
        except Exception:
            continue

    logger.warning("物理机 %s 所有登录方式均失败，无法连接", host_ip)
    return "", ""
