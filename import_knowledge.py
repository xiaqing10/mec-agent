#!/usr/bin/env python3
"""
知识数据导入脚本

将以下数据导入到 RAG 向量库：
1. 诊断历史（diagnose_logs/project_history/*.json）
2. 修复日志（repair_logs/repair_*.jsonl）
3. 运维知识文档（knowledge/*.md）
4. 用户记忆（user_memory.db）

用法：
    python import_knowledge.py
"""

import sys
import os
import json
import sqlite3
import logging
from pathlib import Path

# 设置项目路径
PROJECT_DIR = Path(__file__).parent
sys.path.insert(0, str(PROJECT_DIR))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
)
logger = logging.getLogger(__name__)

# RAG 配置
RAG_CHROMA_DIR = str(PROJECT_DIR / "rag_data" / "chroma")
DIAGNOSE_LOGS_DIR = PROJECT_DIR / "diagnose_logs"
REPAIR_LOGS_DIR = PROJECT_DIR / "repair_logs"
KNOWLEDGE_DIR = PROJECT_DIR / "knowledge"

# 集合名称
COLLECTION_DIAGNOSIS = "diagnosis_history"
COLLECTION_KNOWLEDGE = "knowledge_base"
COLLECTION_REPAIR = "repair_logs"
COLLECTION_MEMORY = "user_memory"


def get_chroma_client():
    """获取 ChromaDB 客户端"""
    import chromadb
    return chromadb.PersistentClient(path=RAG_CHROMA_DIR)


def chunk_text(text, chunk_size=500, overlap=50):
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


def ingest_diagnosis_history(client):
    """导入诊断历史数据"""
    logger.info("📥 导入诊断历史...")
    collection = client.get_or_create_collection(name=COLLECTION_DIAGNOSIS)

    history_dir = DIAGNOSE_LOGS_DIR / "project_history"
    if not history_dir.exists():
        logger.warning("⚠️ 诊断历史目录不存在: %s", history_dir)
        return 0

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

                doc_parts = [f"项目: {project}, 设备: {device_name}"]
                for record in records[-10:]:
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
                metadata = {"project": project, "device": device_name, "ip": device_data.get("ip", "")}
                doc_id = f"diag_{project}_{device_name}"

                collection.upsert(documents=[doc_content], metadatas=[metadata], ids=[doc_id])
                count += 1

        except Exception as e:
            logger.warning("⚠️ 导入诊断历史失败 %s: %s", json_file.name, e)

    logger.info("  ✅ 导入 %d 条诊断历史", count)
    return count


def ingest_repair_logs(client):
    """导入修复日志数据"""
    logger.info("📥 导入修复日志...")
    collection = client.get_or_create_collection(name=COLLECTION_REPAIR)

    if not REPAIR_LOGS_DIR.exists():
        logger.warning("⚠️ 修复日志目录不存在: %s", REPAIR_LOGS_DIR)
        return 0

    count = 0
    for jsonl_file in REPAIR_LOGS_DIR.glob("repair_*.jsonl"):
        try:
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
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

                    doc_content = f"操作: {action}, 设备: {ip}, 目标: {target}\n结果: {'成功' if success else '失败'}\n详情: {result[:200]}"
                    metadata = {"ip": ip, "action": action, "success": success, "timestamp": timestamp}
                    doc_id = f"repair_{ip}_{action}_{timestamp}"

                    collection.upsert(documents=[doc_content], metadatas=[metadata], ids=[doc_id])
                    count += 1

        except Exception as e:
            logger.warning("⚠️ 导入修复日志失败 %s: %s", jsonl_file.name, e)

    logger.info("  ✅ 导入 %d 条修复日志", count)
    return count


def ingest_knowledge_docs(client):
    """导入运维知识文档"""
    logger.info("📥 导入运维知识文档...")
    collection = client.get_or_create_collection(name=COLLECTION_KNOWLEDGE)

    if not KNOWLEDGE_DIR.exists():
        logger.warning("⚠️ 知识文档目录不存在: %s，跳过", KNOWLEDGE_DIR)
        return 0

    count = 0
    for md_file in KNOWLEDGE_DIR.glob("*.md"):
        try:
            with open(md_file, "r", encoding="utf-8") as f:
                content = f.read()

            chunks = chunk_text(content, 500, 50)
            for i, chunk in enumerate(chunks):
                metadata = {"doc_type": "knowledge", "source": md_file.name, "chunk_index": i}
                doc_id = f"knowledge_{md_file.stem}_{i}"
                collection.upsert(documents=[chunk], metadatas=[metadata], ids=[doc_id])
                count += 1

        except Exception as e:
            logger.warning("⚠️ 导入知识文档失败 %s: %s", md_file.name, e)

    logger.info("  ✅ 导入 %d 个知识文档块", count)
    return count


def ingest_user_memory(client):
    """导入用户记忆数据"""
    logger.info("📥 导入用户记忆...")
    collection = client.get_or_create_collection(name=COLLECTION_MEMORY)

    memory_db = PROJECT_DIR / "user_memory.db"
    if not memory_db.exists():
        logger.warning("⚠️ 用户记忆数据库不存在: %s，跳过", memory_db)
        return 0

    try:
        conn = sqlite3.connect(str(memory_db))
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, fact_type, key, value, confidence FROM user_memory WHERE confidence >= 5")
        rows = cursor.fetchall()
        conn.close()

        count = 0
        for user_id, fact_type, key, value, confidence in rows:
            doc_content = f"[{fact_type}] {key}: {value}"
            metadata = {"user_id": user_id, "fact_type": fact_type, "confidence": confidence}
            doc_id = f"memory_{user_id}_{fact_type}_{key}"
            collection.upsert(documents=[doc_content], metadatas=[metadata], ids=[doc_id])
            count += 1

        logger.info("  ✅ 导入 %d 条用户记忆", count)
        return count

    except Exception as e:
        logger.warning("⚠️ 导入用户记忆失败: %s", e)
        return 0


def main():
    print("=" * 50)
    print("🚀 RAG 知识数据导入脚本")
    print("=" * 50)

    # 检查 ChromaDB 目录
    os.makedirs(RAG_CHROMA_DIR, exist_ok=True)

    # 获取 ChromaDB 客户端
    client = get_chroma_client()
    print(f"✅ ChromaDB 客户端初始化完成: {RAG_CHROMA_DIR}\n")

    # 导入数据
    total = 0
    total += ingest_diagnosis_history(client)
    total += ingest_repair_logs(client)
    total += ingest_knowledge_docs(client)
    total += ingest_user_memory(client)

    print("\n" + "=" * 50)
    print(f"✅ 导入完成！共导入 {total} 条知识数据")
    print("=" * 50)


if __name__ == "__main__":
    main()
