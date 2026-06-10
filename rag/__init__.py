"""
RAG (Retrieval-Augmented Generation) 模块

提供基于向量检索的知识增强能力，包括：
- 诊断历史检索
- 运维知识检索
- 用户记忆检索
- 相似案例匹配

依赖：
- chromadb: 本地向量数据库
- sentence-transformers: 本地 Embedding 模型
"""

from .retriever import retrieve_relevant_context, find_similar_cases
from .ingest import ingest_diagnosis_record, ingest_all_knowledge

__all__ = [
    "retrieve_relevant_context",
    "find_similar_cases",
    "ingest_diagnosis_record",
    "ingest_all_knowledge",
]
