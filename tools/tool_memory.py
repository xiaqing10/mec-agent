from langchain_core.tools import tool

MEMORY_TARGETS = {
    "memory": "fact",
    "user": "preference",
}

@tool
def memory(action: str, target: str = "memory", key: str = "", value: str = "") -> str:
    """管理记忆系统。你可以自主保存、替换、删除和查看记忆。
    action: add（新增）/ replace（替换）/ remove（删除）/ list（查看）
    target: memory（环境/项目事实）/ user（用户偏好）
    key: 关键词，list时忽略，remove/replace时用于匹配已有条目
    value: 记忆内容，list/remove时忽略
    """
    from user_memory_store import (
        get_user_memories, upsert_memory, delete_memory,
        get_memory_char_usage,
    )
    from config import get_current_user_id

    user_id = get_current_user_id()
    if not user_id:
        return "错误：无法获取当前用户"

    fact_type = MEMORY_TARGETS.get(target, "fact")

    if action == "add":
        if not key or not value:
            return "错误：add 操作需要 key 和 value 参数"
        current, limit = get_memory_char_usage(user_id, fact_type)
        if current + len(value) > limit:
            existing = get_user_memories(user_id, fact_type)
            lines = [f"- [{m['key']}] {m['value']} ({len(m['value'])}字)" for m in existing]
            usage = f"当前记忆已使用 {current}/{limit} 字，添加此条 ({len(value)}字) 将超限。请先 replace 合并或 remove 删除一些条目。\n现有条目：\n" + "\n".join(lines)
            return usage
        upsert_memory(user_id, fact_type, key, value, source="agent", confidence=8)
        return f"已保存：{key}: {value}"

    elif action == "replace":
        if not key or not value:
            return "错误：replace 操作需要 key 和 value 参数"
        existing = get_user_memories(user_id, fact_type)
        matches = [m for m in existing if key in m["key"] or key in m["value"]]
        if not matches:
            return f"未找到包含「{key}」的记忆条目"
        for m in matches:
            upsert_memory(user_id, fact_type, m["key"], value, source="agent", confidence=8)
        return f"已替换 {len(matches)} 条记忆为：{value}"

    elif action == "remove":
        if not key:
            return "错误：remove 操作需要 key 参数"
        existing = get_user_memories(user_id, fact_type)
        matches = [m for m in existing if key in m["key"] or key in m["value"]]
        if not matches:
            return f"未找到包含「{key}」的记忆条目"
        for m in matches:
            delete_memory(m["id"], user_id)
        return f"已删除 {len(matches)} 条包含「{key}」的记忆"

    elif action == "list":
        existing = get_user_memories(user_id, fact_type)
        if not existing:
            return f"[{target.upper()}] 暂无记忆"
        current, limit = get_memory_char_usage(user_id, fact_type)
        lines = [f"- {m['key']}: {m['value']}" for m in existing]
        header = f"[{target.upper()}] {current}/{limit} 字 ({int(current/limit*100)}%)"
        return header + "\n" + "\n".join(lines)

    return f"未知 action: {action}，支持 add/replace/remove/list"