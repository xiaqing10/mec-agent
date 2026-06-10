"""
RAG 检索器模块

提供向量检索功能，支持：
- 诊断历史检索
- 运维知识检索
- 相似案例匹配
"""

import logging
from typing import List, Dict, Optional

from .config import (
    RAG_CHROMA_DIR, RAG_TOP_K,
    RAG_COLLECTION_DIAGNOSIS, RAG_COLLECTION_KNOWLEDGE,
    RAG_COLLECTION_REPAIR, RAG_COLLECTION_MEMORY,
)
from .embeddings import embed_query

logger = logging.getLogger(__name__)

# ChromaDB 客户端缓存
_chroma_client = None


def _get_chroma_client():
    """获取 ChromaDB 客户端（单例）"""
    global _chroma_client
    if _chroma_client is None:
        import chromadb
        _chroma_client = chromadb.PersistentClient(path=RAG_CHROMA_DIR)
        logger.info("✅ ChromaDB 客户端初始化完成: %s", RAG_CHROMA_DIR)
    return _chroma_client


def _get_collection(name: str):
    """获取或创建 ChromaDB 集合"""
    client = _get_chroma_client()
    return client.get_or_create_collection(name=name)


def retrieve_relevant_context(
    query: str,
    user_id: str = "",
    project: str = "",
    ip: str = "",
    top_k: int = None,
) -> str:
    """检索与当前问题相关的上下文信息。

    从诊断历史、运维知识、修复日志、用户记忆中检索最相关的内容，
    拼接为文本注入到 system prompt 中。

    Args:
        query: 用户当前的问题或消息
        user_id: 当前用户 ID（用于过滤用户记忆）
        project: 当前项目名（用于过滤诊断历史）
        ip: 当前设备 IP（用于过滤诊断历史）
        top_k: 返回结果数量，默认使用配置值

    Returns:
        检索结果文本，如果没有相关内容则返回空字符串
    """
    if not query or len(query.strip()) < 2:
        return ""

    if top_k is None:
        top_k = RAG_TOP_K

    results = []

    # 1. 检索诊断历史
    try:
        diag_results = _search_diagnosis(query, project, ip, top_k=min(top_k, 3))
        if diag_results:
            results.append("### 历史诊断案例\n")
            for r in diag_results:
                results.append(f"- **{r.get('device', '')}** ({r.get('project', '')}): {r.get('issue', '无描述')}")
                if r.get('solution'):
                    results.append(f"  解决方案: {r['solution']}")
            results.append("")
    except Exception as e:
        logger.debug("诊断历史检索失败: %s", e)

    # 2. 检索运维知识
    try:
        knowledge_results = _search_knowledge(query, top_k=min(top_k, 3))
        if knowledge_results:
            results.append("### 运维知识参考\n")
            for r in knowledge_results:
                results.append(f"- {r.get('content', '')[:200]}")
            results.append("")
    except Exception as e:
        logger.debug("运维知识检索失败: %s", e)

    # 3. 检索修复日志
    try:
        repair_results = _search_repair(query, ip, top_k=min(top_k, 2))
        if repair_results:
            results.append("### 相关修复记录\n")
            for r in repair_results:
                results.append(f"- {r.get('action', '')} ({r.get('device', '')}): {'成功' if r.get('success') else '失败'}")
            results.append("")
    except Exception as e:
        logger.debug("修复日志检索失败: %s", e)

    # 4. 检索用户记忆
    if user_id:
        try:
            memory_results = _search_memory(query, user_id, top_k=min(top_k, 2))
            if memory_results:
                results.append("### 用户相关记忆\n")
                for r in memory_results:
                    results.append(f"- [{r.get('fact_type', '')}] {r.get('key', '')}: {r.get('value', '')}")
                results.append("")
        except Exception as e:
            logger.debug("用户记忆检索失败: %s", e)

    return "\n".join(results) if results else ""


def find_similar_cases(
    diag_type: str,
    issue: str,
    top_k: int = 3,
) -> List[Dict]:
    """查找与当前诊断相似的历史案例。

    Args:
        diag_type: 诊断类型
        issue: 问题描述
        top_k: 返回结果数量

    Returns:
        相似案例列表
    """
    query = f"{diag_type} {issue}"
    return _search_diagnosis(query, top_k=top_k)


def _search_diagnosis(query: str, project: str = "", ip: str = "", top_k: int = 3) -> List[Dict]:
    """检索诊断历史"""
    try:
        collection = _get_collection(RAG_COLLECTION_DIAGNOSIS)
        # 构建 where 条件
        where = {}
        if project:
            where["project"] = project
        if ip:
            where["ip"] = ip

        query_embedding = embed_query(query)
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where if where else None,
            include=["documents", "metadatas"],
        )

        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]

        return [
            {"content": doc, **meta}
            for doc, meta in zip(docs, metas)
        ]
    except Exception as e:
        logger.debug("诊断历史检索异常: %s", e)
        return []


def _search_knowledge(query: str, top_k: int = 3) -> List[Dict]:
    """检索运维知识"""
    try:
        collection = _get_collection(RAG_COLLECTION_KNOWLEDGE)
        query_embedding = embed_query(query)
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["documents", "metadatas"],
        )

        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]

        return [
            {"content": doc, **meta}
            for doc, meta in zip(docs, metas)
        ]
    except Exception as e:
        logger.debug("运维知识检索异常: %s", e)
        return []


def _search_repair(query: str, ip: str = "", top_k: int = 2) -> List[Dict]:
    """检索修复日志"""
    try:
        collection = _get_collection(RAG_COLLECTION_REPAIR)
        where = {}
        if ip:
            where["ip"] = ip

        query_embedding = embed_query(query)
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where if where else None,
            include=["documents", "metadatas"],
        )

        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]

        return [
            {"content": doc, **meta}
            for doc, meta in zip(docs, metas)
        ]
    except Exception as e:
        logger.debug("修复日志检索异常: %s", e)
        return []


def _search_memory(query: str, user_id: str, top_k: int = 2) -> List[Dict]:
    """检索用户记忆"""
    try:
        collection = _get_collection(RAG_COLLECTION_MEMORY)
        query_embedding = embed_query(query)
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where={"user_id": user_id},
            include=["documents", "metadatas"],
        )

        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]

        return [
            {"content": doc, **meta}
            for doc, meta in zip(docs, metas)
        ]
    except Exception as e:
        logger.debug("用户记忆检索异常: %s", e)
        return []
