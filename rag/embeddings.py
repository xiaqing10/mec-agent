"""
Embedding 模块

封装 sentence-transformers 的 Embedding 接口，提供文本向量化能力。
使用 shibing624/text2vec-base-chinese 模型，支持中文文本。
"""

import logging
from typing import List

logger = logging.getLogger(__name__)

# 全局缓存，避免重复加载模型
_embedding_model = None


def _get_embedding_model():
    """懒加载 Embedding 模型（单例模式）"""
    global _embedding_model
    if _embedding_model is None:
        from .config import RAG_EMBEDDING_MODEL
        logger.info("🔄 加载 Embedding 模型: %s ...", RAG_EMBEDDING_MODEL)
        try:
            from sentence_transformers import SentenceTransformer
            _embedding_model = SentenceTransformer(RAG_EMBEDDING_MODEL)
            logger.info("✅ Embedding 模型加载完成")
        except Exception as e:
            logger.error("❌ Embedding 模型加载失败: %s", e)
            raise
    return _embedding_model


def embed_documents(texts: List[str]) -> List[List[float]]:
    """将文本列表转换为向量列表。

    Args:
        texts: 文本列表

    Returns:
        向量列表，每个向量是一个浮点数列表
    """
    model = _get_embedding_model()
    embeddings = model.encode(texts, show_progress_bar=False)
    return embeddings.tolist()


def embed_query(text: str) -> List[float]:
    """将查询文本转换为向量。

    Args:
        text: 查询文本

    Returns:
        向量，一个浮点数列表
    """
    model = _get_embedding_model()
    embedding = model.encode([text], show_progress_bar=False)
    return embedding[0].tolist()
