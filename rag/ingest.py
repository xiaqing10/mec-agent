"""
RAG 数据导入管道模块

从各数据源提取数据，分块后写入 ChromaDB 向量库。
"""

import json
import logging
import os
from pathlib import Path
from typing import List, Dict

from .config import (
    PROJECT_DIR, RAG_CHUNK_SIZE,
    RAG_COLLECTION_DIAGNOSIS, RAG_COLLECTION_KNOWLEDGE,
    RAG_COLLECTION_REPAIR, RAG_COLLECTION_MEMORY,
)
from .embeddings import embed_documents

logger = logging.getLogger(__name__)

# 项目根目录
DIAGNOSE_LOGS_DIR = PROJECT_DIR / "diagnose_logs"
REPAIR_LOGS_DIR = PROJECT_DIR / "repair_logs"
KNOWLEDGE_DIR = PROJECT_DIR / "knowledge"


def _get_chroma_client():
    """获取 ChromaDB 客户端"""
    import chromadb
    return chromadb.PersistentClient(path=str(PROJECT_DIR / "rag_data" / "chroma"))


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """将文本分块。

    Args:
        text: 原始文本
        chunk_size: 每个 chunk 的最大字符数
        overlap: chunk 之间的重叠字符数

    Returns:
        分块后的文本列表
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        start = end - overlap

    return chunks


def ingest_diagnosis_record(
    project: str,
    device_name: str,
    ip: str,
    diag_result: dict,
) -> None:
    """将诊断记录增量导入向量库（带质量过滤）。

    在诊断完成后调用，将诊断结果写入 ChromaDB。
    只导入有明确根因的诊断结果，避免错误诊断污染知识库。

    Args:
        project: 项目名
        device_name: 设备名
        ip: 设备 IP
        diag_result: 诊断结果字典
    """
    try:
        # 质量过滤：只导入有明确问题的诊断
        diagnosis = diag_result.get("diagnosis", {})
        diag_type = diag_result.get("type", "")
        issue = diagnosis.get("issue", "")
        error = diagnosis.get("error", "")
        today_image_count = diagnosis.get("today_image_count", -1)
        
        # 跳过正常设备（今天图片数 > 0 且没有错误）
        if today_image_count > 0 and not error and "恢复正常" in issue:
            logger.debug("⏭️ 跳过 RAG 入库（设备正常）: %s %s", project, device_name)
            return
        
        # 跳过没有明确问题的诊断
        if not diag_type and not issue and not error:
            logger.debug("⏭️ 跳过 RAG 入库（无明确问题）: %s %s", project, device_name)
            return

        from .config import RAG_CHROMA_DIR
        import chromadb

        client = chromadb.PersistentClient(path=RAG_CHROMA_DIR)
        collection = client.get_or_create_collection(name=RAG_COLLECTION_DIAGNOSIS)

        diagnosis_time = diag_result.get("timestamp", "")
        if not diagnosis_time:
            from datetime import datetime
            diagnosis_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 构建文档内容
        doc_content = (
            f"项目: {project}, 设备: {device_name}, IP: {ip}\n"
            f"诊断时间: {diagnosis_time}\n"
            f"诊断类型: {diag_type}\n"
            f"问题: {issue}\n"
            f"错误: {error}\n"
            f"今日图片数: {today_image_count}"
        )

        # 构建 metadata
        metadata = {
            "project": project,
            "device": device_name,
            "ip": ip,
            "diag_type": diag_type,
            "issue": issue[:200] if issue else "",
            "today_image_count": today_image_count,
            "timestamp": diagnosis_time,
        }

        # 生成唯一 ID
        doc_id = f"{project}_{device_name}_{diagnosis_time}"

        # 写入向量库
        collection.add(
            documents=[doc_content],
            metadatas=[metadata],
            ids=[doc_id],
        )

        logger.info("✅ 诊断记录已导入 RAG: %s %s (类型: %s, 问题: %s)", project, device_name, diag_type, issue[:50] if issue else "无")

    except Exception as e:
        logger.warning("⚠️ 诊断记录导入 RAG 失败: %s", e)


def ingest_all_knowledge() -> None:
    """导入所有知识数据到向量库。

    包括：
    1. 诊断历史（从 diagnose_logs/project_history/*.json）
    2. 修复日志（从 repair_logs/repair_*.jsonl）
    3. 运维知识文档（从 knowledge/*.md）
    4. 用户记忆（从 user_memory.db）
    """
    logger.info("🚀 开始导入知识数据到 RAG 向量库...")

    client = _get_chroma_client()

    # 1. 导入诊断历史
    _ingest_diagnosis_history(client)

    # 2. 导入修复日志
    _ingest_repair_logs(client)

    # 3. 导入运维知识文档
    _ingest_knowledge_docs(client)

    # 4. 导入用户记忆
    _ingest_user_memory(client)

    logger.info("✅ 知识数据导入完成")


def _ingest_diagnosis_history(client) -> None:
    """导入诊断历史数据"""
    logger.info("📥 导入诊断历史...")
    collection = client.get_or_create_collection(name=RAG_COLLECTION_DIAGNOSIS)

    history_dir = DIAGNOSE_LOGS_DIR / "project_history"
    if not history_dir.exists():
        logger.warning("⚠️ 诊断历史目录不存在: %s", history_dir)
        return

    count = 0
    for json_file in history_dir.glob("*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            project = data.get("project", json_file.stem)
            devices = data.get("devices", {})

            for device_name, device_data in devices.items():
                records = device_data.get("records", [])
                if not records:
                    continue

                # 构建文档：每个设备的诊断记录
                doc_parts = [f"项目: {project}, 设备: {device_name}"]
                for record in records[-10:]:  # 只取最近 10 条
                    ts = record.get("timestamp", "")
                    issue = record.get("issue", "")
                    error = record.get("error", "")
                    img_count = record.get("today_image_count", -1)

                    line = f"- {ts}: "
                    if error:
                        line += f"错误={error}"
                    elif issue:
                        line += f"问题={issue}"
                    else:
                        line += f"图片数={img_count}"
                    doc_parts.append(line)

                doc_content = "\n".join(doc_parts)
                metadata = {
                    "project": project,
                    "device": device_name,
                    "ip": device_data.get("ip", ""),
                }
                doc_id = f"diag_{project}_{device_name}"

                collection.upsert(
                    documents=[doc_content],
                    metadatas=[metadata],
                    ids=[doc_id],
                )
                count += 1

        except Exception as e:
            logger.warning("⚠️ 导入诊断历史失败 %s: %s", json_file.name, e)

    logger.info("  ✅ 导入 %d 条诊断历史", count)


def _ingest_repair_logs(client) -> None:
    """导入修复日志数据"""
    logger.info("📥 导入修复日志...")
    collection = client.get_or_create_collection(name=RAG_COLLECTION_REPAIR)

    if not REPAIR_LOGS_DIR.exists():
        logger.warning("⚠️ 修复日志目录不存在: %s", REPAIR_LOGS_DIR)
        return

    count = 0
    for jsonl_file in REPAIR_LOGS_DIR.glob("repair_*.jsonl"):
        try:
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    action = record.get("action", "")
                    ip = record.get("ip", "")
                    target = record.get("target", "")
                    result = record.get("result", "")
                    success = record.get("success", False)
                    timestamp = record.get("timestamp", "")

                    doc_content = (
                        f"操作: {action}, 设备: {ip}, 目标: {target}\n"
                        f"结果: {'成功' if success else '失败'}\n"
                        f"详情: {result[:200]}"
                    )
                    metadata = {
                        "ip": ip,
                        "action": action,
                        "success": success,
                        "timestamp": timestamp,
                    }
                    doc_id = f"repair_{ip}_{action}_{timestamp}"

                    collection.upsert(
                        documents=[doc_content],
                        metadatas=[metadata],
                        ids=[doc_id],
                    )
                    count += 1

        except Exception as e:
            logger.warning("⚠️ 导入修复日志失败 %s: %s", jsonl_file.name, e)

    logger.info("  ✅ 导入 %d 条修复日志", count)


def _ingest_knowledge_docs(client) -> None:
    """导入运维知识文档"""
    logger.info("📥 导入运维知识文档...")
    collection = client.get_or_create_collection(name=RAG_COLLECTION_KNOWLEDGE)

    if not KNOWLEDGE_DIR.exists():
        logger.warning("⚠️ 知识文档目录不存在: %s，跳过", KNOWLEDGE_DIR)
        return

    count = 0
    for md_file in KNOWLEDGE_DIR.glob("*.md"):
        try:
            with open(md_file, "r", encoding="utf-8") as f:
                content = f.read()

            # 分块
            from .config import RAG_CHUNK_SIZE, RAG_CHUNK_OVERLAP
            chunks = _chunk_text(content, RAG_CHUNK_SIZE, RAG_CHUNK_OVERLAP)

            for i, chunk in enumerate(chunks):
                metadata = {
                    "doc_type": "knowledge",
                    "source": md_file.name,
                    "chunk_index": i,
                }
                doc_id = f"knowledge_{md_file.stem}_{i}"

                collection.upsert(
                    documents=[chunk],
                    metadatas=[metadata],
                    ids=[doc_id],
                )
                count += 1

        except Exception as e:
            logger.warning("⚠️ 导入知识文档失败 %s: %s", md_file.name, e)

    logger.info("  ✅ 导入 %d 个知识文档块", count)


def _ingest_user_memory(client) -> None:
    """导入用户记忆数据"""
    logger.info("📥 导入用户记忆...")
    collection = client.get_or_create_collection(name=RAG_COLLECTION_MEMORY)

    memory_db = PROJECT_DIR / "user_memory.db"
    if not memory_db.exists():
        logger.warning("⚠️ 用户记忆数据库不存在: %s，跳过", memory_db)
        return

    try:
        import sqlite3
        conn = sqlite3.connect(str(memory_db))
        cursor = conn.cursor()

        # 只导入高置信度的记忆
        cursor.execute(
            "SELECT user_id, fact_type, key, value, confidence FROM user_memory WHERE confidence >= 5"
        )
        rows = cursor.fetchall()
        conn.close()

        count = 0
        for user_id, fact_type, key, value, confidence in rows:
            doc_content = f"[{fact_type}] {key}: {value}"
            metadata = {
                "user_id": user_id,
                "fact_type": fact_type,
                "confidence": confidence,
            }
            doc_id = f"memory_{user_id}_{fact_type}_{key}"

            collection.upsert(
                documents=[doc_content],
                metadatas=[metadata],
                ids=[doc_id],
            )
            count += 1

        logger.info("  ✅ 导入 %d 条用户记忆", count)

    except Exception as e:
        logger.warning("⚠️ 导入用户记忆失败: %s", e)


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """将文本分块"""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        start = end - overlap

    return chunks
