import logging
import subprocess
import asyncio
import socket
import shlex
import time
import concurrent.futures
import re
from pathlib import Path

from config import SSH_KEY_PATH, SSH_CMD_PATH, CONTAINER_SSH_PORT, CONTAINER_SSH_USER, PHYSICAL_SSH_USERS, PHYSICAL_SSH_USERS_ENABLED

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

    if SSH_KEY:
        cmd[1:1] = ["-i", SSH_KEY]

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


async def async_ssh_exec(host_ip: str, port: int, user: str, command: str, exec_timeout: int = 30, password: str = "") -> tuple:
    """Non-blocking adapter for synchronous SSH operations used by HTTP handlers."""
    return await asyncio.to_thread(
        ssh_exec, host_ip, port, user, command, exec_timeout, password
    )


def resolve_device_access(host_ip: str) -> dict:
    """Resolve one usable access path without equating physical SSH with device reachability.

    Access priority:
      1. physical-host SSH
      2. direct container SSH :10022
      3. physical-host docker exec
    """
    result = {
        "device_reachable": False,
        "physical_ssh": False,
        "container_ssh": False,
        "docker_exec": False,
        "access_mode": "none",
        "physical_user": "",
        "login_method": "",
        "ssh_password": "",
        "container_password": "",
        "error": "",
    }

    physical_user, login_method = find_physical_user(host_ip)
    if physical_user:
        result.update({
            "device_reachable": True,
            "physical_ssh": True,
            "physical_user": physical_user,
            "login_method": login_method,
        })
        if login_method == "password":
            creds = _get_device_credentials(host_ip)
            result["ssh_password"] = creds.get("pm_password") or creds.get("password", "")
        else:
            result["ssh_password"] = ""

        # Prefer the direct container path when available; otherwise keep
        # physical SSH + docker exec as a valid container execution path.
        out, err, rc = ssh_exec(
            host_ip, CONTAINER_PORT, CONTAINER_USER, "echo 'OK'",
            exec_timeout=6
        )
        if rc == 0 and "OK" in out:
            result["container_ssh"] = True
            result["access_mode"] = "direct_container"
            result["container_password"] = ""
            return result

        creds = _get_device_credentials(host_ip)
        cont_pass = creds.get("password", "")
        if cont_pass:
            out, err, rc = ssh_exec(
                host_ip, CONTAINER_PORT, CONTAINER_USER, "echo 'OK'",
                exec_timeout=6, password=cont_pass
            )
            if rc == 0 and "OK" in out:
                result["container_ssh"] = True
                result["access_mode"] = "direct_container"
                result["container_password"] = cont_pass
                return result

        docker_cmd = _docker_cmd(
            physical_user,
            "docker exec dev bash -l -c 'echo OK' 2>&1"
        )
        out, err, rc = ssh_exec(
            host_ip, 22, physical_user, docker_cmd,
            exec_timeout=8, password=result["ssh_password"]
        )
        if rc == 0 and "OK" in out:
            result["docker_exec"] = True
            result["access_mode"] = "docker_exec"
            return result

        result["access_mode"] = "physical"
        result["error"] = "物理机可达，但容器未建立访问路径"
        return result

    # Physical SSH failed. This is deliberately not a device-unreachable
    # conclusion: try the container endpoint independently.
    creds = _get_device_credentials(host_ip)
    out, err, rc = ssh_exec(
        host_ip, CONTAINER_PORT, CONTAINER_USER, "echo 'OK'",
        exec_timeout=6
    )
    if rc == 0 and "OK" in out:
        result.update({
            "device_reachable": True,
            "container_ssh": True,
            "access_mode": "direct_container",
        })
        return result

    cont_pass = creds.get("password", "")
    if cont_pass:
        out, err, rc = ssh_exec(
            host_ip, CONTAINER_PORT, CONTAINER_USER, "echo 'OK'",
            exec_timeout=6, password=cont_pass
        )
        if rc == 0 and "OK" in out:
            result.update({
                "device_reachable": True,
                "container_ssh": True,
                "access_mode": "direct_container",
                "container_password": cont_pass,
            })
            return result

    ping_ok, ping_info = ping_host(host_ip)
    result["error"] = f"物理SSH失败、容器10022失败（Ping: {ping_info}）"
    return result


def find_physical_user(host_ip: str) -> tuple:
    """Find a usable physical-host SSH identity without a brittle banner gate.

    The TCP port is treated as a hint only. The SSH command itself is the
    authoritative connectivity/authentication test, because some gateways or
    SSH servers delay their banner.
    """
    # Avoid spending ~80s probing every possible account. Respect the config:
    # by default only the first/root account is tried unless a DB physical user
    # is explicitly configured.
    port_open = _quick_port_check(host_ip, 22, timeout=2.0)
    if not port_open:
        logger.warning("物理机 %s:22 TCP 探测失败，仍允许容器 10022 直连路径继续工作", host_ip)
        return "", ""

    creds = _get_device_credentials(host_ip)
    db_pm_user = creds.get("pm_username", "")
    db_pm_pass = creds.get("pm_password", "")
    db_dev_pass = creds.get("password", "")

    key_users = []
    if db_pm_user:
        key_users.append(db_pm_user)
    configured_users = [u.strip() for u in PHYSICAL_USERS if u.strip()]
    if PHYSICAL_SSH_USERS_ENABLED:
        key_users.extend(configured_users)
    elif configured_users:
        key_users.append(configured_users[0])

    seen = set()
    key_users = [u for u in key_users if not (u in seen or seen.add(u))]

    # 1) Known physical username with configured key.
    for pm_user in key_users:
        stdout, stderr, code = ssh_exec(host_ip, 22, pm_user, "echo 'OK'", exec_timeout=6)
        if code == 0 and stdout.strip() == "OK":
            logger.info("物理机用户: %s@%s (密钥)", pm_user, host_ip)
            return pm_user, "key"

    # 2) Known physical username with DB physical password, then the device
    # password as a compatibility fallback when the physical username is known.
    if db_pm_user:
        passwords = [p for p in (db_pm_pass, db_dev_pass) if p]
        seen_pw = set()
        for pwd in [p for p in passwords if not (p in seen_pw or seen_pw.add(p))]:
            stdout, stderr, code = ssh_exec(
                host_ip, 22, db_pm_user, "echo 'OK'", exec_timeout=6, password=pwd
            )
            if code == 0 and stdout.strip() == "OK":
                logger.info("物理机用户: %s@%s (数据库密码)", db_pm_user, host_ip)
                return db_pm_user, "password"

    # 3) Optional additional physical usernames with the physical password.
    if db_pm_pass and PHYSICAL_SSH_USERS_ENABLED:
        for pm_user in key_users:
            stdout, stderr, code = ssh_exec(
                host_ip, 22, pm_user, "echo 'OK'", exec_timeout=6, password=db_pm_pass
            )
            if code == 0 and stdout.strip() == "OK":
                logger.info("物理机用户: %s@%s (密码)", pm_user, host_ip)
                return pm_user, "password"

    logger.warning("物理机 %s 登录探测失败（并不代表容器不可达）", host_ip)
    return "", ""

