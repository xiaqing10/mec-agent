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


@tool
def rag_list_knowledge(
    category: str = "all",
    limit: int = 10,
    project: str = "",
) -> str:
    """列出 RAG 知识库中的内容，查看已存储的知识数据。

    可用于：
    - 查看知识库中有哪些诊断案例
    - 查看知识库中有哪些修复记录
    - 查看知识库中有哪些运维知识文档
    - 查看知识库中有哪些用户记忆

    Args:
        category: 要查看的类别 - all(全部)/diagnosis(诊断案例)/repair(修复记录)/memory(用户记忆)/knowledge(运维知识)
        limit: 每个类别显示的记录数，默认10条
        project: 按项目过滤（仅对 diagnosis 有效），如 "德会"
    """
    from rag.config import (
        RAG_CHROMA_DIR,
        RAG_COLLECTION_DIAGNOSIS,
        RAG_COLLECTION_KNOWLEDGE,
        RAG_COLLECTION_REPAIR,
        RAG_COLLECTION_MEMORY,
    )
    import chromadb

    try:
        client = chromadb.PersistentClient(path=RAG_CHROMA_DIR)
    except Exception as e:
        return f"❌ ChromaDB 连接失败: {e}"

    lines = ["📚 **RAG 知识库内容**\n"]

    # 诊断历史
    if category in ("all", "diagnosis"):
        try:
            collection = client.get_or_create_collection(name=RAG_COLLECTION_DIAGNOSIS)
            count = collection.count()
            if count > 0:
                where = {"project": project} if project else None
                results = collection.get(limit=limit, where=where, include=["documents", "metadatas"])
                metas = results.get("metadatas", [])
                docs = results.get("documents", [])
                lines.append(f"### 诊断历史 ({count}条)")
                for i, (meta, doc) in enumerate(zip(metas, docs), 1):
                    proj = meta.get("project", "")
                    device = meta.get("device", "")
                    ip = meta.get("ip", "")
                    diag_type = meta.get("diag_type", "")
                    issue = meta.get("issue", "")
                    today_image_count = meta.get("today_image_count", -1)
                    timestamp = meta.get("timestamp", "")[:16]
                    lines.append(f"{i}. [{proj}] {device} ({ip})")
                    lines.append(f"   时间: {timestamp} | 类型: {diag_type} | 图片数: {today_image_count}")
                    lines.append(f"   问题: {issue[:100]}")
                if count > limit:
                    lines.append(f"  ... 还有 {count - limit} 条记录")
            else:
                lines.append("### 诊断历史 (0条)")
            lines.append("")
        except Exception as e:
            lines.append(f"### 诊断历史: 查询失败 - {e}\n")

    # 修复记录
    if category in ("all", "repair"):
        try:
            collection = client.get_or_create_collection(name=RAG_COLLECTION_REPAIR)
            count = collection.count()
            if count > 0:
                results = collection.get(limit=limit, include=["metadatas"])
                metas = results.get("metadatas", [])
                lines.append(f"### 修复记录 ({count}条)")
                for i, meta in enumerate(metas, 1):
                    ip = meta.get("ip", "")
                    action = meta.get("action", "")
                    success = meta.get("success", False)
                    timestamp = meta.get("timestamp", "")[:10]
                    status = "成功" if success else "失败"
                    lines.append(f"{i}. {ip} - {action}: {status}, {timestamp}")
                if count > limit:
                    lines.append(f"  ... 还有 {count - limit} 条记录")
            else:
                lines.append("### 修复记录 (0条)")
            lines.append("")
        except Exception as e:
            lines.append(f"### 修复记录: 查询失败 - {e}\n")

    # 用户记忆
    if category in ("all", "memory"):
        try:
            collection = client.get_or_create_collection(name=RAG_COLLECTION_MEMORY)
            count = collection.count()
            if count > 0:
                results = collection.get(limit=limit, include=["documents", "metadatas"])
                docs = results.get("documents", [])
                metas = results.get("metadatas", [])
                lines.append(f"### 用户记忆 ({count}条)")
                for i, (doc, meta) in enumerate(zip(docs, metas), 1):
                    fact_type = meta.get("fact_type", "")
                    user_id = meta.get("user_id", "")
                    confidence = meta.get("confidence", 0)
                    lines.append(f"{i}. [{fact_type}] {user_id}: {doc[:80]}... (置信度={confidence})")
                if count > limit:
                    lines.append(f"  ... 还有 {count - limit} 条记录")
            else:
                lines.append("### 用户记忆 (0条)")
            lines.append("")
        except Exception as e:
            lines.append(f"### 用户记忆: 查询失败 - {e}\n")

    # 运维知识
    if category in ("all", "knowledge"):
        try:
            collection = client.get_or_create_collection(name=RAG_COLLECTION_KNOWLEDGE)
            count = collection.count()
            if count > 0:
                results = collection.get(limit=limit, include=["documents", "metadatas"])
                docs = results.get("documents", [])
                metas = results.get("metadatas", [])
                lines.append(f"### 运维知识 ({count}条)")
                for i, (doc, meta) in enumerate(zip(docs, metas), 1):
                    source = meta.get("source", "")
                    chunk_index = meta.get("chunk_index", 0)
                    lines.append(f"{i}. {source} [chunk {chunk_index}]: {doc[:80]}...")
                if count > limit:
                    lines.append(f"  ... 还有 {count - limit} 条记录")
            else:
                lines.append("### 运维知识 (0条)")
            lines.append("")
        except Exception as e:
            lines.append(f"### 运维知识: 查询失败 - {e}\n")

    return "\n".join(lines)
