"""
RAG 知识查询工具

提供 LLM 主动查询知识库的能力。
"""

import json
import logging
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def rag_search_knowledge(
    query: str,
    category: str = "all",
    top_k: int = 5,
) -> str:
    """搜索运维知识库，查找与问题相关的历史案例和解决方案。

    可用于：
    - 查找类似故障的历史诊断记录
    - 查找设备维修的成功案例
    - 查找运维操作的最佳实践

    Args:
        query: 搜索问题描述，如 "K7+197 图片为0"
        category: 搜索范围 - all(全部)/diagnosis(诊断案例)/repair(修复记录)/memory(用户记忆)
        top_k: 返回结果数量，默认5条
    """
    from rag.retriever import (
        _search_diagnosis,
        _search_repair,
        _search_memory,
        _search_knowledge,
    )

    results = []

    try:
        if category in ("all", "diagnosis"):
            diag_results = _search_diagnosis(query, top_k=min(top_k, 3))
            if diag_results:
                results.append({"category": "诊断案例", "items": diag_results})

        if category in ("all", "repair"):
            repair_results = _search_repair(query, top_k=min(top_k, 3))
            if repair_results:
                results.append({"category": "修复记录", "items": repair_results})

        if category in ("all", "memory"):
            memory_results = _search_memory(query, user_id="", top_k=min(top_k, 3))
            if memory_results:
                results.append({"category": "用户记忆", "items": memory_results})

        if category in ("all", "knowledge"):
            knowledge_results = _search_knowledge(query, top_k=min(top_k, 3))
            if knowledge_results:
                results.append({"category": "运维知识", "items": knowledge_results})

    except Exception as e:
        logger.warning("RAG 搜索失败: %s", e)
        return json.dumps({"error": f"知识库搜索失败: {e}"}, ensure_ascii=False)

    if not results:
        return "未找到相关知识。"

    # 格式化输出
    lines = ["📚 **知识库搜索结果**\n"]
    for group in results:
        lines.append(f"### {group['category']}")
        for i, item in enumerate(group["items"], 1):
            content = item.get("content", "")[:300]
            lines.append(f"{i}. {content}")
        lines.append("")

    return "\n".join(lines)
