"""
RAG 配置模块

配置向量数据库路径、Embedding 模型、检索参数等。
"""

import os
from pathlib import Path

# 项目根目录
PROJECT_DIR = Path(__file__).parent.parent

# ChromaDB 持久化路径
RAG_CHROMA_DIR = os.getenv(
    "RAG_CHROMA_DIR",
    str(PROJECT_DIR / "rag_data" / "chroma")
)

# Embedding 模型路径（优先使用 HuggingFace 缓存）
RAG_EMBEDDING_MODEL = os.getenv(
    "RAG_EMBEDDING_MODEL",
    str(Path.home() / ".cache" / "huggingface" / "hub" / "models--shibing624--text2vec-base-chinese" / "snapshots" / "183bb99aa7af74355fb58d16edf8c13ae7c5433e")
)

# 文本分块参数
RAG_CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "500"))  # 每个 chunk 的最大字符数
RAG_CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "50"))  # chunk 之间的重叠字符数

# 检索参数
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "5"))  # 默认返回最相似的 5 个结果

# RAG 功能开关（设为 false 可关闭 RAG，回退到纯 LLM 模式）
RAG_ENABLED = os.getenv("RAG_ENABLED", "true").lower() == "true"

# 向量库集合名称
RAG_COLLECTION_DIAGNOSIS = "diagnosis_history"  # 诊断历史
RAG_COLLECTION_KNOWLEDGE = "knowledge_base"     # 运维知识
RAG_COLLECTION_REPAIR = "repair_logs"           # 修复日志
RAG_COLLECTION_MEMORY = "user_memory"           # 用户记忆
