#!/usr/bin/env python3
import os
from contextvars import ContextVar
from pathlib import Path

SELF_AGENT_DIR = Path(__file__).parent

# Thread-local current user ID for tools that need to know who is calling
_current_user_id: ContextVar[str] = ContextVar("_current_user_id", default="")

def set_current_user_id(user_id: str):
    _current_user_id.set(user_id)

def get_current_user_id() -> str:
    return _current_user_id.get()


# 可用模型配置
AVAILABLE_MODELS = {
    "deepseek-v4-flash": {
        "label": "DeepSeek V4 Flash（火山）",
        "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
        "api_key": "a385d094-7f69-41e0-b3b8-6c773877e97b"
    },
    "glm-5.1": {
        "label": "GLM-5.1（火山）",
        "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
        "api_key": "a385d094-7f69-41e0-b3b8-6c773877e97b"
    },
    "deepseek-v4-pro": {
        "label": "deepseek-v4-pro（火山）",
        "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
        "api_key": "a385d094-7f69-41e0-b3b8-6c773877e97b"
    },
    "deepseek-v4-flash-baidu": {
        "label": "DeepSeek V4 Flash（百度云）",
        "base_url": "https://qianfan.baidubce.com/v2/coding",
        "api_key": "bce-v3/ALTAKSP-8rIUW18KeRfA0NloMkZvX/f8158fdb129ce95064be0550ec888e737416ba39"
    },
    "deepseek-v4-pro-baidu": {
        "label": "DeepSeek V4 Pro（百度云）",
        "base_url": "https://qianfan.baidubce.com/v2/coding",
        "api_key": "bce-v3/ALTAKSP-8rIUW18KeRfA0NloMkZvX/f8158fdb129ce95064be0550ec888e737416ba39"
    }
}

# API 配置
API_HOST = os.getenv("SELF_AGENT_HOST", "0.0.0.0")
API_PORT = int(os.getenv("SELF_AGENT_PORT", "8645"))
API_KEY = os.getenv("SELF_AGENT_API_KEY", "mec-diagnose-agent-2026")

# HTTPS 配置（设为空字符串或 None 则仅 HTTP）
SSL_CERT = os.getenv("SSL_CERT", "")
SSL_KEY = os.getenv("SSL_KEY", "")

#LLM_API_KEY = "8668b8cf-f301-4ee5-b5c3-b43da332643b"
#LLM_API_KEY = "a385d094-7f69-41e0-b3b8-6c773877e97b"
#LLM_BASE_URL = "https://ark.cn-beijing.volces.com/api/coding/v3"
#LLM_MODEL = "deepseek-v4-flash"

#LLM_BASE_URL= "https://qianfan.baidubce.com/v2/coding"
#LLM_API_KEY= "bce-v3/ALTAKSP-8rIUW18KeRfA0NloMkZvX/f8158fdb129ce95064be0550ec888e737416ba39"
#LLM_MODEL = "deepseek-v4-flash"

# 诊断日志目录
DIAGNOSE_DIR = SELF_AGENT_DIR / "diagnose_logs"

# KNOWN_PROJECTS（从 code_analyze.py 同步）
KNOWN_PROJECTS = ["德会", "德会隧道", "柯诸", "汉宜", "南京仙新路", "山西灵石", "汕梅", "沈海", "绵九", "贵阳", "青海"]

# WebUI 用户（用户名: 密码）
USERS = {
    "admin": "admin",
    "tyf": "tyf",
    "cy": "cy",
    "xq": "xq",
    "yjx": "yjx",
    "sy": "sy"
}

# 反馈弹窗延迟（秒），对话完成后等待 N 秒弹出反馈栏
FEEDBACK_DELAY_SECONDS = 0

FEISHU_APP_SECRET = "bIi2vPfsKlh663TWi4ZHWcV4pnMOUrrr"
FEISHU_DOMAIN = "feishu"
FEISHU_CONNECTION_MODE = "websocket"
FEISHU_ALLOW_ALL_USERS = "true"
FEISHU_ALLOWED_USERS = ""
FEISHU_GROUP_POLICY = "open"
FEISHU_GROUP_MENTION_MODE = "all"
FEISHU_ACCEPT_BOT_MESSAGES = "true"

# MySQL 数据库配置
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASS = os.getenv("MYSQL_PASS", "sy123456")
MYSQL_DB = os.getenv("MYSQL_DB", "mec_monitor")

# MongoDB 雷达交通数据配置
MONGO_HOST = os.getenv("MONGO_HOST", "172.172.5.6")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))
MONGO_DB = os.getenv("MONGO_DB", "radarData")

# 事件图片 HTTP 访问基地址（MongoDB 雷达事件图片通过此 URL 读取）
# 最终 URL 格式: {EVENT_IMAGE_BASE_URL}/{devNo大写_转-}/{ftpDir}/{ftpImg}
EVENT_IMAGE_BASE_URL = os.getenv("EVENT_IMAGE_BASE_URL", "http://172.172.5.6:7080")

# SSH 密钥路径
#SSH_KEY_PATH = os.getenv("SSH_KEY_PATH", str(SELF_AGENT_DIR / "id_ed25519"))
SSH_KEY_PATH = os.getenv("SSH_KEY_PATH", "/home/sy-cs/workspace/mec_agent_project/mec_agent/id_ed25519")

# SSH 客户端路径 — Windows 用原生 ssh.exe，Linux 用系统 ssh
#SSH_CMD_PATH = os.getenv("SSH_CMD", "/mnt/c/Windows/System32/OpenSSH/ssh.exe")
SSH_CMD_PATH = os.getenv("SSH_CMD", "ssh")

# 需要 sudo 提权的用户列表
SSH_SUDO_USERS = os.getenv("SSH_SUDO_USERS", "lcfc,nvidia").split(",")

# ROS 环境初始化命令（三段 fallback）
SSH_ROS_ENV_CMD = os.getenv(
    "SSH_ROS_ENV_CMD",
    "source /home/files/rvf/setup.bash 2>/dev/null || "
    "source /home/files/install/setup.bash 2>/dev/null || "
    "source /opt/ros/noetic/setup.bash 2>/dev/null"
)

# 容器 SSH 默认端口和用户
CONTAINER_SSH_PORT = int(os.getenv("CONTAINER_SSH_PORT", "10022"))
CONTAINER_SSH_USER = os.getenv("CONTAINER_SSH_USER", "root")

# 物理机 SSH 尝试用户列表（公钥+密码各一次，不重试）
PHYSICAL_SSH_USERS = os.getenv("PHYSICAL_SSH_USERS", "root,nvidia,lcfc,ema").split(",")
# 是否启用 PHYSICAL_SSH_USERS 中的额外用户（root 外）尝试公钥登录
# True=尝试 root→密码→额外用户公钥; False=仅尝试 root+密码
PHYSICAL_SSH_USERS_ENABLED = os.getenv("PHYSICAL_SSH_USERS_ENABLED", "false").lower() == "true"

# 事件图片获取模式: "ssh_exec" | "sftp"
# ssh_exec: SSH exec "base64 /path" 读取（兼容性好）
# sftp: paramiko SFTP 直接传输二进制（性能好）
EVENT_IMAGE_FETCH_MODE = os.getenv("EVENT_IMAGE_FETCH_MODE", "ssh_exec")

# 事件图片临时存储目录
EVENT_IMAGE_TEMP_DIR = os.getenv("EVENT_IMAGE_TEMP_DIR", "/tmp/event_images")

# 临时图片保留时间（小时）
EVENT_IMAGE_TTL_HOURS = int(os.getenv("EVENT_IMAGE_TTL_HOURS", "24"))
