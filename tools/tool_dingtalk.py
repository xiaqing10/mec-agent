import json

from langchain_core.tools import tool


@tool
def push_to_dingtalk(title: str, message: str) -> str:
    """推送消息到钉钉群。使用钉钉机器人Webhook，支持HMAC-SHA256签名认证。

    Args:
        title: 消息标题
        message: 消息内容
    """
    from dingtalk_send import send_dingtalk

    if not message:
        return json.dumps({"error": "消息内容为空"}, ensure_ascii=False)

    if not title:
        title = "智慧交通垂域智能体消息"

    resp = send_dingtalk(title, message)
    return json.dumps({"success": True, "dingtalk_response": resp}, ensure_ascii=False)